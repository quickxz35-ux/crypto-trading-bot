import math
import requests
from cachetools import TTLCache
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, RedirectResponse

app = FastAPI(title="Scalping Screener")

TIMEFRAME_OPTIONS = ["1d", "4h", "1h", "30m", "15m", "5m"]
favorites = []

market_cache = TTLCache(maxsize=10, ttl=900)      # 15 min
scan_cache = TTLCache(maxsize=20, ttl=45)         # 45 sec
detail_cache = TTLCache(maxsize=10000, ttl=90)    # 90 sec


# -------------------------------------------------
# HELPERS
# -------------------------------------------------

def safe_get_json(url, params=None, timeout=15):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, params=params, headers=headers, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        print("GET FAILED:", url, r.status_code)
    except Exception as e:
        print("GET ERROR:", url, str(e))
    return None


def parse_float(value, default=0.0):
    try:
        if value in (None, "", "null"):
            return default
        return float(value)
    except Exception:
        return default


def pct_change(current, previous):
    current = parse_float(current, 0)
    previous = parse_float(previous, 0)
    if previous <= 0:
        return None
    return ((current - previous) / previous) * 100.0


def avg(values):
    vals = [parse_float(v, None) for v in values if v is not None]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def sum_safe(values):
    vals = [parse_float(v, 0) for v in values if v is not None]
    return sum(vals)


def format_num(value, decimals=2, suffix=""):
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.{decimals}f}{suffix}"
    except Exception:
        return "N/A"


def normalize_coin(symbol):
    s = symbol.upper().replace("-", "").replace("_", "")
    s = s.replace("SWAP", "")
    for quote in ["USDT", "USDC", "USD"]:
        if s.endswith(quote):
            return s[: -len(quote)]
    return s


def clamp(value, min_value, max_value):
    return max(min_value, min(value, max_value))


def build_select(name, selected):
    html = ""
    for tf in TIMEFRAME_OPTIONS:
        sel = "selected" if tf == selected else ""
        html += f'<option value="{tf}" {sel}>{tf}</option>'
    return f'<select name="{name}" style="padding:8px 10px;border-radius:8px;background:#0f172a;color:#fff;border:1px solid #334155;">{html}</select>'


def metric_label(value, decimals=2, suffix=""):
    return f'<div class="mini-num">{format_num(value, decimals, suffix)}</div>'


def centered_bar(value, max_abs=8, width=120, height=10, decimals=2, suffix=""):
    if value is None:
        return '<span class="muted">N/A</span>'
    v = clamp(float(value), -max_abs, max_abs)
    half = width / 2
    fill = (abs(v) / max_abs) * half
    color = "#22c55e" if v > 0 else "#ef4444" if v < 0 else "#64748b"
    left = half if v >= 0 else half - fill
    return f"""
    <div class="metric-wrap" title="{format_num(value, decimals, suffix)}">
        <div class="bar centered" style="width:{width}px;height:{height}px;">
            <div class="bar-mid"></div>
            <div class="bar-fill" style="left:{left}px;width:{fill}px;background:{color};"></div>
        </div>
        {metric_label(value, decimals, suffix)}
    </div>
    """


def fill_bar(value, max_value=100, width=120, height=10, color="#38bdf8", decimals=2, suffix=""):
    if value is None:
        return '<span class="muted">N/A</span>'
    v = clamp(float(value), 0, max_value)
    fill = (v / max_value) * width
    return f"""
    <div class="metric-wrap" title="{format_num(value, decimals, suffix)}">
        <div class="bar" style="width:{width}px;height:{height}px;">
            <div class="bar-fill" style="left:0;width:{fill}px;background:{color};"></div>
        </div>
        {metric_label(value, decimals, suffix)}
    </div>
    """


def ratio_bar(value, center=1.0, max_dev=0.75, width=120, height=10, decimals=3):
    if value is None:
        return '<span class="muted">N/A</span>'
    v = float(value)
    dev = clamp(v - center, -max_dev, max_dev)
    half = width / 2
    fill = (abs(dev) / max_dev) * half if max_dev > 0 else 0
    color = "#22c55e" if dev > 0 else "#ef4444" if dev < 0 else "#64748b"
    left = half if dev >= 0 else half - fill
    return f"""
    <div class="metric-wrap" title="{format_num(value, decimals)}">
        <div class="bar centered" style="width:{width}px;height:{height}px;">
            <div class="bar-mid"></div>
            <div class="bar-fill" style="left:{left}px;width:{fill}px;background:{color};"></div>
        </div>
        {metric_label(value, decimals)}
    </div>
    """


def score_bar(value, max_score=100, width=120, height=10):
    if value is None:
        return '<span class="muted">N/A</span>'
    v = clamp(float(value), 0, max_score)
    color = "#22c55e" if v >= 70 else "#eab308" if v >= 40 else "#f97316"
    return fill_bar(v, max_value=max_score, width=width, height=height, color=color, decimals=1)


def bias_badge(bias):
    color = "#22c55e" if bias == "Bullish" else "#ef4444" if bias == "Bearish" else "#eab308"
    return f'<span style="color:{color};font-weight:700;">{bias}</span>'


def base_layout(title, body):
    return f"""
    <html>
    <head>
        <title>{title}</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                background:#0f172a;
                color:white;
                padding:20px;
                margin:0;
            }}
            h1 {{
                color:#38bdf8;
                margin-bottom:12px;
            }}
            .nav {{
                margin-bottom:20px;
            }}
            .nav a {{
                margin-right:20px;
                color:#38bdf8;
                text-decoration:none;
                font-weight:bold;
            }}
            table {{
                width:100%;
                border-collapse:collapse;
                background:#1e293b;
                font-size:14px;
                border:1px solid #334155;
            }}
            th, td {{
                padding:10px;
                border:1px solid #334155;
                text-align:left;
                vertical-align:middle;
            }}
            th {{
                background:#334155;
                position:sticky;
                top:0;
            }}
            tr:hover {{
                background:#273549;
            }}
            .controls {{
                margin:16px 0;
            }}
            button {{
                padding:8px 14px;
                border-radius:8px;
                border:1px solid #475569;
                background:#1e293b;
                color:white;
                cursor:pointer;
            }}
            input, select {{
                margin-right:10px;
            }}
            .card {{
                background:#1e293b;
                padding:20px;
                border:1px solid #334155;
                margin:16px 0;
                border-radius:12px;
            }}
            .muted {{
                color:#94a3b8;
            }}
            .metric-wrap {{
                display:flex;
                flex-direction:column;
                gap:4px;
                min-width:124px;
            }}
            .mini-num {{
                color:#94a3b8;
                font-size:11px;
                line-height:1;
            }}
            .bar {{
                position:relative;
                background:#0f172a;
                border:1px solid #334155;
                border-radius:999px;
                overflow:hidden;
            }}
            .bar.centered {{
                background:linear-gradient(to right, rgba(239,68,68,0.10) 0%, rgba(15,23,42,1) 50%, rgba(34,197,94,0.10) 100%);
            }}
            .bar-mid {{
                position:absolute;
                left:50%;
                top:0;
                width:1px;
                height:100%;
                background:#64748b;
                z-index:2;
            }}
            .bar-fill {{
                position:absolute;
                top:0;
                height:100%;
                border-radius:999px;
                z-index:1;
            }}
            .grid {{
                display:grid;
                grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
                gap:14px;
            }}
            .metric-card {{
                background:#0f172a;
                border:1px solid #334155;
                border-radius:12px;
                padding:14px;
            }}
            .metric-title {{
                color:#cbd5e1;
                font-size:13px;
                margin-bottom:8px;
                font-weight:700;
            }}
            .table-wrap {{
                overflow-x:auto;
                border-radius:12px;
            }}
            a {{
                text-decoration:none;
            }}
        </style>
    </head>
    <body>
        <div class="nav">
            <a href="/dashboard">📊 Screener</a>
            <a href="/favorites">⭐ Favorites</a>
        </div>
        {body}
    </body>
    </html>
    """


# -------------------------------------------------
# TIMEFRAME HELPERS
# -------------------------------------------------

def timeframe_to_binance(tf):
    return tf


def timeframe_to_okx(tf):
    mapping = {
        "1d": "1Dutc",
        "4h": "4H",
        "1h": "1H",
        "30m": "30m",
        "15m": "15m",
        "5m": "5m",
    }
    return mapping[tf]


def timeframe_to_bybit(tf):
    mapping = {
        "1d": "D",
        "4h": "240",
        "1h": "60",
        "30m": "30",
        "15m": "15",
        "5m": "5",
    }
    return mapping[tf]


def shortlist_atr_floor(tf):
    return {
        "5m": 0.10,
        "15m": 0.20,
        "30m": 0.28,
        "1h": 0.40,
        "4h": 0.80,
        "1d": 1.50,
    }.get(tf, 0.20)


def deep_thresholds(tf):
    return {
        "5m": {"min_score": 18, "move_cap": 2.5, "vol_cap": 120, "oi_cap": 8, "atr_cap": 2.0},
        "15m": {"min_score": 20, "move_cap": 3.0, "vol_cap": 150, "oi_cap": 10, "atr_cap": 2.5},
        "30m": {"min_score": 22, "move_cap": 3.5, "vol_cap": 180, "oi_cap": 12, "atr_cap": 3.0},
        "1h": {"min_score": 24, "move_cap": 4.0, "vol_cap": 220, "oi_cap": 15, "atr_cap": 4.0},
        "4h": {"min_score": 26, "move_cap": 6.0, "vol_cap": 260, "oi_cap": 18, "atr_cap": 6.0},
        "1d": {"min_score": 30, "move_cap": 10.0, "vol_cap": 300, "oi_cap": 20, "atr_cap": 10.0},
    }[tf]


# -------------------------------------------------
# BINANCE
# -------------------------------------------------

def get_binance_markets():
    cache_key = "binance_markets"
    if cache_key in market_cache:
        return market_cache[cache_key]

    data = safe_get_json("https://fapi.binance.com/fapi/v1/exchangeInfo")
    markets = {}
    if isinstance(data, dict):
        for item in data.get("symbols", []):
            if (
                item.get("contractType") == "PERPETUAL"
                and item.get("quoteAsset") == "USDT"
                and item.get("status") == "TRADING"
            ):
                coin = normalize_coin(item.get("symbol", ""))
                markets[coin] = {
                    "symbol": item.get("symbol"),
                    "exchange": "Binance",
                }

    market_cache[cache_key] = markets
    return markets


def get_binance_tickers():
    data = safe_get_json("https://fapi.binance.com/fapi/v1/ticker/24hr")
    result = {}
    if isinstance(data, list):
        for item in data:
            symbol = item.get("symbol", "")
            if symbol.endswith("USDT"):
                coin = normalize_coin(symbol)
                result[coin] = {
                    "symbol": symbol,
                    "price_change_pct": parse_float(item.get("priceChangePercent")),
                    "price": parse_float(item.get("lastPrice")),
                    "volume_24h": parse_float(item.get("quoteVolume")),
                }
    return result


def get_binance_funding(symbol):
    data = safe_get_json("https://fapi.binance.com/fapi/v1/premiumIndex", params={"symbol": symbol})
    if isinstance(data, dict):
        return parse_float(data.get("lastFundingRate"), None)
    return None


def get_binance_oi_change(symbol, tf):
    data = safe_get_json(
        "https://fapi.binance.com/futures/data/openInterestHist",
        params={"symbol": symbol, "period": tf, "limit": 2},
    )
    if isinstance(data, list) and len(data) >= 2:
        prev_oi = parse_float(data[-2].get("sumOpenInterest"))
        last_oi = parse_float(data[-1].get("sumOpenInterest"))
        return pct_change(last_oi, prev_oi), last_oi
    return None, None


def get_binance_long_short(symbol, tf):
    data = safe_get_json(
        "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
        params={"symbol": symbol, "period": tf, "limit": 1},
    )
    if isinstance(data, list) and data:
        return parse_float(data[-1].get("longShortRatio"), None)
    return None


def get_binance_candles(symbol, tf, limit=6):
    data = safe_get_json(
        "https://fapi.binance.com/fapi/v1/klines",
        params={"symbol": symbol, "interval": timeframe_to_binance(tf), "limit": limit},
    )
    if isinstance(data, list):
        return data
    return []


# -------------------------------------------------
# OKX
# -------------------------------------------------

def get_okx_markets():
    cache_key = "okx_markets"
    if cache_key in market_cache:
        return market_cache[cache_key]

    data = safe_get_json("https://www.okx.com/api/v5/public/instruments", params={"instType": "SWAP"})
    markets = {}
    if isinstance(data, dict):
        for item in data.get("data", []):
            if item.get("settleCcy") == "USDT" and item.get("state") == "live":
                coin = normalize_coin(item.get("instId", ""))
                markets[coin] = {
                    "symbol": item.get("instId"),
                    "exchange": "OKX",
                }

    market_cache[cache_key] = markets
    return markets


def get_okx_tickers():
    data = safe_get_json("https://www.okx.com/api/v5/market/tickers", params={"instType": "SWAP"})
    result = {}
    if isinstance(data, dict):
        for item in data.get("data", []):
            inst_id = item.get("instId", "")
            if inst_id.endswith("-USDT-SWAP"):
                coin = normalize_coin(inst_id)
                result[coin] = {
                    "symbol": inst_id,
                    "price_change_pct": None,
                    "price": parse_float(item.get("last")),
                    "volume_24h": parse_float(item.get("volCcy24h")),
                }
    return result


def get_okx_funding(symbol):
    data = safe_get_json("https://www.okx.com/api/v5/public/funding-rate", params={"instId": symbol})
    if isinstance(data, dict) and data.get("data"):
        return parse_float(data["data"][0].get("fundingRate"), None)
    return None


def get_okx_open_interest(symbol):
    data = safe_get_json(
        "https://www.okx.com/api/v5/public/open-interest",
        params={"instType": "SWAP", "instId": symbol},
    )
    if isinstance(data, dict) and data.get("data"):
        return parse_float(data["data"][0].get("oi"), None)
    return None


def get_okx_candles(symbol, tf, limit=6):
    data = safe_get_json(
        "https://www.okx.com/api/v5/market/history-candles",
        params={"instId": symbol, "bar": timeframe_to_okx(tf), "limit": limit},
    )
    if isinstance(data, dict):
        return data.get("data", [])
    return []


# -------------------------------------------------
# BYBIT
# -------------------------------------------------

def get_bybit_markets():
    cache_key = "bybit_markets"
    if cache_key in market_cache:
        return market_cache[cache_key]

    data = safe_get_json(
        "https://api.bybit.com/v5/market/instruments-info",
        params={"category": "linear", "limit": 1000},
    )
    markets = {}
    if isinstance(data, dict):
        items = ((data.get("result") or {}).get("list")) or []
        for item in items:
            if item.get("quoteCoin") == "USDT" and item.get("status") == "Trading":
                coin = normalize_coin(item.get("symbol", ""))
                markets[coin] = {
                    "symbol": item.get("symbol"),
                    "exchange": "Bybit",
                }

    market_cache[cache_key] = markets
    return markets


def get_bybit_tickers():
    data = safe_get_json("https://api.bybit.com/v5/market/tickers", params={"category": "linear"})
    result = {}
    if isinstance(data, dict):
        items = ((data.get("result") or {}).get("list")) or []
        for item in items:
            symbol = item.get("symbol", "")
            if symbol.endswith("USDT"):
                coin = normalize_coin(symbol)
                result[coin] = {
                    "symbol": symbol,
                    "price_change_pct": parse_float(item.get("price24hPcnt")) * 100.0,
                    "price": parse_float(item.get("lastPrice")),
                    "volume_24h": parse_float(item.get("turnover24h")),
                }
    return result


def get_bybit_funding(symbol):
    data = safe_get_json(
        "https://api.bybit.com/v5/market/funding/history",
        params={"category": "linear", "symbol": symbol, "limit": 1},
    )
    if isinstance(data, dict):
        items = ((data.get("result") or {}).get("list")) or []
        if items:
            return parse_float(items[0].get("fundingRate"), None)
    return None


def get_bybit_oi_change(symbol, tf):
    interval_map = {
        "1d": "1d",
        "4h": "4h",
        "1h": "1h",
        "30m": "30min",
        "15m": "15min",
        "5m": "5min",
    }
    data = safe_get_json(
        "https://api.bybit.com/v5/market/open-interest",
        params={
            "category": "linear",
            "symbol": symbol,
            "intervalTime": interval_map[tf],
            "limit": 2,
        },
    )
    if isinstance(data, dict):
        items = ((data.get("result") or {}).get("list")) or []
        if len(items) >= 2:
            prev_oi = parse_float(items[-1].get("openInterest"))
            last_oi = parse_float(items[0].get("openInterest"))
            return pct_change(last_oi, prev_oi), last_oi
    return None, None


def get_bybit_long_short(symbol, tf):
    period_map = {
        "1d": "1d",
        "4h": "4h",
        "1h": "1h",
        "30m": "30min",
        "15m": "15min",
        "5m": "5min",
    }
    data = safe_get_json(
        "https://api.bybit.com/v5/market/account-ratio",
        params={"category": "linear", "symbol": symbol, "period": period_map[tf], "limit": 1},
    )
    if isinstance(data, dict):
        items = ((data.get("result") or {}).get("list")) or []
        if items:
            buy = parse_float(items[0].get("buyRatio"), None)
            sell = parse_float(items[0].get("sellRatio"), None)
            if buy is not None and sell not in (None, 0):
                return buy / max(sell, 1e-9)
    return None


def get_bybit_candles(symbol, tf, limit=6):
    data = safe_get_json(
        "https://api.bybit.com/v5/market/kline",
        params={
            "category": "linear",
            "symbol": symbol,
            "interval": timeframe_to_bybit(tf),
            "limit": limit,
        },
    )
    if isinstance(data, dict):
        return ((data.get("result") or {}).get("list")) or []
    return []


# -------------------------------------------------
# MERGED UNIVERSE
# -------------------------------------------------

def get_merged_universe():
    universe = {}
    for source in [get_binance_markets(), get_okx_markets(), get_bybit_markets()]:
        for coin, item in source.items():
            if coin not in universe:
                universe[coin] = {"coin": coin, "exchanges": {}}
            universe[coin]["exchanges"][item["exchange"]] = item["symbol"]
    return universe


def get_merged_tickers():
    merged = {}

    binance = get_binance_tickers()
    okx = get_okx_tickers()
    bybit = get_bybit_tickers()

    all_coins = set(binance.keys()) | set(okx.keys()) | set(bybit.keys())

    for coin in all_coins:
        prices = []
        vols = []
        price_changes = []
        exchanges = {}

        if coin in binance:
            exchanges["Binance"] = binance[coin]["symbol"]
            prices.append(binance[coin]["price"])
            vols.append(binance[coin]["volume_24h"])
            price_changes.append(binance[coin]["price_change_pct"])

        if coin in okx:
            exchanges["OKX"] = okx[coin]["symbol"]
            prices.append(okx[coin]["price"])
            vols.append(okx[coin]["volume_24h"])

        if coin in bybit:
            exchanges["Bybit"] = bybit[coin]["symbol"]
            prices.append(bybit[coin]["price"])
            vols.append(bybit[coin]["volume_24h"])
            price_changes.append(bybit[coin]["price_change_pct"])

        merged[coin] = {
            "coin": coin,
            "exchanges": exchanges,
            "price": avg(prices),
            "volume_24h": sum_safe(vols),
            "price_change_pct_24h": avg(price_changes),
        }

    return merged


def get_primary_exchange_symbol(exchange_symbols):
    if "Binance" in exchange_symbols:
        return "Binance", exchange_symbols["Binance"]
    if "Bybit" in exchange_symbols:
        return "Bybit", exchange_symbols["Bybit"]
    if "OKX" in exchange_symbols:
        return "OKX", exchange_symbols["OKX"]
    return None, None


# -------------------------------------------------
# SHORTLIST SCAN
# -------------------------------------------------

def compute_shortlist_probe(exchange, symbol, tf):
    cache_key = f"probe::{exchange}::{symbol}::{tf}"
    if cache_key in detail_cache:
        return detail_cache[cache_key]

    candles = []
    if exchange == "Binance":
        candles = get_binance_candles(symbol, tf, 6)
        if len(candles) >= 6:
            current_open = parse_float(candles[-1][1])
            current_close = parse_float(candles[-1][4])
            current_high = parse_float(candles[-1][2])
            current_low = parse_float(candles[-1][3])
            current_vol = parse_float(candles[-1][7])
            prev_5 = [parse_float(c[7]) for c in candles[-6:-1]]
        else:
            current_open = current_close = current_high = current_low = current_vol = None
            prev_5 = []

    elif exchange == "Bybit":
        candles = get_bybit_candles(symbol, tf, 6)
        if len(candles) >= 6:
            candles = list(reversed(candles))
            current_open = parse_float(candles[-1][1])
            current_high = parse_float(candles[-1][2])
            current_low = parse_float(candles[-1][3])
            current_close = parse_float(candles[-1][4])
            current_vol = parse_float(candles[-1][6])
            prev_5 = [parse_float(c[6]) for c in candles[-6:-1]]
        else:
            current_open = current_close = current_high = current_low = current_vol = None
            prev_5 = []

    else:
        candles = get_okx_candles(symbol, tf, 6)
        if len(candles) >= 6:
            candles = list(reversed(candles))
            current_open = parse_float(candles[-1][1])
            current_high = parse_float(candles[-1][2])
            current_low = parse_float(candles[-1][3])
            current_close = parse_float(candles[-1][4])
            current_vol = parse_float(candles[-1][7]) or parse_float(candles[-1][6]) or parse_float(candles[-1][5])
            prev_5 = [(parse_float(c[7]) or parse_float(c[6]) or parse_float(c[5])) for c in candles[-6:-1]]
        else:
            current_open = current_close = current_high = current_low = current_vol = None
            prev_5 = []

    avg_5 = avg(prev_5)
    price_move_pct = pct_change(current_close, current_open) if current_open else None
    rel_vol = (current_vol / avg_5) if (current_vol and avg_5 and avg_5 > 0) else None
    atr_pct = ((current_high - current_low) / current_low) * 100.0 if current_low and current_low > 0 else None

    result = {
        "price_move_pct": price_move_pct,
        "rel_volume": rel_vol,
        "atr_pct": atr_pct,
    }
    detail_cache[cache_key] = result
    return result


def shortlist_candidates(tf):
    merged = get_merged_tickers()
    universe = get_merged_universe()

    candidates = []
    for coin, row in merged.items():
        if parse_float(row["volume_24h"]) < 1_500_000:
            continue

        exchange, symbol = get_primary_exchange_symbol(universe.get(coin, {}).get("exchanges", {}))
        if not exchange or not symbol:
            continue

        probe = compute_shortlist_probe(exchange, symbol, tf)
        move = probe["price_move_pct"]
        rel_vol = probe["rel_volume"]
        atr_pct = probe["atr_pct"]

        if move is None or rel_vol is None or atr_pct is None:
            continue

        if atr_pct < shortlist_atr_floor(tf):
            continue

        score = (
            min(abs(move), 6.0) * 0.50 +
            min(rel_vol, 4.0) * 0.40 +
            min(atr_pct, 6.0) * 0.10
        )

        candidates.append({
            "coin": coin,
            "exchanges": row["exchanges"],
            "price": row["price"],
            "volume_24h": row["volume_24h"],
            "price_change_pct_24h": row["price_change_pct_24h"],
            "shortlist_move_pct": move,
            "shortlist_rel_volume": rel_vol,
            "shortlist_atr_pct": atr_pct,
            "shortlist_score": round(score, 4),
        })

    candidates.sort(key=lambda x: x["shortlist_score"], reverse=True)
    return candidates[:90]


# -------------------------------------------------
# DEEP METRICS
# -------------------------------------------------

def compute_candle_metrics_binance(symbol, tf):
    cache_key = f"binance_candles::{symbol}::{tf}"
    if cache_key in detail_cache:
        return detail_cache[cache_key]

    candles = get_binance_candles(symbol, tf, 6)
    result = {"price_change_pct": None, "volume_change_pct": None, "volatility_pct": None, "rel_volume": None}

    if len(candles) >= 6:
        current_open = parse_float(candles[-1][1])
        current_close = parse_float(candles[-1][4])
        current_high = parse_float(candles[-1][2])
        current_low = parse_float(candles[-1][3])
        current_vol = parse_float(candles[-1][7])
        prev_5 = [parse_float(c[7]) for c in candles[-6:-1]]
        avg_5 = avg(prev_5)

        result["price_change_pct"] = pct_change(current_close, current_open)
        result["volume_change_pct"] = pct_change(current_vol, avg_5)
        result["rel_volume"] = (current_vol / avg_5) if avg_5 and avg_5 > 0 else None
        if current_low > 0:
            result["volatility_pct"] = ((current_high - current_low) / current_low) * 100.0

    detail_cache[cache_key] = result
    return result


def compute_candle_metrics_okx(symbol, tf):
    cache_key = f"okx_candles::{symbol}::{tf}"
    if cache_key in detail_cache:
        return detail_cache[cache_key]

    candles = get_okx_candles(symbol, tf, 6)
    result = {"price_change_pct": None, "volume_change_pct": None, "volatility_pct": None, "rel_volume": None}

    if len(candles) >= 6:
        candles = list(reversed(candles))
        current_open = parse_float(candles[-1][1])
        current_high = parse_float(candles[-1][2])
        current_low = parse_float(candles[-1][3])
        current_close = parse_float(candles[-1][4])
        current_vol = parse_float(candles[-1][7]) or parse_float(candles[-1][6]) or parse_float(candles[-1][5])
        prev_5 = [(parse_float(c[7]) or parse_float(c[6]) or parse_float(c[5])) for c in candles[-6:-1]]
        avg_5 = avg(prev_5)

        result["price_change_pct"] = pct_change(current_close, current_open)
        result["volume_change_pct"] = pct_change(current_vol, avg_5)
        result["rel_volume"] = (current_vol / avg_5) if avg_5 and avg_5 > 0 else None
        if current_low > 0:
            result["volatility_pct"] = ((current_high - current_low) / current_low) * 100.0

    detail_cache[cache_key] = result
    return result


def compute_candle_metrics_bybit(symbol, tf):
    cache_key = f"bybit_candles::{symbol}::{tf}"
    if cache_key in detail_cache:
        return detail_cache[cache_key]

    candles = get_bybit_candles(symbol, tf, 6)
    result = {"price_change_pct": None, "volume_change_pct": None, "volatility_pct": None, "rel_volume": None}

    if len(candles) >= 6:
        candles = list(reversed(candles))
        current_open = parse_float(candles[-1][1])
        current_high = parse_float(candles[-1][2])
        current_low = parse_float(candles[-1][3])
        current_close = parse_float(candles[-1][4])
        current_vol = parse_float(candles[-1][6])
        prev_5 = [parse_float(c[6]) for c in candles[-6:-1]]
        avg_5 = avg(prev_5)

        result["price_change_pct"] = pct_change(current_close, current_open)
        result["volume_change_pct"] = pct_change(current_vol, avg_5)
        result["rel_volume"] = (current_vol / avg_5) if avg_5 and avg_5 > 0 else None
        if current_low > 0:
            result["volatility_pct"] = ((current_high - current_low) / current_low) * 100.0

    detail_cache[cache_key] = result
    return result


def get_okx_oi_change(symbol, tf):
    oi = get_okx_open_interest(symbol)
    return None, oi


def get_okx_long_short(symbol, tf):
    return None


def get_binance_exchange_metrics(symbol, tf):
    candle = compute_candle_metrics_binance(symbol, tf)
    oi_change_pct, oi = get_binance_oi_change(symbol, tf)
    funding = get_binance_funding(symbol)
    long_short = get_binance_long_short(symbol, tf)

    return {
        "price_change_pct": candle["price_change_pct"],
        "volume_change_pct": candle["volume_change_pct"],
        "volatility_pct": candle["volatility_pct"],
        "rel_volume": candle["rel_volume"],
        "oi_change_pct": oi_change_pct,
        "open_interest": oi,
        "funding_rate": funding,
        "long_short_ratio": long_short,
        "liquidations": None,
    }


def get_okx_exchange_metrics(symbol, tf):
    candle = compute_candle_metrics_okx(symbol, tf)
    oi_change_pct, oi = get_okx_oi_change(symbol, tf)
    funding = get_okx_funding(symbol)
    long_short = get_okx_long_short(symbol, tf)

    return {
        "price_change_pct": candle["price_change_pct"],
        "volume_change_pct": candle["volume_change_pct"],
        "volatility_pct": candle["volatility_pct"],
        "rel_volume": candle["rel_volume"],
        "oi_change_pct": oi_change_pct,
        "open_interest": oi,
        "funding_rate": funding,
        "long_short_ratio": long_short,
        "liquidations": None,
    }


def get_bybit_exchange_metrics(symbol, tf):
    candle = compute_candle_metrics_bybit(symbol, tf)
    oi_change_pct, oi = get_bybit_oi_change(symbol, tf)
    funding = get_bybit_funding(symbol)
    long_short = get_bybit_long_short(symbol, tf)

    return {
        "price_change_pct": candle["price_change_pct"],
        "volume_change_pct": candle["volume_change_pct"],
        "volatility_pct": candle["volatility_pct"],
        "rel_volume": candle["rel_volume"],
        "oi_change_pct": oi_change_pct,
        "open_interest": oi,
        "funding_rate": funding,
        "long_short_ratio": long_short,
        "liquidations": None,
    }


def merge_metrics(coin, exchange_symbols, tf):
    metrics = []
    contributing = []

    if "Binance" in exchange_symbols:
        m = get_binance_exchange_metrics(exchange_symbols["Binance"], tf)
        if any(v is not None for v in m.values()):
            metrics.append(m)
            contributing.append("Binance")

    if "OKX" in exchange_symbols:
        m = get_okx_exchange_metrics(exchange_symbols["OKX"], tf)
        if any(v is not None for v in m.values()):
            metrics.append(m)
            contributing.append("OKX")

    if "Bybit" in exchange_symbols:
        m = get_bybit_exchange_metrics(exchange_symbols["Bybit"], tf)
        if any(v is not None for v in m.values()):
            metrics.append(m)
            contributing.append("Bybit")

    if not metrics:
        return None

    price_change_pct = avg([m["price_change_pct"] for m in metrics if m["price_change_pct"] is not None])
    volume_change_pct = avg([m["volume_change_pct"] for m in metrics if m["volume_change_pct"] is not None])
    rel_volume = avg([m["rel_volume"] for m in metrics if m["rel_volume"] is not None])
    oi_change_pct = avg([m["oi_change_pct"] for m in metrics if m["oi_change_pct"] is not None])
    funding_rate = avg([m["funding_rate"] for m in metrics if m["funding_rate"] is not None])
    long_short_ratio = avg([m["long_short_ratio"] for m in metrics if m["long_short_ratio"] is not None])
    volatility_pct = avg([m["volatility_pct"] for m in metrics if m["volatility_pct"] is not None])
    open_interest = sum_safe([m["open_interest"] for m in metrics if m["open_interest"] is not None])

    liquidation_total = sum_safe([m["liquidations"] for m in metrics if m["liquidations"] is not None])
    liquidation_total = liquidation_total if liquidation_total > 0 else None

    bias = "Neutral"
    if (
        price_change_pct is not None and price_change_pct > 0
        and oi_change_pct is not None and oi_change_pct > 0
        and long_short_ratio is not None and long_short_ratio > 1
        and funding_rate is not None and funding_rate > 0
    ):
        bias = "Bullish"
    elif (
        price_change_pct is not None and price_change_pct < 0
        and oi_change_pct is not None and oi_change_pct > 0
        and long_short_ratio is not None and long_short_ratio < 1
        and funding_rate is not None and funding_rate < 0
    ):
        bias = "Bearish"

    return {
        "coin": coin,
        "exchanges": ", ".join(contributing),
        "price_change_pct": price_change_pct,
        "volume_change_pct": volume_change_pct,
        "rel_volume": rel_volume,
        "oi_change_pct": oi_change_pct,
        "long_short_ratio": long_short_ratio,
        "funding_rate": funding_rate,
        "volatility_pct": volatility_pct,
        "liquidations": liquidation_total,
        "open_interest": open_interest,
        "bias": bias,
        "activity_score": 0,
    }


def score_deep_scan(row, tf):
    t = deep_thresholds(tf)

    move = min(abs(parse_float(row.get("price_change_pct"), 0)), t["move_cap"])
    vol = min(max(parse_float(row.get("volume_change_pct"), 0), 0), t["vol_cap"])
    oi = min(abs(parse_float(row.get("oi_change_pct"), 0)), t["oi_cap"])
    atr = min(parse_float(row.get("volatility_pct"), 0), t["atr_cap"])
    rel_vol = min(parse_float(row.get("rel_volume"), 0), 4.0)
    funding = parse_float(row.get("funding_rate"), 0)
    lsr = row.get("long_short_ratio")

    score = 0.0
    score += move * 7.0
    score += vol * 0.16
    score += oi * 3.0
    score += atr * 5.0
    score += rel_vol * 8.0

    if row["bias"] == "Bullish":
        score += 6
    elif row["bias"] == "Bearish":
        score += 6

    if funding is not None and abs(funding) > 0.0001:
        score += min(abs(funding) * 10000, 5)

    if lsr is not None and abs(lsr - 1.0) > 0.05:
        score += min(abs(lsr - 1.0) * 10, 5)

    return round(min(score, 100), 2)


# -------------------------------------------------
# SCANNER
# -------------------------------------------------

def run_scan(tf):
    cache_key = f"scan::{tf}"
    if cache_key in scan_cache:
        return scan_cache[cache_key]

    if tf not in TIMEFRAME_OPTIONS:
        return []

    candidates = shortlist_candidates(tf)

    rows = []
    for c in candidates:
        merged = merge_metrics(c["coin"], c["exchanges"], tf)
        if merged is None:
            continue

        merged["shortlist_score"] = c["shortlist_score"]
        merged["shortlist_move_pct"] = c["shortlist_move_pct"]
        merged["shortlist_rel_volume"] = c["shortlist_rel_volume"]
        merged["shortlist_atr_pct"] = c["shortlist_atr_pct"]
        merged["activity_score"] = score_deep_scan(merged, tf)

        rows.append(merged)

    rows.sort(key=lambda x: x["activity_score"], reverse=True)

    threshold = deep_thresholds(tf)["min_score"]
    strict_rows = [r for r in rows if r["activity_score"] >= threshold]

    final_rows = strict_rows[:30] if strict_rows else rows[:30]

    scan_cache[cache_key] = final_rows
    return final_rows


def analyze_coin(coin, tf):
    universe = get_merged_universe()
    coin = coin.upper().strip()

    if coin not in universe:
        return None

    row = merge_metrics(coin, universe[coin]["exchanges"], tf)
    if row is None:
        return None

    row["activity_score"] = score_deep_scan(row, tf)
    return row


# -------------------------------------------------
# HTML RENDERERS
# -------------------------------------------------

def screener_row_html(i, row, tf):
    return f"""
    <tr>
        <td>{i}</td>
        <td><b>{row['coin']}</b></td>
        <td>{row['exchanges']}</td>
        <td>{centered_bar(row['price_change_pct'], max_abs=6, suffix='%')}</td>
        <td>{fill_bar(max(parse_float(row['volume_change_pct'], 0), 0), max_value=200, color='#8b5cf6', suffix='%')}</td>
        <td>{centered_bar(row['oi_change_pct'], max_abs=10, suffix='%')}</td>
        <td>{ratio_bar(row['long_short_ratio'])}</td>
        <td>{centered_bar((parse_float(row['funding_rate'], 0) * 100), max_abs=0.10, suffix='%')}</td>
        <td>{fill_bar(row['volatility_pct'], max_value=8, color='#f97316', suffix='%')}</td>
        <td>{bias_badge(row['bias'])}</td>
        <td>{score_bar(row['activity_score'])}</td>
        <td><a href="/analyze?coin={row['coin']}&tf={tf}" style="color:#38bdf8;">Analyze</a></td>
        <td><a href="/favorite?coin={row['coin']}" style="color:#facc15;">⭐ Add</a></td>
    </tr>
    """


def favorite_row_html(i, row, tf):
    return f"""
    <tr>
        <td>{i}</td>
        <td><b>{row['coin']}</b></td>
        <td>{row['exchanges']}</td>
        <td>{centered_bar(row['price_change_pct'], max_abs=6, suffix='%')}</td>
        <td>{fill_bar(max(parse_float(row['volume_change_pct'], 0), 0), max_value=200, color='#8b5cf6', suffix='%')}</td>
        <td>{centered_bar(row['oi_change_pct'], max_abs=10, suffix='%')}</td>
        <td>{ratio_bar(row['long_short_ratio'])}</td>
        <td>{centered_bar((parse_float(row['funding_rate'], 0) * 100), max_abs=0.10, suffix='%')}</td>
        <td>{fill_bar(row['volatility_pct'], max_value=8, color='#f97316', suffix='%')}</td>
        <td>{bias_badge(row['bias'])}</td>
        <td>{score_bar(row['activity_score'])}</td>
        <td><a href="/analyze?coin={row['coin']}&tf={tf}" style="color:#38bdf8;">Analyze</a></td>
        <td><a href="/remove_favorite?coin={row['coin']}" style="color:#f87171;">Remove</a></td>
    </tr>
    """


def analyze_metric_card(title, bar_html):
    return f"""
    <div class="metric-card">
        <div class="metric-title">{title}</div>
        {bar_html}
    </div>
    """


# -------------------------------------------------
# ROUTES
# -------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home():
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(tf: str = Query(None)):
    body = f"""
        <h1>Scalping Screener</h1>
        <form class="controls" method="get" action="/dashboard">
            <label>Timeframe:</label>
            {build_select("tf", tf or "15m")}
            <button type="submit">Scan</button>
        </form>
    """

    if not tf:
        body += """
        <div class="card">
            <p>Select a timeframe and click <b>Scan</b>.</p>
            <p class="muted">Shortlist scan runs first. Deep scan only checks the Top 30/Top 90 candidates.</p>
        </div>
        """
        return base_layout("Scalping Screener", body)

    rows = run_scan(tf)
    rows_html = "".join([screener_row_html(i, row, tf) for i, row in enumerate(rows, start=1)])

    body += f"""
        <div class="card">
            <p>Showing merged Binance + OKX + Bybit futures results for <b>{tf}</b>.</p>
            <p class="muted">Scanner uses loose shortlist ATR, then deep-scan scoring. If strict matches are empty, it falls back to top-scored results instead of showing nothing.</p>
        </div>
        <div class="table-wrap">
            <table>
                <tr>
                    <th>#</th>
                    <th>Coin</th>
                    <th>Exchanges</th>
                    <th>Price</th>
                    <th>Volume</th>
                    <th>OI</th>
                    <th>L/S</th>
                    <th>Funding</th>
                    <th>Volatility</th>
                    <th>Bias</th>
                    <th>Score</th>
                    <th>Analyze</th>
                    <th>Favorite</th>
                </tr>
                {rows_html if rows_html else "<tr><td colspan='13'>No matching coins found for this timeframe.</td></tr>"}
            </table>
        </div>
    """
    return base_layout("Scalping Screener", body)


@app.get("/analyze", response_class=HTMLResponse)
def analyze(coin: str = Query("BTC"), tf: str = Query("15m")):
    if tf not in TIMEFRAME_OPTIONS:
        tf = "15m"

    row = analyze_coin(coin, tf)

    body = f"""
        <h1>{coin.upper()} Analysis</h1>
        <form class="controls" method="get" action="/analyze">
            <input name="coin" value="{coin.upper()}" style="padding:8px 10px;border-radius:8px;background:#0f172a;color:#fff;border:1px solid #334155;" />
            <label>Timeframe:</label>
            {build_select("tf", tf)}
            <button type="submit">Analyze</button>
        </form>
    """

    if row is None:
        body += """
        <div class="card">
            <p>Coin not found in merged Binance / OKX / Bybit USDT futures universe.</p>
        </div>
        """
        return base_layout("Analyze Coin", body)

    body += f"""
        <div class="card">
            <p><b>Coin:</b> {row['coin']}</p>
            <p><b>Exchanges:</b> {row['exchanges']}</p>
            <p><b>Bias:</b> {bias_badge(row['bias'])}</p>
        </div>

        <div class="grid">
            {analyze_metric_card("Price Momentum", centered_bar(row['price_change_pct'], max_abs=6, width=180, height=12, suffix='%'))}
            {analyze_metric_card("Volume Activity", fill_bar(max(parse_float(row['volume_change_pct'], 0), 0), max_value=200, width=180, height=12, color='#8b5cf6', suffix='%'))}
            {analyze_metric_card("Open Interest Flow", centered_bar(row['oi_change_pct'], max_abs=10, width=180, height=12, suffix='%'))}
            {analyze_metric_card("Long / Short Positioning", ratio_bar(row['long_short_ratio'], width=180, height=12))}
            {analyze_metric_card("Funding Pressure", centered_bar((parse_float(row['funding_rate'], 0) * 100), max_abs=0.10, width=180, height=12, suffix='%'))}
            {analyze_metric_card("Volatility", fill_bar(row['volatility_pct'], max_value=8, width=180, height=12, color='#f97316', suffix='%'))}
            {analyze_metric_card("Signal Strength", score_bar(row['activity_score'], max_score=100, width=180, height=12))}
        </div>

        <div class="card">
            <a href="/favorite?coin={row['coin']}" style="color:#facc15;">⭐ Add to Favorites</a>
        </div>
    """
    return base_layout("Analyze Coin", body)


@app.get("/favorites", response_class=HTMLResponse)
def favorites_page(tf: str = Query("15m")):
    if tf not in TIMEFRAME_OPTIONS:
        tf = "15m"

    rows_html = ""
    for i, coin in enumerate(favorites, start=1):
        row = analyze_coin(coin, tf)
        if row is None:
            rows_html += f"""
            <tr>
                <td>{i}</td>
                <td>{coin}</td>
                <td colspan="10">Not found in current merged futures universe</td>
                <td><a href="/remove_favorite?coin={coin}" style="color:#f87171;">Remove</a></td>
            </tr>
            """
            continue

        rows_html += favorite_row_html(i, row, tf)

    body = f"""
        <h1>Favorites</h1>
        <form class="controls" method="get" action="/favorites">
            <label>Timeframe:</label>
            {build_select("tf", tf)}
            <button type="submit">Refresh</button>
        </form>

        <form class="controls" method="get" action="/favorite">
            <input name="coin" placeholder="BTC, ETH, SOL" style="padding:8px 10px;border-radius:8px;background:#0f172a;color:#fff;border:1px solid #334155;" />
            <button type="submit">Add Coin</button>
        </form>

        <div class="table-wrap">
            <table>
                <tr>
                    <th>#</th>
                    <th>Coin</th>
                    <th>Exchanges</th>
                    <th>Price</th>
                    <th>Volume</th>
                    <th>OI</th>
                    <th>L/S</th>
                    <th>Funding</th>
                    <th>Volatility</th>
                    <th>Bias</th>
                    <th>Score</th>
                    <th>Analyze</th>
                    <th>Action</th>
                </tr>
                {rows_html if rows_html else "<tr><td colspan='13'>No favorites yet.</td></tr>"}
            </table>
        </div>
    """
    return base_layout("Favorites", body)


@app.get("/favorite")
def add_favorite(coin: str = Query(...)):
    coin = coin.upper().strip()
    if coin and coin not in favorites:
        favorites.append(coin)
    return RedirectResponse(url="/favorites", status_code=302)


@app.get("/remove_favorite")
def remove_favorite(coin: str = Query(...)):
    coin = coin.upper().strip()
    if coin in favorites:
        favorites.remove(coin)
    return RedirectResponse(url="/favorites", status_code=302)


@app.get("/debug")
def debug(tf: str = Query("15m")):
    universe = get_merged_universe()
    shortlist = shortlist_candidates(tf) if tf in TIMEFRAME_OPTIONS else []
    scan = run_scan(tf) if tf in TIMEFRAME_OPTIONS else []

    return {
        "timeframes": TIMEFRAME_OPTIONS,
        "favorites_count": len(favorites),
        "merged_universe_count": len(universe),
        "binance_markets": len(get_binance_markets()),
        "okx_markets": len(get_okx_markets()),
        "bybit_markets": len(get_bybit_markets()),
        "shortlist_count": len(shortlist),
        "scan_count": len(scan),
        "sample_shortlist": shortlist[:5],
        "sample_scan": scan[:5],
    }
