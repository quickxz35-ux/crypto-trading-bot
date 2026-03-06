import asyncio
import os
import requests
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, RedirectResponse

app = FastAPI()

ALTFINS_KEY = os.getenv("ALTFINS_API_KEY")

latest_top_10 = []
favorites = []


def parse_number(value):
    if value is None:
        return 0.0
    text = str(value).replace(",", "").replace("%", "").strip()
    if text == "":
        return 0.0
    try:
        return float(text)
    except Exception:
        return 0.0


def safe_get_json(url, params=None, headers=None, timeout=20):
    try:
        r = requests.get(url, params=params, headers=headers, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def safe_post_json(url, json_body=None, headers=None, timeout=20):
    try:
        r = requests.post(url, json=json_body, headers=headers, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def score_signal(item):
    score = 0

    direction = item.get("direction", "")
    signal_name = item.get("signalName", "")
    price_change = parse_number(item.get("priceChange"))
    market_cap = parse_number(item.get("marketCap"))

    if direction != "BULLISH":
        return -999

    if price_change <= 0:
        return -999

    if market_cap < 25000000:
        return -999

    score += 50
    score += min(price_change * 5, 25)

    if market_cap > 500000000:
        score += 20
    elif market_cap > 100000000:
        score += 15
    else:
        score += 10

    if "Bull Power" in signal_name:
        score += 15

    if "Oversold" in signal_name:
        score += 12

    return score


def get_binance_spot_24h(symbol):
    return safe_get_json(
        "https://api.binance.com/api/v3/ticker/24hr",
        params={"symbol": symbol}
    )


def get_binance_spot_klines(symbol, interval="5m", limit=3):
    return safe_get_json(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit}
    )


def get_futures_24h(symbol):
    return safe_get_json(
        "https://fapi.binance.com/fapi/v1/ticker/24hr",
        params={"symbol": symbol}
    )


def get_open_interest(symbol):
    return safe_get_json(
        "https://fapi.binance.com/fapi/v1/openInterest",
        params={"symbol": symbol}
    )


def get_long_short_ratio(symbol, period="5m", limit=2):
    return safe_get_json(
        "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
        params={"symbol": symbol, "period": period, "limit": limit}
    )


def get_top_trader_ratio(symbol, period="5m", limit=2):
    return safe_get_json(
        "https://fapi.binance.com/futures/data/topLongShortPositionRatio",
        params={"symbol": symbol, "period": period, "limit": limit}
    )


def get_taker_buy_sell_volume(symbol, period="5m", limit=2):
    return safe_get_json(
        "https://fapi.binance.com/futures/data/takerlongshortRatio",
        params={"symbol": symbol, "period": period, "limit": limit}
    )


def get_altfins_signal_map():
    data = safe_post_json(
        "https://altfins.com/api/v2/public/signals-feed/search-requests",
        json_body={
            "timeRange": {"from": "now-2h", "to": "now"},
            "direction": "BULLISH"
        },
        headers={"X-API-KEY": ALTFINS_KEY},
        timeout=20
    )

    signal_map = {}
    if isinstance(data, dict):
        for item in data.get("content", []):
            symbol = item.get("symbol")
            if symbol and symbol not in signal_map:
                signal_map[symbol.upper()] = item
    return signal_map


def compute_volume_change_percent(klines):
    if not isinstance(klines, list) or len(klines) < 2:
        return 0.0

    prev_vol = parse_number(klines[-2][5])
    last_vol = parse_number(klines[-1][5])

    if prev_vol <= 0:
        return 0.0

    return ((last_vol - prev_vol) / prev_vol) * 100.0


def compute_price_change_5m(klines):
    if not isinstance(klines, list) or len(klines) < 1:
        return 0.0

    open_price = parse_number(klines[-1][1])
    close_price = parse_number(klines[-1][4])

    if open_price <= 0:
        return 0.0

    return ((close_price - open_price) / open_price) * 100.0


def compute_taker_buy_ratio(taker_data):
    if not isinstance(taker_data, list) or len(taker_data) < 1:
        return 0.0

    row = taker_data[-1]
    buy = parse_number(row.get("buySellRatio"))
    return buy


def compute_long_short_ratio(ls_data):
    if not isinstance(ls_data, list) or len(ls_data) < 1:
        return 0.0

    return parse_number(ls_data[-1].get("longShortRatio"))


def compute_top_trader_ratio(top_data):
    if not isinstance(top_data, list) or len(top_data) < 1:
        return 0.0

    return parse_number(top_data[-1].get("longShortRatio"))


def get_coin_metrics(base_symbol):
    spot_symbol = f"{base_symbol.upper()}USDT"

    spot24 = get_binance_spot_24h(spot_symbol)
    klines = get_binance_spot_klines(spot_symbol, interval="5m", limit=3)

    futures24 = get_futures_24h(spot_symbol)
    oi = get_open_interest(spot_symbol)
    ls = get_long_short_ratio(spot_symbol, period="5m", limit=2)
    top_ls = get_top_trader_ratio(spot_symbol, period="5m", limit=2)
    taker = get_taker_buy_sell_volume(spot_symbol, period="5m", limit=2)

    last_price = "N/A"
    price_change_24h = "N/A"
    volume_24h = "N/A"

    if isinstance(spot24, dict) and spot24.get("lastPrice"):
        last_price = spot24.get("lastPrice")
        price_change_24h = spot24.get("priceChangePercent")
        volume_24h = spot24.get("volume")

    if last_price == "N/A" and isinstance(futures24, dict):
        last_price = futures24.get("lastPrice", "N/A")
        price_change_24h = futures24.get("priceChangePercent", "N/A")
        volume_24h = futures24.get("volume", "N/A")

    volume_change_5m = round(compute_volume_change_percent(klines), 2)
    price_change_5m = round(compute_price_change_5m(klines), 2)

    oi_value = "N/A"
    if isinstance(oi, dict):
        oi_value = oi.get("openInterest", "N/A")

    long_short = round(compute_long_short_ratio(ls), 3)
    top_ratio = round(compute_top_trader_ratio(top_ls), 3)
    taker_ratio = round(compute_taker_buy_ratio(taker), 3)

    volume_explosion_score = 0
    if price_change_5m > 0:
        volume_explosion_score += min(price_change_5m * 15, 25)
    if volume_change_5m > 0:
        volume_explosion_score += min(volume_change_5m * 0.6, 35)
    if taker_ratio > 1:
        volume_explosion_score += min((taker_ratio - 1) * 40, 20)
    if long_short > 1:
        volume_explosion_score += min((long_short - 1) * 25, 10)
    if top_ratio > 1:
        volume_explosion_score += min((top_ratio - 1) * 20, 10)

    volume_explosion_score = round(volume_explosion_score, 2)

    smart_money_label = "Neutral"
    if top_ratio >= 1.2 and taker_ratio >= 1.05:
        smart_money_label = "Bullish"
    elif top_ratio <= 0.9 and taker_ratio <= 0.95:
        smart_money_label = "Bearish"

    entry_bias = "Wait"
    if price_change_5m > 0.25 and volume_change_5m > 15 and taker_ratio > 1.05:
        entry_bias = "Breakout watch"
    if price_change_5m < 0.15 and volume_change_5m > 15 and taker_ratio > 1.05:
        entry_bias = "Pullback watch"

    return {
        "symbol": base_symbol.upper(),
        "pair": spot_symbol,
        "last_price": last_price,
        "price_change_24h": price_change_24h,
        "volume_24h": volume_24h,
        "price_change_5m": price_change_5m,
        "volume_change_5m": volume_change_5m,
        "open_interest": oi_value,
        "long_short_ratio": long_short,
        "top_trader_ratio": top_ratio,
        "taker_ratio": taker_ratio,
        "volume_explosion_score": volume_explosion_score,
        "smart_money_label": smart_money_label,
        "entry_bias": entry_bias,
    }


async def altfins_worker():
    global latest_top_10

    while True:
        try:
            data = safe_post_json(
                "https://altfins.com/api/v2/public/signals-feed/search-requests",
                json_body={
                    "timeRange": {"from": "now-2h", "to": "now"},
                    "direction": "BULLISH"
                },
                headers={"X-API-KEY": ALTFINS_KEY},
                timeout=20
            )

            items = []
            if isinstance(data, dict):
                items = data.get("content", [])

            scored = []
            for item in items:
                score = score_signal(item)
                if score > 0:
                    item["score"] = round(score, 2)
                    scored.append(item)

            latest_top_10 = sorted(
                scored,
                key=lambda x: x["score"],
                reverse=True
            )[:10]

        except Exception as e:
            print("Worker error:", e)

        await asyncio.sleep(600)


@app.on_event("startup")
async def start_worker():
    asyncio.create_task(altfins_worker())


@app.get("/")
def home():
    return RedirectResponse(url="/dashboard")


@app.get("/favorite")
def add_favorite(
    symbol: str = Query(...),
    signal: str = Query(""),
    change: str = Query(""),
    market_cap: str = Query(""),
    score: str = Query("")
):
    global favorites

    exists = any(item["symbol"] == symbol.upper() for item in favorites)
    if not exists:
        favorites.append({
            "symbol": symbol.upper(),
            "signal": signal,
            "priceChange": change,
            "marketCap": market_cap,
            "score": score
        })

    return RedirectResponse(url="/favorites", status_code=302)


@app.get("/remove_favorite")
def remove_favorite(symbol: str = Query(...)):
    global favorites
    favorites = [item for item in favorites if item["symbol"] != symbol.upper()]
    return RedirectResponse(url="/favorites", status_code=302)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    rows = ""

    for i, item in enumerate(latest_top_10, start=1):
        symbol = item.get("symbol", "")
        rows += f"""
        <tr>
            <td>{i}</td>
            <td>{symbol}</td>
            <td style="color:#16a34a;font-weight:bold;">{item.get('direction')}</td>
            <td>{item.get('signalName')}</td>
            <td>{item.get('priceChange')}</td>
            <td>{item.get('marketCap')}</td>
            <td>{item.get('score')}</td>
            <td><a href="/analyze?symbol={symbol}" style="color:#38bdf8;">Analyze</a></td>
            <td>
                <a href="/favorite?symbol={symbol}&signal={item.get('signalName')}&change={item.get('priceChange')}&market_cap={item.get('marketCap')}&score={item.get('score')}"
                   style="color:#facc15;text-decoration:none;font-weight:bold;">⭐ Add</a>
            </td>
        </tr>
        """

    return f"""
    <html>
    <head>
        <title>Crypto Dashboard</title>
        <meta http-equiv="refresh" content="30">
        <style>
            body {{ font-family: Arial, sans-serif; background: #0f172a; color: white; padding: 20px; }}
            h1 {{ color: #38bdf8; }}
            .nav a {{ margin-right: 20px; color: #38bdf8; text-decoration: none; font-weight: bold; }}
            table {{ width: 100%; border-collapse: collapse; background: #1e293b; }}
            th, td {{ padding: 12px; border: 1px solid #334155; text-align: left; }}
            th {{ background: #334155; }}
            tr:hover {{ background: #273549; }}
            input {{ padding: 10px; width: 240px; }}
            button {{ padding: 10px 16px; }}
        </style>
    </head>
    <body>
        <h1>Top 10 Crypto Opportunities</h1>
        <div class="nav">
            <a href="/dashboard">📊 New Scan</a>
            <a href="/favorites">⭐ Favorites</a>
            <a href="/volume-explosions">⚡ Volume Explosions</a>
        </div>
        <form action="/analyze" method="get" style="margin:20px 0;">
            <input name="symbol" placeholder="Enter coin, e.g. BTC or SOL" />
            <button type="submit">Analyze</button>
        </form>
        <p>Auto-refreshes every 30 seconds</p>
        <table>
            <tr>
                <th>Rank</th>
                <th>Coin</th>
                <th>Direction</th>
                <th>Signal</th>
                <th>Price Change</th>
                <th>Market Cap</th>
                <th>Score</th>
                <th>Analyze</th>
                <th>Favorite</th>
            </tr>
            {rows}
        </table>
    </body>
    </html>
    """


@app.get("/favorites", response_class=HTMLResponse)
def favorites_page():
    rows = ""

    for i, item in enumerate(favorites, start=1):
        m = get_coin_metrics(item.get("symbol"))
        smart_color = "#16a34a" if m["smart_money_label"] == "Bullish" else "#f87171" if m["smart_money_label"] == "Bearish" else "#eab308"

        rows += f"""
        <tr>
            <td>{i}</td>
            <td>{item.get('symbol')}</td>
            <td>{m.get('last_price')}</td>
            <td>{m.get('price_change_5m')}%</td>
            <td>{m.get('volume_change_5m')}%</td>
            <td>{m.get('taker_ratio')}</td>
            <td>{m.get('long_short_ratio')}</td>
            <td>{m.get('top_trader_ratio')}</td>
            <td style="color:{smart_color};font-weight:bold;">{m.get('smart_money_label')}</td>
            <td>{m.get('entry_bias')}</td>
            <td>{m.get('volume_explosion_score')}</td>
            <td><a href="/analyze?symbol={item.get('symbol')}" style="color:#38bdf8;">Analyze</a></td>
            <td><a href="/remove_favorite?symbol={item.get('symbol')}" style="color:#f87171;">Remove</a></td>
        </tr>
        """

    return f"""
    <html>
    <head>
        <title>Favorites</title>
        <meta http-equiv="refresh" content="30">
        <style>
            body {{ font-family: Arial, sans-serif; background: #0f172a; color: white; padding: 20px; }}
            h1 {{ color: #facc15; }}
            .nav a {{ margin-right: 20px; color: #38bdf8; text-decoration: none; font-weight: bold; }}
            table {{ width: 100%; border-collapse: collapse; background: #1e293b; }}
            th, td {{ padding: 12px; border: 1px solid #334155; text-align: left; }}
            th {{ background: #334155; }}
            tr:hover {{ background: #273549; }}
        </style>
    </head>
    <body>
        <h1>⭐ Favorite Coins</h1>
        <div class="nav">
            <a href="/dashboard">📊 New Scan</a>
            <a href="/favorites">⭐ Favorites</a>
            <a href="/volume-explosions">⚡ Volume Explosions</a>
        </div>
        <table>
            <tr>
                <th>#</th>
                <th>Coin</th>
                <th>Price</th>
                <th>5m %</th>
                <th>5m Vol %</th>
                <th>Taker</th>
                <th>L/S</th>
                <th>Top Trader</th>
                <th>Smart Money</th>
                <th>Entry Bias</th>
                <th>Explosion</th>
                <th>Analyze</th>
                <th>Action</th>
            </tr>
            {rows if rows else "<tr><td colspan='13'>No favorites yet.</td></tr>"}
        </table>
    </body>
    </html>
    """


@app.get("/volume-explosions", response_class=HTMLResponse)
def volume_explosions():
    rows = ""

    ranked = []
    for item in latest_top_10:
        symbol = item.get("symbol")
        m = get_coin_metrics(symbol)
        ranked.append(m)

    ranked = sorted(ranked, key=lambda x: x["volume_explosion_score"], reverse=True)

    for i, m in enumerate(ranked, start=1):
        rows += f"""
        <tr>
            <td>{i}</td>
            <td>{m.get('symbol')}</td>
            <td>{m.get('last_price')}</td>
            <td>{m.get('price_change_5m')}%</td>
            <td>{m.get('volume_change_5m')}%</td>
            <td>{m.get('taker_ratio')}</td>
            <td>{m.get('long_short_ratio')}</td>
            <td>{m.get('top_trader_ratio')}</td>
            <td>{m.get('volume_explosion_score')}</td>
            <td>{m.get('entry_bias')}</td>
            <td><a href="/analyze?symbol={m.get('symbol')}" style="color:#38bdf8;">Analyze</a></td>
        </tr>
        """

    return f"""
    <html>
    <head>
        <title>Volume Explosions</title>
        <meta http-equiv="refresh" content="30">
        <style>
            body {{ font-family: Arial, sans-serif; background: #0f172a; color: white; padding: 20px; }}
            h1 {{ color: #f97316; }}
            .nav a {{ margin-right: 20px; color: #38bdf8; text-decoration: none; font-weight: bold; }}
            table {{ width: 100%; border-collapse: collapse; background: #1e293b; }}
            th, td {{ padding: 12px; border: 1px solid #334155; text-align: left; }}
            th {{ background: #334155; }}
        </style>
    </head>
    <body>
        <h1>⚡ Volume Explosion Scanner</h1>
        <div class="nav">
            <a href="/dashboard">📊 New Scan</a>
            <a href="/favorites">⭐ Favorites</a>
            <a href="/volume-explosions">⚡ Volume Explosions</a>
        </div>
        <table>
            <tr>
                <th>Rank</th>
                <th>Coin</th>
                <th>Price</th>
                <th>5m %</th>
                <th>5m Vol %</th>
                <th>Taker</th>
                <th>L/S</th>
                <th>Top Trader</th>
                <th>Explosion</th>
                <th>Entry Bias</th>
                <th>Analyze</th>
            </tr>
            {rows if rows else "<tr><td colspan='11'>No data yet.</td></tr>"}
        </table>
    </body>
    </html>
    """


@app.get("/analyze", response_class=HTMLResponse)
def analyze(symbol: str = Query(...)):
    symbol = symbol.upper()
    metrics = get_coin_metrics(symbol)
    signal_map = get_altfins_signal_map()
    alt_signal = signal_map.get(symbol, {})

    signal_name = alt_signal.get("signalName", "N/A")
    signal_direction = alt_signal.get("direction", "N/A")
    signal_change = alt_signal.get("priceChange", "N/A")
    signal_market_cap = alt_signal.get("marketCap", "N/A")

    entry_zone = "Wait for confirmation"
    invalidation = "Below recent 5m low"

    if metrics["entry_bias"] == "Breakout watch":
        entry_zone = "Enter on break of local high with volume confirmation"
        invalidation = "Lose breakout level / weak taker flow"
    elif metrics["entry_bias"] == "Pullback watch":
        entry_zone = "Watch pullback into support / reclaim"
        invalidation = "Break below pullback support"

    return f"""
    <html>
    <head>
        <title>{symbol} Analysis</title>
        <style>
            body {{ font-family: Arial, sans-serif; background: #0f172a; color: white; padding: 20px; }}
            h1 {{ color: #38bdf8; }}
            .card {{ background:#1e293b; padding:20px; margin-bottom:20px; border:1px solid #334155; }}
            .nav a {{ margin-right: 20px; color: #38bdf8; text-decoration: none; font-weight: bold; }}
        </style>
    </head>
    <body>
        <div class="nav">
            <a href="/dashboard">📊 New Scan</a>
            <a href="/favorites">⭐ Favorites</a>
            <a href="/volume-explosions">⚡ Volume Explosions</a>
        </div>

        <h1>{symbol} Trade Analysis</h1>

        <div class="card">
            <h2>Market</h2>
            <p>Pair: {metrics.get('pair')}</p>
            <p>Last Price: {metrics.get('last_price')}</p>
            <p>24h Change: {metrics.get('price_change_24h')}%</p>
            <p>24h Volume: {metrics.get('volume_24h')}</p>
            <p>5m Change: {metrics.get('price_change_5m')}%</p>
            <p>5m Volume Change: {metrics.get('volume_change_5m')}%</p>
        </div>

        <div class="card">
            <h2>Order Flow / Derivatives</h2>
            <p>Open Interest: {metrics.get('open_interest')}</p>
            <p>Global Long/Short Ratio: {metrics.get('long_short_ratio')}</p>
            <p>Top Trader Ratio: {metrics.get('top_trader_ratio')}</p>
            <p>Taker Buy/Sell Ratio: {metrics.get('taker_ratio')}</p>
            <p>Smart Money: {metrics.get('smart_money_label')}</p>
            <p>Volume Explosion Score: {metrics.get('volume_explosion_score')}</p>
        </div>

        <div class="card">
            <h2>altFINS Context</h2>
            <p>Signal Direction: {signal_direction}</p>
            <p>Signal Name: {signal_name}</p>
            <p>Signal Price Change: {signal_change}</p>
            <p>Market Cap: {signal_market_cap}</p>
        </div>

        <div class="card">
            <h2>Entry Guidance</h2>
            <p>Bias: {metrics.get('entry_bias')}</p>
            <p>Entry Idea: {entry_zone}</p>
            <p>Invalidation: {invalidation}</p>
        </div>
    </body>
    </html>
    """
