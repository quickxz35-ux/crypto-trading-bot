import os
import requests
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from cachetools import TTLCache

app = FastAPI()

# -----------------------------
# CACHE
# -----------------------------

cache = TTLCache(maxsize=200, ttl=60)

# -----------------------------
# SAFE REQUEST
# -----------------------------

def safe_get_json(url, params=None):
    try:
        r = requests.get(url, params=params, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


# -----------------------------
# BINANCE SPOT DATA
# -----------------------------

def get_binance_symbols():

    cached = cache.get("symbols")
    if cached:
        return cached

    data = safe_get_json("https://api.binance.com/api/v3/exchangeInfo")

    symbols = []

    if isinstance(data, dict):
        for item in data.get("symbols", []):

            if (
                item.get("quoteAsset") == "USDT"
                and item.get("status") == "TRADING"
            ):
                symbols.append(item.get("symbol"))

    cache["symbols"] = symbols
    return symbols


def get_ticker_24h(symbol):

    return safe_get_json(
        "https://api.binance.com/api/v3/ticker/24hr",
        {"symbol": symbol},
    )


def get_klines(symbol, interval="1h", limit=2):

    return safe_get_json(
        "https://api.binance.com/api/v3/klines",
        {"symbol": symbol, "interval": interval, "limit": limit},
    )


# -----------------------------
# FUNDING PULSE
# -----------------------------

def get_funding_pulse():

    cached = cache.get("funding")
    if cached:
        return cached

    data = safe_get_json("https://fundingpulse.com/api/overview")

    result = {}

    if isinstance(data, list):
        for item in data:

            symbol = item.get("symbol")

            if symbol:
                result[symbol.upper()] = {
                    "oi": item.get("openInterest"),
                    "ls": item.get("longShortRatio"),
                    "funding": item.get("fundingRate"),
                }

    cache["funding"] = result
    return result


# -----------------------------
# SCORING
# -----------------------------

def compute_score(price_change, volume_change, long_short):

    score = 0

    if price_change:
        score += price_change * 10

    if volume_change:
        score += volume_change * 5

    if long_short:
        score += (long_short - 1) * 50

    return round(score, 2)


# -----------------------------
# BUILD ROW
# -----------------------------

def build_row(symbol):

    ticker = get_ticker_24h(symbol)

    if not ticker:
        return None

    klines = get_klines(symbol)

    price_change = float(ticker.get("priceChangePercent", 0))

    volume_change = None

    if klines and len(klines) >= 2:

        prev_vol = float(klines[0][5])
        last_vol = float(klines[1][5])

        if prev_vol > 0:
            volume_change = ((last_vol - prev_vol) / prev_vol) * 100

    funding = get_funding_pulse()

    deriv = funding.get(symbol)

    long_short = None
    oi = None

    if deriv:
        long_short = deriv.get("ls")
        oi = deriv.get("oi")

    score = compute_score(price_change, volume_change, long_short)

    return {
        "symbol": symbol.replace("USDT", ""),
        "price": ticker.get("lastPrice"),
        "price_change": price_change,
        "volume_change": volume_change,
        "long_short": long_short,
        "open_interest": oi,
        "score": score,
    }


# -----------------------------
# SCANNER
# -----------------------------

def scan():

    symbols = get_binance_symbols()

    rows = []

    for symbol in symbols[:80]:

        row = build_row(symbol)

        if row:
            rows.append(row)

    rows.sort(key=lambda x: x["score"], reverse=True)

    return rows[:10]


# -----------------------------
# DASHBOARD
# -----------------------------

@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():

    rows = scan()

    html_rows = ""

    for r in rows:

        html_rows += f"""
        <tr>
        <td>{r['symbol']}</td>
        <td>{r['price']}</td>
        <td>{round(r['price_change'],2)}%</td>
        <td>{round(r['volume_change'],2) if r['volume_change'] else "N/A"}%</td>
        <td>{r['long_short'] if r['long_short'] else "N/A"}</td>
        <td>{r['score']}</td>
        </tr>
        """

    return f"""
    <html>
    <head>
    <title>Crypto Scanner</title>
    <style>

    body {{
        font-family: Arial;
        background:#0f172a;
        color:white;
        padding:30px;
    }}

    table {{
        width:100%;
        border-collapse:collapse;
        background:#1e293b;
    }}

    th,td {{
        padding:10px;
        border:1px solid #334155;
    }}

    th {{
        background:#334155;
    }}

    </style>
    </head>

    <body>

    <h1>Crypto Opportunity Scanner</h1>

    <table>

    <tr>
    <th>Coin</th>
    <th>Price</th>
    <th>Price %</th>
    <th>Volume %</th>
    <th>Long/Short</th>
    <th>Score</th>
    </tr>

    {html_rows}

    </table>

    </body>
    </html>
    """


# -----------------------------
# DEBUG
# -----------------------------

@app.get("/debug")
def debug():

    return {
        "symbols_loaded": len(get_binance_symbols()),
        "funding_items": len(get_funding_pulse()),
        "cache_items": len(cache),
    }
