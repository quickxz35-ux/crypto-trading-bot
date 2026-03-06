import math
import requests
from cachetools import TTLCache
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, RedirectResponse

app = FastAPI(title="Scalping Screener")

TIMEFRAME_OPTIONS = ["1d", "4h", "1h", "30m", "15m", "5m"]

favorites = []

market_cache = TTLCache(maxsize=10, ttl=900)     # 15 min
scan_cache = TTLCache(maxsize=20, ttl=45)        # 45 sec
detail_cache = TTLCache(maxsize=5000, ttl=60)    # 60 sec


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
    for quote in ["USDT", "USDC", "USD"]:
        if s.endswith(quote):
            return s[: -len(quote)]
    return s


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


def build_select(name, selected):
    html = ""
    for tf in TIMEFRAME_OPTIONS:
        sel = "selected" if tf == selected else ""
        html += f'<option value="{tf}" {sel}>{tf}</option>'
    return f'<select name="{name}" style="padding:8px;">{html}</select>'


def base_layout(title, body):
    return f"""
    <html>
    <head>
        <title>{title}</title>
        <style>
            body {{ font-family: Arial, sans-serif; background:#0f172a; color:white; padding:20px; }}
            h1 {{ color:#38bdf8; }}
            .nav a {{ margin-right:20px; color:#38bdf8; text-decoration:none; font-weight:bold; }}
            table {{ width:100%; border-collapse:collapse; background:#1e293b; font-size:14px; }}
            th, td {{ padding:10px; border:1px solid #334155; text-align:left; }}
            th {{ background:#334155; }}
            tr:hover {{ background:#273549; }}
            .controls {{ margin:16px 0; }}
            button {{ padding:8px 14px; }}
            input, select {{ margin-right:10px; }}
            .card {{ background:#1e293b; padding:20px; border:1px solid #334155; margin:16px 0; }}
            .muted {{ color:#94a3b8; }}
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
                    "price_change_pct": parse_float(item.get("last"), 0),  # placeholder, replaced by candles
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
            return parse_float(items[0].get("buyRatio"), None) / max(parse_float(items[0].get("sellRatio"), 1), 1e-9)
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
# MARKET UNIVERSE / SHORTLIST
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


def shortlist_candidates():
    merged = get_merged_tickers()
    rows = list(merged.values())

    # fast filter
    filtered = []
    for row in rows:
        if parse_float(row["volume_24h"]) < 5_000_000:
            continue
        if abs(parse_float(row["price_change_pct_24h"])) < 0.8:
            continue
        filtered.append(row)

    # rank cheap shortlist first
    filtered.sort(
        key=lambda x: (
            abs(parse_float(x["price_change_pct_24h"]))
            + math.log10(max(parse_float(x["volume_24h"]), 1))
        ),
        reverse=True,
    )

    return filtered[:90]


# -------------------------------------------------
# PER-EXCHANGE DETAIL METRICS
# -------------------------------------------------

def compute_candle_metrics_binance(symbol, tf):
    cache_key = f"binance_candles::{symbol}::{tf}"
    if cache_key in detail_cache:
        return detail_cache[cache_key]

    candles = get_binance_candles(symbol, tf, 6)
    result = {"price_change_pct": None, "volume_change_pct": None, "volatility_pct": None}

    if len(candles) >= 6:
        # order oldest -> newest already
        current_open = parse_float(candles[-1][1])
        current_close = parse_float(candles[-1][4])
        current_high = parse_float(candles[-1][2])
        current_low = parse_float(candles[-1][3])
        current_vol = parse_float(candles[-1][7])  # quote volume
        prev_5 = [parse_float(c[7]) for c in candles[-6:-1]]
        avg_5 = avg(prev_5)

        result["price_change_pct"] = pct_change(current_close, current_open)
        result["volume_change_pct"] = pct_change(current_vol, avg_5)
        if current_low > 0:
            result["volatility_pct"] = ((current_high - current_low) / current_low) * 100.0

    detail_cache[cache_key] = result
    return result


def compute_candle_metrics_okx(symbol, tf):
    cache_key = f"okx_candles::{symbol}::{tf}"
    if cache_key in detail_cache:
        return detail_cache[cache_key]

    candles = get_okx_candles(symbol, tf, 6)
    result = {"price_change_pct": None, "volume_change_pct": None, "volatility_pct": None}

    if len(candles) >= 6:
        # newest first
        candles = list(reversed(candles))
        current_open = parse_float(candles[-1][1])
        current_high = parse_float(candles[-1][2])
        current_low = parse_float(candles[-1][3])
        current_close = parse_float(candles[-1][4])
        current_vol = parse_float(candles[-1][7]) or parse_float(candles[-1][6]) or parse_float(candles[-1][5])
        prev_5 = []
        for c in candles[-6:-1]:
            prev_5.append(parse_float(c[7]) or parse_float(c[6]) or parse_float(c[5]))
        avg_5 = avg(prev_5)

        result["price_change_pct"] = pct_change(current_close, current_open)
        result["volume_change_pct"] = pct_change(current_vol, avg_5)
        if current_low > 0:
            result["volatility_pct"] = ((current_high - current_low) / current_low) * 100.0

    detail_cache[cache_key] = result
    return result


def compute_candle_metrics_bybit(symbol, tf):
    cache_key = f"bybit_candles::{symbol}::{tf}"
    if cache_key in detail_cache:
        return detail_cache[cache_key]

    candles = get_bybit_candles(symbol, tf, 6)
    result = {"price_change_pct": None, "volume_change_pct": None, "volatility_pct": None}

    if len(candles) >= 6:
        # newest first
        candles = list(reversed(candles))
        current_open = parse_float(candles[-1][1])
        current_high = parse_float(candles[-1][2])
        current_low = parse_float(candles[-1][3])
        current_close = parse_float(candles[-1][4])
        current_vol = parse_float(candles[-1][6])  # turnover
        prev_5 = [parse_float(c[6]) for c in candles[-6:-1]]
        avg_5 = avg(prev_5)

        result["price_change_pct"] = pct_change(current_close, current_open)
        result["volume_change_pct"] = pct_change(current_vol, avg_5)
        if current_low > 0:
            result["volatility_pct"] = ((current_high - current_low) / current_low) * 100.0

    detail_cache[cache_key] = result
    return result


def get_okx_oi_change(symbol, tf):
    # OKX public OI history isn't as convenient across these timeframes.
    # For v1 we use current OI only, no pct change from OKX.
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

    price_change_pct = avg([m["price_change_pct"] for m in metrics])
    volume_change_pct = avg([m["volume_change_pct"] for m in metrics])
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

    liquidation_boost = 0
    if liquidation_total is not None:
        liquidation_boost = min(liquidation_total / 1_000_000, 10)

    activity_score = round(
        (abs(price_change_pct) if price_change_pct is not None else 0) * 10
        + (max(volume_change_pct, 0) if volume_change_pct is not None else 0) * 4
        + (abs(oi_change_pct) if oi_change_pct is not None else 0) * 12
        + (volatility_pct if volatility_pct is not None else 0) * 8
        + liquidation_boost,
        2,
    )

    return {
        "coin": coin,
        "exchanges": ", ".join(contributing),
        "price_change_pct": price_change_pct,
        "volume_change_pct": volume_change_pct,
        "oi_change_pct": oi_change_pct,
        "long_short_ratio": long_short_ratio,
        "funding_rate": funding_rate,
        "volatility_pct": volatility_pct,
        "liquidations": liquidation_total,
        "open_interest": open_interest,
        "bias": bias,
        "activity_score": activity_score,
    }


# -------------------------------------------------
# SCANNER
# -------------------------------------------------

def run_scan(tf):
    cache_key = f"scan::{tf}"
    if cache_key in scan_cache:
        return scan_cache[cache_key]

    if tf not in TIMEFRAME_OPTIONS:
        return []

    candidates = shortlist_candidates()

    # stage 2: OI + candles on shortlist
    rows = []
    for c in candidates:
        merged = merge_metrics(c["coin"], c["exchanges"], tf)
        if merged is None:
            continue

        # shortlist rules with OI included
        if merged["price_change_pct"] is None:
            continue
        if abs(merged["price_change_pct"]) < 0.5:
            continue
        if merged["volume_change_pct"] is not None and merged["volume_change_pct"] < 5:
            continue
        if merged["oi_change_pct"] is not None and abs(merged["oi_change_pct"]) < 0.3:
            continue

        rows.append(merged)

    rows.sort(key=lambda x: x["activity_score"], reverse=True)
    rows = rows[:30]

    scan_cache[cache_key] = rows
    return rows


def analyze_coin(coin, tf):
    universe = get_merged_universe()
    coin = coin.upper().strip()

    if coin not in universe:
        return None

    return merge_metrics(coin, universe[coin]["exchanges"], tf)


# -------------------------------------------------
# ROUTES
# -------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home():
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(tf: str = Query(None)):
    rows_html = ""
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
            <p class="muted">The screener will not run until you choose a timeframe.</p>
        </div>
        """
        return base_layout("Scalping Screener", body)

    rows = run_scan(tf)

    for i, row in enumerate(rows, start=1):
        bias_color = "#16a34a" if row["bias"] == "Bullish" else "#ef4444" if row["bias"] == "Bearish" else "#eab308"
        rows_html += f"""
        <tr>
            <td>{i}</td>
            <td>{row['coin']}</td>
            <td>{row['exchanges']}</td>
            <td>{format_num(row['price_change_pct'], 2, '%')}</td>
            <td>{format_num(row['volume_change_pct'], 2, '%')}</td>
            <td>{format_num(row['oi_change_pct'], 2, '%')}</td>
            <td>{format_num(row['long_short_ratio'], 3)}</td>
            <td>{format_num(row['funding_rate'], 4)}</td>
            <td>{format_num(row['volatility_pct'], 2, '%')}</td>
            <td>{format_num(row['liquidations'], 2)}</td>
            <td style="color:{bias_color};font-weight:bold;">{row['bias']}</td>
            <td>{format_num(row['activity_score'], 2)}</td>
            <td><a href="/analyze?coin={row['coin']}&tf={tf}" style="color:#38bdf8;">Analyze</a></td>
            <td><a href="/favorite?coin={row['coin']}" style="color:#facc15;">⭐ Add</a></td>
        </tr>
        """

    body += f"""
        <div class="card">
            <p>Showing merged Binance + OKX + Bybit results for <b>{tf}</b>.</p>
        </div>
        <table>
            <tr>
                <th>#</th>
                <th>Coin</th>
                <th>Exchanges</th>
                <th>Price %</th>
                <th>Volume %</th>
                <th>OI %</th>
                <th>L/S</th>
                <th>Funding</th>
                <th>Volatility</th>
                <th>Liquidations</th>
                <th>Bias</th>
                <th>Score</th>
                <th>Analyze</th>
                <th>Favorite</th>
            </tr>
            {rows_html if rows_html else "<tr><td colspan='14'>No matching coins found for this timeframe.</td></tr>"}
        </table>
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
            <input name="coin" value="{coin.upper()}" style="padding:8px;" />
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

    bias_color = "#16a34a" if row["bias"] == "Bullish" else "#ef4444" if row["bias"] == "Bearish" else "#eab308"

    body += f"""
        <div class="card">
            <p><b>Coin:</b> {row['coin']}</p>
            <p><b>Exchanges:</b> {row['exchanges']}</p>
            <p><b>Price Change %:</b> {format_num(row['price_change_pct'], 2, '%')}</p>
            <p><b>Volume Change %:</b> {format_num(row['volume_change_pct'], 2, '%')}</p>
            <p><b>Open Interest Change %:</b> {format_num(row['oi_change_pct'], 2, '%')}</p>
            <p><b>Long / Short Ratio:</b> {format_num(row['long_short_ratio'], 3)}</p>
            <p><b>Funding Rate:</b> {format_num(row['funding_rate'], 4)}</p>
            <p><b>Volatility:</b> {format_num(row['volatility_pct'], 2, '%')}</p>
            <p><b>Liquidations:</b> {format_num(row['liquidations'], 2)}</p>
            <p><b>Bias:</b> <span style="color:{bias_color};font-weight:bold;">{row['bias']}</span></p>
            <p><b>Activity Score:</b> {format_num(row['activity_score'], 2)}</p>
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
                <td colspan="9">Not found in current merged futures universe</td>
                <td><a href="/remove_favorite?coin={coin}" style="color:#f87171;">Remove</a></td>
            </tr>
            """
            continue

        bias_color = "#16a34a" if row["bias"] == "Bullish" else "#ef4444" if row["bias"] == "Bearish" else "#eab308"

        rows_html += f"""
        <tr>
            <td>{i}</td>
            <td>{row['coin']}</td>
            <td>{row['exchanges']}</td>
            <td>{format_num(row['price_change_pct'], 2, '%')}</td>
            <td>{format_num(row['volume_change_pct'], 2, '%')}</td>
            <td>{format_num(row['oi_change_pct'], 2, '%')}</td>
            <td>{format_num(row['long_short_ratio'], 3)}</td>
            <td>{format_num(row['funding_rate'], 4)}</td>
            <td>{format_num(row['volatility_pct'], 2, '%')}</td>
            <td style="color:{bias_color};font-weight:bold;">{row['bias']}</td>
            <td>{format_num(row['activity_score'], 2)}</td>
            <td><a href="/analyze?coin={row['coin']}&tf={tf}" style="color:#38bdf8;">Analyze</a></td>
            <td><a href="/remove_favorite?coin={row['coin']}" style="color:#f87171;">Remove</a></td>
        </tr>
        """

    body = f"""
        <h1>Favorites</h1>
        <form class="controls" method="get" action="/favorites">
            <label>Timeframe:</label>
            {build_select("tf", tf)}
            <button type="submit">Refresh</button>
        </form>

        <form class="controls" method="get" action="/favorite">
            <input name="coin" placeholder="BTC, ETH, SOL" style="padding:8px;" />
            <button type="submit">Add Coin</button>
        </form>

        <table>
            <tr>
                <th>#</th>
                <th>Coin</th>
                <th>Exchanges</th>
                <th>Price %</th>
                <th>Volume %</th>
                <th>OI %</th>
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
def debug():
    universe = get_merged_universe()
    return {
        "timeframes": TIMEFRAME_OPTIONS,
        "favorites_count": len(favorites),
        "merged_universe_count": len(universe),
        "binance_markets": len(get_binance_markets()),
        "okx_markets": len(get_okx_markets()),
        "bybit_markets": len(get_bybit_markets()),
    }
