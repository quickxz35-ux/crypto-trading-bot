import os
import time
import json
import threading
import requests
import websocket

from cachetools import TTLCache
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

app = FastAPI(title="Crypto Favorites Watchlist")

TIMEFRAME_OPTIONS = ["1d", "4h", "1h", "30m", "15m", "5m", "1m"]
favorites = []

data_cache = TTLCache(maxsize=5000, ttl=30)
stale_cache = {}

CRYPTOMETER_API_KEY = os.getenv("CRYPTOMETER_API_KEY")
CRYPTOMETER_TIMEOUT = float(os.getenv("CRYPTOMETER_TIMEOUT", "20"))
CRYPTOMETER_RETRIES = int(os.getenv("CRYPTOMETER_RETRIES", "2"))
CRYPTOMETER_BACKOFF = float(os.getenv("CRYPTOMETER_BACKOFF", "0.6"))

SESSION = requests.Session()

# -------------------------------------------------
# TRADE FLOW STORAGE
# -------------------------------------------------

trade_flow = {}
trade_streams_started = set()
trade_flow_lock = threading.Lock()


def start_trade_stream(symbol):
    if symbol in trade_streams_started:
        return

    trade_streams_started.add(symbol)
    url = f"wss://fstream.binance.com/ws/{symbol.lower()}@aggTrade"

    def on_message(ws, message):
        try:
            data = json.loads(message)
            qty = parse_float(data.get("q"), 0.0) or 0.0
            is_sell = bool(data.get("m", False))

            with trade_flow_lock:
                if symbol not in trade_flow:
                    trade_flow[symbol] = {"buy": 0.0, "sell": 0.0}

                if is_sell:
                    trade_flow[symbol]["sell"] += qty
                else:
                    trade_flow[symbol]["buy"] += qty
        except Exception as e:
            print("TRADE FLOW MESSAGE ERROR:", symbol, str(e))

    def on_error(ws, error):
        print("TRADE FLOW WS ERROR:", symbol, str(error))

    def on_close(ws, close_status_code, close_msg):
        print("TRADE FLOW WS CLOSED:", symbol, close_status_code, close_msg)

    def run():
        while True:
            try:
                ws = websocket.WebSocketApp(
                    url,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                )
                ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as e:
                print("TRADE FLOW WS RECONNECT:", symbol, str(e))
            time.sleep(3)

    threading.Thread(target=run, daemon=True).start()


def reset_trade_flow_loop():
    while True:
        time.sleep(60)
        with trade_flow_lock:
            for symbol in list(trade_flow.keys()):
                trade_flow[symbol] = {"buy": 0.0, "sell": 0.0}


threading.Thread(target=reset_trade_flow_loop, daemon=True).start()


# -------------------------------------------------
# HELPERS
# -------------------------------------------------

def safe_get_json(url, params=None, timeout=10, retries=0, backoff=0.5):
    headers = {"User-Agent": "Mozilla/5.0"}
    last_error = None
    for attempt in range(retries + 1):
        try:
            r = SESSION.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 200:
                return r.json()

            if r.status_code in (408, 425, 429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(backoff * (2 ** attempt))
                continue

            print("GET FAILED:", url, r.status_code, r.text[:300])
            return None
        except requests.exceptions.Timeout as e:
            last_error = e
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt))
                continue
            print("GET ERROR:", url, f"timeout after {retries + 1} attempts: {str(e)}")
            return None
        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt))
                continue
            print("GET ERROR:", url, str(e))
            return None

    if last_error:
        print("GET ERROR:", url, str(last_error))
    return None


def parse_float(value, default=None):
    try:
        if value in (None, "", "null"):
            return default
        return float(value)
    except Exception:
        return default


def normalize_pct(x):
    if x is None:
        return None
    try:
        x = float(x)
        if x > 1000:
            return x / 1000.0
        if x > 100:
            return x / 100.0
        return x
    except Exception:
        return None


def avg(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def clamp(value, min_value, max_value):
    return max(min_value, min(value, max_value))


def format_num(value, decimals=2, suffix=""):
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.{decimals}f}{suffix}"
    except Exception:
        return "N/A"


def tf_to_binance(tf):
    return tf


def tf_to_cryptometer(tf):
    return {
        "1m": "5m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "1h",
        "4h": "4h",
        "1d": "d",
    }.get(tf, "15m")


def cryptometer_symbol(coin):
    mapping = {
        "BTC": "xbt",
        "ETH": "eth",
        "SOL": "sol",
        "LINK": "link",
        "DOGE": "doge",
        "XRP": "xrp",
        "ADA": "ada",
        "BNB": "bnb",
        "AVAX": "avax",
        "DOT": "dot",
        "LTC": "ltc",
        "BCH": "bch",
        "TRX": "trx",
        "APT": "apt",
        "ARB": "arb",
        "OP": "op",
        "INJ": "inj",
        "TIA": "tia",
        "RUNE": "rune",
        "SUI": "sui",
    }
    return mapping.get(coin.upper(), coin.lower())


def cryptometer_orderbook_symbol(coin):
    return f"{coin.upper()}USDT"


# -------------------------------------------------
# UNIVERSAL COLOR SYSTEM
# -------------------------------------------------

def band_color(score):
    s = clamp(float(score), 0, 100)
    if s < 25:
        return "#64748b", "Weak"
    if s < 50:
        return "#3b82f6", "Building"
    if s < 75:
        return "#22c55e", "Strong"
    return "#a855f7", "Extreme"


def bias_badge(bias):
    color = "#22c55e" if bias == "Bullish" else "#ef4444" if bias == "Bearish" else "#eab308"
    return f'<span class="bias" style="color:{color};">{bias}</span>'


# -------------------------------------------------
# BINANCE FUTURES DATA
# -------------------------------------------------

def get_binance_candles(symbol, tf, limit=25):
    cache_key = f"klines::{symbol}::{tf}::{limit}"
    if cache_key in data_cache:
        return data_cache[cache_key]

    data = safe_get_json(
        "https://fapi.binance.com/fapi/v1/klines",
        params={"symbol": symbol, "interval": tf_to_binance(tf), "limit": limit},
    )

    result = data if isinstance(data, list) else []
    data_cache[cache_key] = result
    return result


def get_binance_funding(symbol):
    cache_key = f"funding::{symbol}"
    if cache_key in data_cache:
        return data_cache[cache_key]

    data = safe_get_json(
        "https://fapi.binance.com/fapi/v1/premiumIndex",
        params={"symbol": symbol},
    )
    value = parse_float(data.get("lastFundingRate"), None) if isinstance(data, dict) else None
    data_cache[cache_key] = value
    return value


def get_binance_oi_change(symbol, tf):
    cache_key = f"oi::{symbol}::{tf}"
    if cache_key in data_cache:
        return data_cache[cache_key]

    data = safe_get_json(
        "https://fapi.binance.com/futures/data/openInterestHist",
        params={"symbol": symbol, "period": tf, "limit": 2},
    )

    result = (None, None)
    if isinstance(data, list) and len(data) >= 2:
        prev_oi = parse_float(data[-2].get("sumOpenInterest"), None)
        last_oi = parse_float(data[-1].get("sumOpenInterest"), None)
        if prev_oi and prev_oi > 0 and last_oi is not None:
            oi_change_pct = ((last_oi - prev_oi) / prev_oi) * 100.0
            result = (oi_change_pct, last_oi)

    data_cache[cache_key] = result
    return result


# -------------------------------------------------
# CRYPTOMETER DATA
# -------------------------------------------------

def get_long_short(coin, tf):
    cache_key = f"longshort::{coin}::{tf}"
    if cache_key in data_cache:
        return data_cache[cache_key]

    if not CRYPTOMETER_API_KEY:
        return None, None

    params = {
        "e": "binance_futures",
        "symbol": cryptometer_symbol(coin),
        "timeframe": tf_to_cryptometer(tf),
        "api_key": CRYPTOMETER_API_KEY,
    }

    data = safe_get_json(
        "https://api.cryptometer.io/long-shorts-data/",
        params=params,
        timeout=CRYPTOMETER_TIMEOUT,
        retries=CRYPTOMETER_RETRIES,
        backoff=CRYPTOMETER_BACKOFF,
    )

    long_pct = None
    short_pct = None

    if isinstance(data, dict):
        if "longs" in data or "shorts" in data:
            long_pct = normalize_pct(parse_float(data.get("longs"), None))
            short_pct = normalize_pct(parse_float(data.get("shorts"), None))
        elif isinstance(data.get("data"), dict):
            inner = data["data"]
            long_pct = normalize_pct(parse_float(inner.get("longs"), None))
            short_pct = normalize_pct(parse_float(inner.get("shorts"), None))
        elif isinstance(data.get("data"), list) and data["data"]:
            inner = data["data"][0]
            if isinstance(inner, dict):
                long_pct = normalize_pct(parse_float(inner.get("longs"), None))
                short_pct = normalize_pct(parse_float(inner.get("shorts"), None))

    result = (long_pct, short_pct)
    if result[0] is not None or result[1] is not None:
        stale_cache[cache_key] = result
    elif cache_key in stale_cache:
        result = stale_cache[cache_key]
        print("LONG/SHORT STALE FALLBACK:", coin, tf)

    data_cache[cache_key] = result
    return result


def get_orderbook(coin):
    cache_key = f"orderbook::{coin}"
    if cache_key in data_cache:
        return data_cache[cache_key]

    if not CRYPTOMETER_API_KEY:
        return None, None

    params = {
        "symbol": cryptometer_orderbook_symbol(coin),
        "api_key": CRYPTOMETER_API_KEY,
    }

    data = safe_get_json(
        "https://api.cryptometer.io/merged-orderbook/",
        params=params,
        timeout=CRYPTOMETER_TIMEOUT,
        retries=CRYPTOMETER_RETRIES,
        backoff=CRYPTOMETER_BACKOFF,
    )

    def sum_book_side(entries):
        if entries is None:
            return 0.0

        if isinstance(entries, (int, float)):
            return float(entries)

        if isinstance(entries, str):
            f = parse_float(entries, None)
            return f if f is not None else 0.0

        if isinstance(entries, dict):
            total = 0.0
            for _, v in entries.items():
                if isinstance(v, (int, float)):
                    total += float(v)
                elif isinstance(v, str):
                    total += parse_float(v, 0.0) or 0.0
                elif isinstance(v, (list, tuple)) and len(v) >= 2:
                    total += parse_float(v[1], 0.0) or 0.0
                elif isinstance(v, dict):
                    total += parse_float(
                        v.get("amount", v.get("qty", v.get("quantity", v.get("size")))),
                        0.0
                    ) or 0.0
            return total

        if isinstance(entries, list):
            total = 0.0
            for x in entries:
                if isinstance(x, (list, tuple)) and len(x) >= 2:
                    total += parse_float(x[1], 0.0) or 0.0
                elif isinstance(x, dict):
                    total += parse_float(
                        x.get("amount", x.get("qty", x.get("quantity", x.get("size")))),
                        0.0
                    ) or 0.0
                elif isinstance(x, (int, float)):
                    total += float(x)
                elif isinstance(x, str):
                    total += parse_float(x, 0.0) or 0.0
            return total

        return 0.0

    bids_total = None
    asks_total = None

    if isinstance(data, dict):
        if "bids" in data or "asks" in data:
            bids_total = sum_book_side(data.get("bids"))
            asks_total = sum_book_side(data.get("asks"))
        elif isinstance(data.get("data"), dict):
            inner = data.get("data", {})
            bids_total = sum_book_side(inner.get("bids"))
            asks_total = sum_book_side(inner.get("asks"))
        elif isinstance(data.get("data"), list) and data["data"]:
            inner = data["data"][0]
            if isinstance(inner, dict):
                bids_total = sum_book_side(inner.get("bids"))
                asks_total = sum_book_side(inner.get("asks"))

    result = (
        bids_total if bids_total not in (None, 0) else None,
        asks_total if asks_total not in (None, 0) else None,
    )

    if result[0] is not None or result[1] is not None:
        stale_cache[cache_key] = result
    elif cache_key in stale_cache:
        result = stale_cache[cache_key]
        print("ORDERBOOK STALE FALLBACK:", coin)

    data_cache[cache_key] = result
    return result


# -------------------------------------------------
# ANALYSIS
# -------------------------------------------------

def analyze_coin(coin, tf):
    coin = coin.upper().strip()
    symbol = f"{coin}USDT"

    candles = get_binance_candles(symbol, tf, limit=25)
    if len(candles) < 21:
        return None

    latest = candles[-1]
    prev_20 = candles[-21:-1]

    open_price = parse_float(latest[1], None)
    high_price = parse_float(latest[2], None)
    low_price = parse_float(latest[3], None)
    close_price = parse_float(latest[4], None)
    quote_volume = parse_float(latest[7], None)

    if None in (open_price, high_price, low_price, close_price, quote_volume):
        return None
    if open_price <= 0 or low_price <= 0:
        return None

    prev_quote_volumes = [parse_float(c[7], None) for c in prev_20]
    prev_ranges_pct = []
    prev_moves_pct = []

    for c in prev_20:
        o = parse_float(c[1], None)
        h = parse_float(c[2], None)
        l = parse_float(c[3], None)
        cl = parse_float(c[4], None)
        if o and o > 0 and l and l > 0 and h is not None and cl is not None:
            prev_moves_pct.append(abs((cl - o) / o) * 100.0)
            prev_ranges_pct.append(((h - l) / l) * 100.0)

    avg_prev_volume = avg(prev_quote_volumes)
    avg_prev_range = avg(prev_ranges_pct)
    avg_prev_move = avg(prev_moves_pct)

    with ThreadPoolExecutor(max_workers=4) as executor:
        funding_future = executor.submit(get_binance_funding, symbol)
        oi_future = executor.submit(get_binance_oi_change, symbol, tf)
        long_short_future = executor.submit(get_long_short, coin, tf)
        orderbook_future = executor.submit(get_orderbook, coin)

        funding_rate = funding_future.result()
        oi_change_pct, open_interest = oi_future.result()
        long_pct, short_pct = long_short_future.result()
        bids_total, asks_total = orderbook_future.result()

    price_change_pct = ((close_price - open_price) / open_price) * 100.0
    volatility_pct = ((high_price - low_price) / low_price) * 100.0

    volume_change_pct = None
    rel_volume = None
    momentum_strength = None
    compression_score = None

    if avg_prev_volume and avg_prev_volume > 0:
        volume_change_pct = ((quote_volume - avg_prev_volume) / avg_prev_volume) * 100.0
        rel_volume = quote_volume / avg_prev_volume

    if avg_prev_move and avg_prev_move > 0:
        momentum_strength = abs(price_change_pct) / avg_prev_move

    if avg_prev_range and avg_prev_range > 0:
        compression_ratio = volatility_pct / avg_prev_range
        compression_score = clamp((1.0 - min(compression_ratio, 1.0)) * 100.0, 0.0, 100.0)

    bias = "Neutral"
    if price_change_pct > 0.15:
        bias = "Bullish"
    elif price_change_pct < -0.15:
        bias = "Bearish"

    momentum_score = clamp((momentum_strength or 0) / 3.0 * 100.0, 0.0, 100.0)
    volume_score = clamp((rel_volume or 0) / 3.0 * 100.0, 0.0, 100.0)

    vol_expansion = None
    if avg_prev_range and avg_prev_range > 0:
        vol_expansion = volatility_pct / avg_prev_range
    volatility_score = clamp((vol_expansion or 0) / 3.0 * 100.0, 0.0, 100.0)

    oi_abs = abs(oi_change_pct or 0)
    oi_score = clamp((oi_abs / 1.0) * 100.0, 0.0, 100.0)

    long_short_score = None
    if long_pct is not None and short_pct is not None:
        long_short_score = clamp(abs(long_pct - 50.0) * 2.0, 0.0, 100.0)

    orderbook_score = None
    orderbook_bias_pct = None
    if bids_total is not None and asks_total is not None:
        total_liq = bids_total + asks_total
        if total_liq > 0:
            bid_share = (bids_total / total_liq) * 100.0
            orderbook_bias_pct = round(bid_share, 1)
            orderbook_score = clamp(abs(bid_share - 50.0) * 2.0, 0.0, 100.0)

    breakout_pressure = 0
    if (compression_score or 0) >= 70:
        breakout_pressure += 25
    if volume_score >= 50:
        breakout_pressure += 25
    if momentum_score >= 50:
        breakout_pressure += 25
    if volatility_score >= 40:
        breakout_pressure += 25
    breakout_pressure = clamp(breakout_pressure, 0.0, 100.0)

    liquidation_pressure = 0
    if (compression_score or 0) >= 65:
        liquidation_pressure += 30
    if volume_score >= 50:
        liquidation_pressure += 25
    if oi_score >= 40:
        liquidation_pressure += 25
    if volatility_score <= 40:
        liquidation_pressure += 20
    liquidation_pressure = clamp(liquidation_pressure, 0.0, 100.0)

    alignment_bonus = 0
    if momentum_score >= 50 and volume_score >= 50 and (compression_score or 0) >= 60:
        alignment_bonus += 8
    if oi_score >= 35 and (long_short_score or 0) >= 35:
        alignment_bonus += 5
    if (orderbook_score or 0) >= 35 and breakout_pressure >= 50:
        alignment_bonus += 5

    setup_score = (
        momentum_score * 0.22 +
        volume_score * 0.18 +
        volatility_score * 0.12 +
        (compression_score or 0) * 0.10 +
        oi_score * 0.08 +
        breakout_pressure * 0.10 +
        liquidation_pressure * 0.10 +
        (long_short_score or 0) * 0.05 +
        (orderbook_score or 0) * 0.05
    ) + alignment_bonus
    setup_score = round(clamp(setup_score, 0.0, 100.0), 1)

    oi_read = "Flat"
    if oi_change_pct is not None:
        if price_change_pct > 0 and oi_change_pct > 0:
            oi_read = "New longs entering"
        elif price_change_pct < 0 and oi_change_pct > 0:
            oi_read = "New shorts entering"
        elif price_change_pct > 0 and oi_change_pct < 0:
            oi_read = "Shorts closing"
        elif price_change_pct < 0 and oi_change_pct < 0:
            oi_read = "Longs closing"

    with trade_flow_lock:
        flow = trade_flow.get(symbol, {"buy": 0.0, "sell": 0.0})
        buy_volume = flow.get("buy", 0.0)
        sell_volume = flow.get("sell", 0.0)

    delta = buy_volume - sell_volume
    total_flow = buy_volume + sell_volume
    flow_bias_pct = None
    flow_score = None

    if total_flow > 0:
        buy_share = (buy_volume / total_flow) * 100.0
        flow_bias_pct = round(buy_share, 1)
        flow_score = clamp(abs(buy_share - 50.0) * 2.0, 0.0, 100.0)

    flow_bias = "Neutral"
    if delta > 0:
        flow_bias = "Buyers"
    elif delta < 0:
        flow_bias = "Sellers"

    return {
        "coin": coin,
        "symbol": symbol,
        "current_price": round(close_price, 6) if close_price is not None else None,
        "bias": bias,
        "price_change_pct": round(price_change_pct, 2),
        "volume_change_pct": round(volume_change_pct, 2) if volume_change_pct is not None else None,
        "volatility_pct": round(volatility_pct, 2),
        "funding_rate": funding_rate,
        "oi_change_pct": round(oi_change_pct, 2) if oi_change_pct is not None else None,
        "oi_read": oi_read,
        "open_interest": open_interest,
        "momentum_strength": round(momentum_strength, 2) if momentum_strength is not None else None,
        "rel_volume": round(rel_volume, 2) if rel_volume is not None else None,
        "compression_score": round(compression_score, 1) if compression_score is not None else None,
        "momentum_score": round(momentum_score, 1),
        "volume_score": round(volume_score, 1),
        "volatility_score": round(volatility_score, 1),
        "oi_score": round(oi_score, 1),
        "breakout_pressure": round(breakout_pressure, 1),
        "liquidation_pressure": round(liquidation_pressure, 1),
        "long_pct": round(long_pct, 1) if long_pct is not None else None,
        "short_pct": round(short_pct, 1) if short_pct is not None else None,
        "long_short_score": round(long_short_score, 1) if long_short_score is not None else None,
        "orderbook_score": round(orderbook_score, 1) if orderbook_score is not None else None,
        "orderbook_bias_pct": orderbook_bias_pct,
        "bids_total": bids_total,
        "asks_total": asks_total,
        "setup_score": setup_score,
        "buy_volume": round(buy_volume, 4),
        "sell_volume": round(sell_volume, 4),
        "delta": round(delta, 4),
        "flow_bias": flow_bias,
        "flow_bias_pct": flow_bias_pct,
        "flow_score": round(flow_score, 1) if flow_score is not None else None,
    }


# -------------------------------------------------
# BAR HTML
# -------------------------------------------------

def metric_bar(score, subtitle, width=150, height=10):
    if score is None:
        return """
        <div class="bar-wrap">
            <div class="bar"><div class="bar-fill" style="width:0;"></div></div>
            <div class="tiny">N/A</div>
        </div>
        """
    v = clamp(float(score), 0, 100)
    fill = (v / 100.0) * width
    color, band = band_color(v)
    return f"""
    <div class="bar-wrap">
        <div class="bar" style="width:{width}px;height:{height}px;">
            <div class="bar-fill" style="width:{fill}px;background:{color};"></div>
        </div>
        <div class="tiny">{band} · {subtitle}</div>
    </div>
    """


def centered_bias_bar(raw_pct, subtitle, width=150, height=10):
    if raw_pct is None:
        return """
        <div class="bar-wrap">
            <div class="bar centered"><div class="bar-mid"></div></div>
            <div class="tiny">N/A</div>
        </div>
        """

    raw = clamp(float(raw_pct), 0.0, 100.0)
    delta = raw - 50.0
    max_abs = 50.0
    half = width / 2
    fill = (abs(delta) / max_abs) * half
    left = half if delta >= 0 else half - fill
    color = "#22c55e" if delta >= 0 else "#ef4444"

    return f"""
    <div class="bar-wrap">
        <div class="bar centered" style="width:{width}px;height:{height}px;">
            <div class="bar-mid"></div>
            <div class="bar-fill abs" style="left:{left}px;width:{fill}px;background:{color};"></div>
        </div>
        <div class="tiny">{subtitle}</div>
    </div>
    """


def oi_centered_bar(score, raw_value, oi_read, width=150, height=10):
    v = clamp(float(score or 0), 0, 100)
    color, band = band_color(v)

    raw = raw_value or 0.0
    max_abs = 1.0
    clipped = clamp(float(raw), -max_abs, max_abs)

    half = width / 2
    fill = (abs(clipped) / max_abs) * half
    left = half if clipped >= 0 else half - fill

    arrow = "▲" if raw > 0 else "▼" if raw < 0 else "•"
    meaning = "Opening" if raw > 0 else "Closing" if raw < 0 else "Flat"

    return f"""
    <div class="bar-wrap">
        <div class="bar centered" style="width:{width}px;height:{height}px;">
            <div class="bar-mid"></div>
            <div class="bar-fill abs" style="left:{left}px;width:{fill}px;background:{color};"></div>
        </div>
        <div class="tiny">{band} · {arrow} {format_num(raw, 2, '%')} · {meaning} · {oi_read}</div>
    </div>
    """


# -------------------------------------------------
# RENDER
# -------------------------------------------------

def render_coin_row(coin, tf, index):
    row = analyze_coin(coin, tf)

    if row is None:
        return f"""
        <div class="coin-card" id="coin-card-{coin}">
            <div class="coin-header">
                <div>
                    <div class="coin-name">{index}. {coin}</div>
                    <div class="coin-sub muted">No data returned</div>
                </div>
                <div><a class="danger-link" href="/remove?coin={coin}&tf={tf}">Remove</a></div>
            </div>
        </div>
        """

    momentum_sub = f"{format_num(row['momentum_strength'], 2)}x normal" if row["momentum_strength"] is not None else "N/A"
    volume_sub = f"{format_num(row['rel_volume'], 2)}x normal" if row["rel_volume"] is not None else "N/A"
    vol_sub = format_num(row["volatility_pct"], 2, "%")
    comp_sub = format_num(row["compression_score"], 0, "%") if row["compression_score"] is not None else "N/A"
    setup_sub = format_num(row["setup_score"], 1)
    break_sub = format_num(row["breakout_pressure"], 0, "%")
    liq_sub = format_num(row["liquidation_pressure"], 0, "%")
    price_sub = format_num(row["price_change_pct"], 2, "%")

    long_short_sub = "N/A"
    if row["long_pct"] is not None and row["short_pct"] is not None:
        long_short_sub = f"{format_num(row['long_pct'], 1, '%')} Long / {format_num(row['short_pct'], 1, '%')} Short"

    orderbook_sub = "N/A"
    if row["orderbook_bias_pct"] is not None:
        if row["orderbook_bias_pct"] > 50:
            orderbook_sub = f"{format_num(row['orderbook_bias_pct'], 1, '%')} bids"
        elif row["orderbook_bias_pct"] < 50:
            orderbook_sub = f"{format_num(100 - row['orderbook_bias_pct'], 1, '%')} asks"
        else:
            orderbook_sub = "Balanced"

    flow_sub = "N/A"
    if row["flow_bias_pct"] is not None:
        flow_sub = (
            f"{row['flow_bias']} · Δ {format_num(row['delta'], 4)} · "
            f"Buy {format_num(row['buy_volume'], 4)} / Sell {format_num(row['sell_volume'], 4)}"
        )

    return f"""
    <div class="coin-card" id="coin-card-{coin}">
        <div class="coin-header">
            <div>
                <div class="coin-name">{index}. {row['coin']} · ${format_num(row['current_price'], 4)}</div>
                <div class="coin-sub">{bias_badge(row['bias'])} · Price {price_sub}</div>
            </div>
            <div class="coin-actions">
                <a class="danger-link" href="/remove?coin={row['coin']}&tf={tf}">Remove</a>
            </div>
        </div>

        <div class="bars-grid">
            <div class="metric">
                <div class="metric-title">⚡ Momentum</div>
                {metric_bar(row["momentum_score"], momentum_sub)}
            </div>

            <div class="metric">
                <div class="metric-title">🐋 Volume</div>
                {metric_bar(row["volume_score"], volume_sub)}
            </div>

            <div class="metric">
                <div class="metric-title">🔥 Volatility</div>
                {metric_bar(row["volatility_score"], vol_sub)}
            </div>

            <div class="metric">
                <div class="metric-title">📦 Compression</div>
                {metric_bar(row["compression_score"], comp_sub)}
            </div>

            <div class="metric">
                <div class="metric-title">🧲 OI Flow / OI Read</div>
                {oi_centered_bar(row["oi_score"], row["oi_change_pct"], row["oi_read"])}
            </div>

            <div class="metric">
                <div class="metric-title">🚀 Breakout Pressure</div>
                {metric_bar(row["breakout_pressure"], break_sub)}
            </div>

            <div class="metric">
                <div class="metric-title">💣 Liquidation Pressure</div>
                {metric_bar(row["liquidation_pressure"], liq_sub)}
            </div>

            <div class="metric">
                <div class="metric-title">🪖 Long vs Short</div>
                {centered_bias_bar(row["long_pct"], long_short_sub)}
            </div>

            <div class="metric">
                <div class="metric-title">📚 Orderbook Imbalance</div>
                {centered_bias_bar(row["orderbook_bias_pct"], orderbook_sub)}
            </div>

            <div class="metric">
                <div class="metric-title">📊 Trade Flow</div>
                {centered_bias_bar(row["flow_bias_pct"], flow_sub)}
            </div>

            <div class="metric">
                <div class="metric-title">⭐ Setup Score</div>
                {metric_bar(row["setup_score"], setup_sub)}
            </div>
        </div>
    </div>
    """


# -------------------------------------------------
# LAYOUT
# -------------------------------------------------

def base_layout(title, body):
    return f"""
    <html>
    <head>
        <title>{title}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <style>
            body {{
                font-family: Arial, sans-serif;
                background: #0f172a;
                color: white;
                padding: 20px;
                margin: 0;
            }}
            h1 {{
                color: #38bdf8;
                margin-bottom: 10px;
            }}
            .card {{
                background: #1e293b;
                border: 1px solid #334155;
                border-radius: 14px;
                padding: 16px;
                margin-bottom: 16px;
            }}
            .controls {{
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                align-items: center;
            }}
            input, select, button {{
                padding: 9px 12px;
                border-radius: 10px;
                border: 1px solid #334155;
                background: #0f172a;
                color: white;
            }}
            button {{
                cursor: pointer;
            }}
            .muted {{
                color: #94a3b8;
            }}
            .coin-list {{
                display: flex;
                flex-direction: column;
                gap: 14px;
            }}
            .coin-card {{
                background: #1e293b;
                border: 1px solid #334155;
                border-radius: 16px;
                padding: 16px;
            }}
            .coin-header {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 12px;
                margin-bottom: 14px;
            }}
            .coin-name {{
                font-size: 20px;
                font-weight: 700;
            }}
            .coin-sub {{
                color: #cbd5e1;
                font-size: 13px;
                margin-top: 4px;
            }}
            .danger-link {{
                color: #f87171;
                font-weight: 700;
                text-decoration: none;
            }}
            .bars-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
                gap: 14px;
            }}
            .metric {{
                background: #0f172a;
                border: 1px solid #334155;
                border-radius: 12px;
                padding: 12px;
            }}
            .metric-title {{
                font-size: 13px;
                color: #cbd5e1;
                font-weight: 700;
                margin-bottom: 8px;
            }}
            .bar-wrap {{
                display: flex;
                flex-direction: column;
                gap: 6px;
            }}
            .bar {{
                position: relative;
                background: #020617;
                border: 1px solid #334155;
                border-radius: 999px;
                overflow: hidden;
                width: 150px;
                height: 10px;
            }}
            .bar.centered {{
                background: linear-gradient(
                    to right,
                    rgba(239,68,68,0.08) 0%,
                    rgba(2,6,23,1) 50%,
                    rgba(34,197,94,0.08) 100%
                );
            }}
            .bar-mid {{
                position: absolute;
                left: 50%;
                top: 0;
                width: 1px;
                height: 100%;
                background: #64748b;
                z-index: 2;
            }}
            .bar-fill {{
                position: relative;
                height: 100%;
                border-radius: 999px;
                transition: width 0.45s ease, background-color 0.35s ease;
            }}
            .bar-fill.abs {{
                position: absolute;
                top: 0;
                transition: width 0.45s ease, left 0.45s ease, background-color 0.35s ease;
            }}
            .tiny {{
                font-size: 11px;
                color: #ffffff;
                font-weight: 600;
                text-transform: uppercase;
            }}
            .bias {{
                font-weight: 700;
            }}
            .status-row {{
                display: flex;
                flex-wrap: wrap;
                gap: 14px;
                font-size: 12px;
                color: #94a3b8;
                margin-top: 8px;
            }}
        </style>
    </head>
    <body>
        {body}
    </body>
    </html>
    """


# -------------------------------------------------
# ROUTES
# -------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home():
    return RedirectResponse(url="/favorites")


@app.get("/favorites", response_class=HTMLResponse)
def favorites_page(tf: str = Query("15m")):
    if tf not in TIMEFRAME_OPTIONS:
        tf = "15m"

    rows_html = ""
    for i, coin in enumerate(favorites, start=1):
        rows_html += render_coin_row(coin, tf, i)

    body = f"""
        <h1>⭐ Favorites Watchlist</h1>
        <div class="muted" style="margin-bottom:12px;">Background refresh updates one coin at a time.</div>

        <div class="card">
            <form class="controls" method="get" action="/favorites">
                <label>Timeframe:</label>
                <select name="tf">
                    {"".join([f'<option value="{x}" {"selected" if x == tf else ""}>{x}</option>' for x in TIMEFRAME_OPTIONS])}
                </select>
                <button type="submit">Refresh View</button>
            </form>

            <form class="controls" method="get" action="/add" style="margin-top:10px;">
                <input name="coin" placeholder="BTC, ETH, SOL" />
                <input type="hidden" name="tf" value="{tf}" />
                <button type="submit">Add Coin</button>
            </form>

            <div class="status-row">
                <div>Gray = weak</div>
                <div>Blue = building</div>
                <div>Green = strong</div>
                <div>Purple = extreme</div>
            </div>
        </div>

        <div class="coin-list" id="coin-list">
            {rows_html if rows_html else '<div class="card">No favorites yet. Add a coin above.</div>'}
        </div>

        <script>
            const favorites = {favorites};
            const timeframe = "{tf}";
            let refreshIndex = 0;

            async function refreshOneCoin() {{
                if (!favorites.length) return;

                const coin = favorites[refreshIndex % favorites.length];
                refreshIndex += 1;

                try {{
                    const res = await fetch(`/api/coin?coin=${{encodeURIComponent(coin)}}&tf=${{encodeURIComponent(timeframe)}}`);
                    if (!res.ok) return;

                    const html = await res.text();
                    const wrapper = document.createElement("div");
                    wrapper.innerHTML = html.trim();

                    const newCard = wrapper.firstElementChild;
                    const oldCard = document.getElementById(`coin-card-${{coin}}`);

                    if (oldCard && newCard) {{
                        oldCard.replaceWith(newCard);
                    }}
                }} catch (e) {{
                    console.log("refresh error", coin, e);
                }}
            }}

            setInterval(refreshOneCoin, 2000);
        </script>
    """
    return base_layout("Favorites Watchlist", body)


@app.get("/api/coin", response_class=HTMLResponse)
def api_coin(coin: str = Query(...), tf: str = Query("15m")):
    coin = coin.upper().strip()
    if tf not in TIMEFRAME_OPTIONS:
        tf = "15m"

    index = favorites.index(coin) + 1 if coin in favorites else 1
    return HTMLResponse(render_coin_row(coin, tf, index))


@app.get("/add")
def add_coin(coin: str = Query(...), tf: str = Query("15m")):
    coin = coin.upper().strip()
    if coin and coin not in favorites:
        favorites.append(coin)
        start_trade_stream(f"{coin}USDT")
    return RedirectResponse(url=f"/favorites?tf={tf}", status_code=302)


@app.get("/remove")
def remove_coin(coin: str = Query(...), tf: str = Query("15m")):
    coin = coin.upper().strip()
    if coin in favorites:
        favorites.remove(coin)
    return RedirectResponse(url=f"/favorites?tf={tf}", status_code=302)


@app.get("/debug", response_class=JSONResponse)
def debug(coin: str = Query("BTC"), tf: str = Query("15m")):
    return {
        "favorites": favorites,
        "timeframe": tf,
        "cryptometer_key_present": bool(CRYPTOMETER_API_KEY),
        "trade_flow": trade_flow.get(f"{coin.upper().strip()}USDT", {"buy": 0.0, "sell": 0.0}),
        "coin_result": analyze_coin(coin.upper().strip(), tf),
    }
