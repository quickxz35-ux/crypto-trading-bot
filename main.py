import asyncio
import os
import requests
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, RedirectResponse

app = FastAPI()

ALTFINS_KEY = os.getenv("ALTFINS_API_KEY")

SCAN_TIMEFRAMES = ["5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"]
DERIV_TIMEFRAMES = ["5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"]

latest_dashboard_rows = []
favorites = []
binance_futures_symbols = set()


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


def pct_change(current, previous):
    current = parse_number(current)
    previous = parse_number(previous)
    if previous <= 0:
        return None
    return ((current - previous) / previous) * 100.0


def format_num(value, decimals=2, suffix=""):
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.{decimals}f}{suffix}"
    except Exception:
        return "N/A"


def get_binance_futures_symbols():
    data = safe_get_json("https://fapi.binance.com/fapi/v1/exchangeInfo")
    symbols = set()
    if isinstance(data, dict):
        for item in data.get("symbols", []):
            sym = item.get("symbol")
            if sym and item.get("status") == "TRADING":
                symbols.add(sym.upper())
    return symbols


def get_altfins_signals(scan_tf="1h"):
    tf_to_window = {
        "5m": "now-30m",
        "15m": "now-2h",
        "30m": "now-4h",
        "1h": "now-6h",
        "2h": "now-12h",
        "4h": "now-24h",
        "6h": "now-24h",
        "12h": "now-2d",
        "1d": "now-3d",
    }

    from_window = tf_to_window.get(scan_tf, "now-6h")

    data = safe_post_json(
        "https://altfins.com/api/v2/public/signals-feed/search-requests",
        json_body={
            "timeRange": {"from": from_window, "to": "now"},
            "direction": "BULLISH"
        },
        headers={"X-API-KEY": ALTFINS_KEY},
        timeout=20
    )

    if isinstance(data, dict):
        return data.get("content", [])
    return []


def get_spot_klines(symbol, interval="1h", limit=3):
    return safe_get_json(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit}
    )


def get_spot_24h(symbol):
    return safe_get_json(
        "https://api.binance.com/api/v3/ticker/24hr",
        params={"symbol": symbol}
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


def get_open_interest_hist(symbol, period="5m", limit=2):
    return safe_get_json(
        "https://fapi.binance.com/futures/data/openInterestHist",
        params={"symbol": symbol, "period": period, "limit": limit}
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


def get_taker_buy_sell(symbol, period="5m", limit=2):
    return safe_get_json(
        "https://fapi.binance.com/futures/data/takerlongshortRatio",
        params={"symbol": symbol, "period": period, "limit": limit}
    )


def compute_price_change_tf(klines):
    if not isinstance(klines, list) or len(klines) < 1:
        return None
    open_price = parse_number(klines[-1][1])
    close_price = parse_number(klines[-1][4])
    if open_price <= 0:
        return None
    return ((close_price - open_price) / open_price) * 100.0


def compute_volume_change_tf(klines):
    if not isinstance(klines, list) or len(klines) < 2:
        return None
    prev_vol = parse_number(klines[-2][5])
    last_vol = parse_number(klines[-1][5])
    if prev_vol <= 0:
        return None
    return ((last_vol - prev_vol) / prev_vol) * 100.0


def signal_score(item):
    direction = item.get("direction", "")
    signal_name = item.get("signalName", "")
    price_change = parse_number(item.get("priceChange"))
    market_cap = parse_number(item.get("marketCap"))

    if direction != "BULLISH":
        return None
    if price_change <= 0:
        return None
    if market_cap < 25_000_000:
        return None

    score = 50
    score += min(price_change * 5, 20)

    if market_cap > 500_000_000:
        score += 15
    elif market_cap > 100_000_000:
        score += 10
    else:
        score += 5

    if "Bull Power" in signal_name:
        score += 10
    if "Oversold" in signal_name:
        score += 10

    return round(score, 2)


def compute_scalp_score(scan_price_change, scan_volume_change, alt_score):
    score = 0.0

    if scan_price_change is not None and scan_price_change > 0:
        score += min(scan_price_change * 18, 35)

    if scan_volume_change is not None and scan_volume_change > 0:
        score += min(scan_volume_change * 0.7, 35)

    if alt_score is not None:
        score += min(alt_score * 0.35, 30)

    return round(score, 2)


def compute_explosion_score(row):
    volume = row["scan_volume_change"] if row["scan_volume_change"] is not None else 0
    oi = row["oi_change_pct"] if row["oi_change_pct"] is not None else 0
    buy_sell = row["buy_sell_ratio"] if row["buy_sell_ratio"] is not None else 1
    price = row["scan_price_change"] if row["scan_price_change"] is not None else 0

    score = (
        volume * 0.5
        + oi * 20
        + (buy_sell - 1) * 50
        + price * 10
    )
    return round(score, 2)


def is_real_explosion(row):
    volume = row["scan_volume_change"] if row["scan_volume_change"] is not None else 0
    oi = row["oi_change_pct"] if row["oi_change_pct"] is not None else 0
    buy_sell = row["buy_sell_ratio"] if row["buy_sell_ratio"] is not None else 1

    return volume > 200 and oi > 1 and buy_sell > 1.05


def compute_derivatives_metrics(symbol, deriv_tf):
    if symbol not in binance_futures_symbols:
        return {
            "open_interest": "N/A",
            "oi_change_pct": None,
            "buy_sell_ratio": None,
            "buy_sell_change_pct": None,
            "long_short_ratio": None,
            "long_short_change_pct": None,
            "top_trader_ratio": None,
            "smart_money": "N/A",
            "entry_bias": "N/A",
        }

    oi = get_open_interest(symbol)
    oi_hist = get_open_interest_hist(symbol, period=deriv_tf, limit=2)
    taker = get_taker_buy_sell(symbol, period=deriv_tf, limit=2)
    ls = get_long_short_ratio(symbol, period=deriv_tf, limit=2)
    top = get_top_trader_ratio(symbol, period=deriv_tf, limit=2)

    open_interest = "N/A"
    if isinstance(oi, dict):
        open_interest = oi.get("openInterest", "N/A")

    oi_change_pct = None
    if isinstance(oi_hist, list) and len(oi_hist) >= 2:
        prev_oi = parse_number(oi_hist[-2].get("sumOpenInterest"))
        last_oi = parse_number(oi_hist[-1].get("sumOpenInterest"))
        oi_change_pct = pct_change(last_oi, prev_oi)

    buy_sell_ratio = None
    buy_sell_change_pct = None
    if isinstance(taker, list) and len(taker) >= 1:
        buy_sell_ratio = parse_number(taker[-1].get("buySellRatio"))
    if isinstance(taker, list) and len(taker) >= 2:
        prev_val = parse_number(taker[-2].get("buySellRatio"))
        last_val = parse_number(taker[-1].get("buySellRatio"))
        buy_sell_change_pct = pct_change(last_val, prev_val)

    long_short_ratio = None
    long_short_change_pct = None
    if isinstance(ls, list) and len(ls) >= 1:
        long_short_ratio = parse_number(ls[-1].get("longShortRatio"))
    if isinstance(ls, list) and len(ls) >= 2:
        prev_val = parse_number(ls[-2].get("longShortRatio"))
        last_val = parse_number(ls[-1].get("longShortRatio"))
        long_short_change_pct = pct_change(last_val, prev_val)

    top_trader_ratio = None
    if isinstance(top, list) and len(top) >= 1:
        top_trader_ratio = parse_number(top[-1].get("longShortRatio"))

    smart_money = "Neutral"
    if (
        top_trader_ratio is not None and top_trader_ratio >= 1.15 and
        buy_sell_ratio is not None and buy_sell_ratio >= 1.05 and
        oi_change_pct is not None and oi_change_pct > 0
    ):
        smart_money = "Bullish"
    elif (
        top_trader_ratio is not None and top_trader_ratio <= 0.90 and
        buy_sell_ratio is not None and buy_sell_ratio <= 0.95 and
        oi_change_pct is not None and oi_change_pct < 0
    ):
        smart_money = "Bearish"

    entry_bias = "Wait"
    if (
        buy_sell_ratio is not None and buy_sell_ratio > 1.05 and
        oi_change_pct is not None and oi_change_pct > 0
    ):
        entry_bias = "Pullback watch"
    if (
        buy_sell_ratio is not None and buy_sell_ratio > 1.08 and
        oi_change_pct is not None and oi_change_pct > 1.0 and
        long_short_ratio is not None and long_short_ratio > 1.0
    ):
        entry_bias = "Breakout watch"
    if (
        buy_sell_ratio is not None and buy_sell_ratio < 0.95 and
        oi_change_pct is not None and oi_change_pct < 0
    ):
        entry_bias = "Avoid"

    return {
        "open_interest": open_interest,
        "oi_change_pct": oi_change_pct,
        "buy_sell_ratio": buy_sell_ratio,
        "buy_sell_change_pct": buy_sell_change_pct,
        "long_short_ratio": long_short_ratio,
        "long_short_change_pct": long_short_change_pct,
        "top_trader_ratio": top_trader_ratio,
        "smart_money": smart_money,
        "entry_bias": entry_bias,
    }


def build_coin_row(item, scan_tf="1h", deriv_tf="5m"):
    base_symbol = item.get("symbol", "").upper()
    pair = f"{base_symbol}USDT"

    if pair not in binance_futures_symbols:
        return None

    alt_score = signal_score(item)
    if alt_score is None and item.get("signalName") != "Favorite":
        return None

    if alt_score is None:
        alt_score = 0

    klines = get_spot_klines(pair, interval=scan_tf, limit=3)
    spot24 = get_spot_24h(pair)
    futures24 = get_futures_24h(pair)

    last_price = "N/A"
    if isinstance(spot24, dict) and spot24.get("lastPrice"):
        last_price = spot24.get("lastPrice")
    elif isinstance(futures24, dict) and futures24.get("lastPrice"):
        last_price = futures24.get("lastPrice")

    scan_price_change = compute_price_change_tf(klines)
    scan_volume_change = compute_volume_change_tf(klines)
    scalp_score = compute_scalp_score(scan_price_change, scan_volume_change, alt_score)

    deriv = compute_derivatives_metrics(pair, deriv_tf)

    return {
        "symbol": base_symbol,
        "pair": pair,
        "signal": item.get("signalName", "N/A"),
        "direction": item.get("direction", "N/A"),
        "market_cap": item.get("marketCap", "N/A"),
        "alt_price_change": item.get("priceChange", "N/A"),
        "last_price": last_price,
        "scan_price_change": scan_price_change,
        "scan_volume_change": scan_volume_change,
        "scalp_score": scalp_score,
        **deriv
    }


def select_rows(scan_tf="1h", deriv_tf="5m"):
    signals = get_altfins_signals(scan_tf=scan_tf)
    rows = []

    seen = set()
    for item in signals:
        sym = item.get("symbol", "").upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)

        row = build_coin_row(item, scan_tf=scan_tf, deriv_tf=deriv_tf)
        if row is not None:
            rows.append(row)

    rows.sort(key=lambda x: x["scalp_score"], reverse=True)
    return rows[:10]


def build_select(name, options, selected):
    html = ""
    for opt in options:
        sel = "selected" if opt == selected else ""
        html += f'<option value="{opt}" {sel}>{opt}</option>'
    return f'<select name="{name}" style="padding:8px;">{html}</select>'


def nav(scan_tf, deriv_tf):
    return f"""
    <div class="nav">
        <a href="/dashboard?scan_tf={scan_tf}&deriv_tf={deriv_tf}">📊 Dashboard</a>
        <a href="/favorites?scan_tf={scan_tf}&deriv_tf={deriv_tf}">⭐ Favorites</a>
        <a href="/volume-explosions?scan_tf={scan_tf}&deriv_tf={deriv_tf}">⚡ Volume Explosions</a>
    </div>
    """


@app.on_event("startup")
async def startup():
    global binance_futures_symbols
    binance_futures_symbols = get_binance_futures_symbols()


@app.get("/")
def home():
    return RedirectResponse(url="/dashboard?scan_tf=1h&deriv_tf=5m")


@app.get("/favorite")
def add_favorite(symbol: str = Query(...)):
    symbol = symbol.upper()
    if symbol not in favorites:
        favorites.append(symbol)
    return RedirectResponse(url="/favorites?scan_tf=1h&deriv_tf=5m", status_code=302)


@app.get("/remove_favorite")
def remove_favorite(symbol: str = Query(...), scan_tf: str = Query("1h"), deriv_tf: str = Query("5m")):
    symbol = symbol.upper()
    if symbol in favorites:
        favorites.remove(symbol)
    return RedirectResponse(url=f"/favorites?scan_tf={scan_tf}&deriv_tf={deriv_tf}", status_code=302)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(scan_tf: str = Query("1h"), deriv_tf: str = Query("5m")):
    global latest_dashboard_rows

    if scan_tf not in SCAN_TIMEFRAMES:
        scan_tf = "1h"
    if deriv_tf not in DERIV_TIMEFRAMES:
        deriv_tf = "5m"

    latest_dashboard_rows = select_rows(scan_tf=scan_tf, deriv_tf=deriv_tf)

    rows_html = ""
    for i, row in enumerate(latest_dashboard_rows, start=1):
        smart_color = "#16a34a" if row["smart_money"] == "Bullish" else "#f87171" if row["smart_money"] == "Bearish" else "#eab308"

        rows_html += f"""
        <tr>
            <td>{i}</td>
            <td>{row['symbol']}</td>
            <td>{row['signal']}</td>
            <td>{row['market_cap']}</td>
            <td>{format_num(row['scan_price_change'], 2, '%')}</td>
            <td>{format_num(row['scan_volume_change'], 2, '%')}</td>
            <td>{format_num(row['oi_change_pct'], 2, '%')}</td>
            <td>{format_num(row['buy_sell_ratio'], 3)}</td>
            <td>{format_num(row['buy_sell_change_pct'], 2, '%')}</td>
            <td>{format_num(row['long_short_ratio'], 3)}</td>
            <td>{format_num(row['long_short_change_pct'], 2, '%')}</td>
            <td>{format_num(row['top_trader_ratio'], 3)}</td>
            <td style="color:{smart_color};font-weight:bold;">{row['smart_money']}</td>
            <td>{format_num(row['scalp_score'], 2)}</td>
            <td><a href="/analyze?symbol={row['symbol']}&scan_tf={scan_tf}&deriv_tf={deriv_tf}" style="color:#38bdf8;">Analyze</a></td>
            <td><a href="/favorite?symbol={row['symbol']}" style="color:#facc15;">⭐ Add</a></td>
        </tr>
        """

    return f"""
    <html>
    <head>
        <title>Crypto Dashboard</title>
        <meta http-equiv="refresh" content="60">
        <style>
            body {{ font-family: Arial, sans-serif; background: #0f172a; color: white; padding: 20px; }}
            h1 {{ color: #38bdf8; }}
            .nav a {{ margin-right: 20px; color: #38bdf8; text-decoration: none; font-weight: bold; }}
            table {{ width: 100%; border-collapse: collapse; background: #1e293b; font-size: 14px; }}
            th, td {{ padding: 10px; border: 1px solid #334155; text-align: left; }}
            th {{ background: #334155; }}
            tr:hover {{ background: #273549; }}
            .controls {{ margin: 16px 0; }}
            button {{ padding: 8px 14px; }}
            select, input {{ margin-right: 10px; }}
        </style>
    </head>
    <body>
        <h1>Top 10 Crypto Opportunities</h1>
        {nav(scan_tf, deriv_tf)}

        <form class="controls" method="get" action="/dashboard">
            <label>Scan TF:</label>
            {build_select("scan_tf", SCAN_TIMEFRAMES, scan_tf)}
            <label>Derivatives TF:</label>
            {build_select("deriv_tf", DERIV_TIMEFRAMES, deriv_tf)}
            <button type="submit">Apply</button>
        </form>

        <form method="get" action="/analyze" style="margin-bottom:16px;">
            <input name="symbol" placeholder="Enter coin, e.g. BTC or SOL" style="padding:8px;">
            <input type="hidden" name="scan_tf" value="{scan_tf}">
            <input type="hidden" name="deriv_tf" value="{deriv_tf}">
            <button type="submit">Analyze</button>
        </form>

        <p>Refreshes every 1 minute</p>

        <table>
            <tr>
                <th>Rank</th>
                <th>Coin</th>
                <th>Signal</th>
                <th>Market Cap</th>
                <th>{scan_tf} Price %</th>
                <th>{scan_tf} Vol %</th>
                <th>{deriv_tf} OI Δ %</th>
                <th>{deriv_tf} Buy/Sell</th>
                <th>{deriv_tf} Buy/Sell Δ %</th>
                <th>{deriv_tf} L/S</th>
                <th>{deriv_tf} L/S Δ %</th>
                <th>{deriv_tf} Top Trader</th>
                <th>Smart Money</th>
                <th>Scalp Score</th>
                <th>Analyze</th>
                <th>Favorite</th>
            </tr>
            {rows_html if rows_html else "<tr><td colspan='16'>No matching Binance Futures coins found.</td></tr>"}
        </table>
    </body>
    </html>
    """


@app.get("/favorites", response_class=HTMLResponse)
def favorites_page(scan_tf: str = Query("1h"), deriv_tf: str = Query("5m")):
    if scan_tf not in SCAN_TIMEFRAMES:
        scan_tf = "1h"
    if deriv_tf not in DERIV_TIMEFRAMES:
        deriv_tf = "5m"

    rows_html = ""
    for i, symbol in enumerate(favorites, start=1):
        fake_alt_item = {
            "symbol": symbol,
            "signalName": "Favorite",
            "direction": "BULLISH",
            "marketCap": "N/A",
            "priceChange": "N/A",
        }
        row = build_coin_row(fake_alt_item, scan_tf=scan_tf, deriv_tf=deriv_tf)
        if row is None:
            rows_html += f"""
            <tr>
                <td>{i}</td>
                <td>{symbol}</td>
                <td colspan="12">Not available on Binance Futures</td>
                <td><a href="/remove_favorite?symbol={symbol}&scan_tf={scan_tf}&deriv_tf={deriv_tf}" style="color:#f87171;">Remove</a></td>
            </tr>
            """
            continue

        smart_color = "#16a34a" if row["smart_money"] == "Bullish" else "#f87171" if row["smart_money"] == "Bearish" else "#eab308"

        rows_html += f"""
        <tr>
            <td>{i}</td>
            <td>{row['symbol']}</td>
            <td>{row['last_price']}</td>
            <td>{format_num(row['scan_price_change'], 2, '%')}</td>
            <td>{format_num(row['scan_volume_change'], 2, '%')}</td>
            <td>{format_num(row['oi_change_pct'], 2, '%')}</td>
            <td>{format_num(row['buy_sell_ratio'], 3)}</td>
            <td>{format_num(row['buy_sell_change_pct'], 2, '%')}</td>
            <td>{format_num(row['long_short_ratio'], 3)}</td>
            <td>{format_num(row['long_short_change_pct'], 2, '%')}</td>
            <td>{format_num(row['top_trader_ratio'], 3)}</td>
            <td style="color:{smart_color};font-weight:bold;">{row['smart_money']}</td>
            <td>{row['entry_bias']}</td>
            <td>{format_num(row['scalp_score'], 2)}</td>
            <td><a href="/analyze?symbol={row['symbol']}&scan_tf={scan_tf}&deriv_tf={deriv_tf}" style="color:#38bdf8;">Analyze</a></td>
            <td><a href="/remove_favorite?symbol={row['symbol']}&scan_tf={scan_tf}&deriv_tf={deriv_tf}" style="color:#f87171;">Remove</a></td>
        </tr>
        """

    return f"""
    <html>
    <head>
        <title>Favorites</title>
        <meta http-equiv="refresh" content="60">
        <style>
            body {{ font-family: Arial, sans-serif; background: #0f172a; color: white; padding: 20px; }}
            h1 {{ color: #facc15; }}
            .nav a {{ margin-right: 20px; color: #38bdf8; text-decoration: none; font-weight: bold; }}
            table {{ width: 100%; border-collapse: collapse; background: #1e293b; font-size: 14px; }}
            th, td {{ padding: 10px; border: 1px solid #334155; text-align: left; }}
            th {{ background: #334155; }}
            tr:hover {{ background: #273549; }}
            .controls {{ margin: 16px 0; }}
            button {{ padding: 8px 14px; }}
        </style>
    </head>
    <body>
        <h1>⭐ Favorites</h1>
        {nav(scan_tf, deriv_tf)}

        <form class="controls" method="get" action="/favorites">
            <label>Scan TF:</label>
            {build_select("scan_tf", SCAN_TIMEFRAMES, scan_tf)}
            <label>Derivatives TF:</label>
            {build_select("deriv_tf", DERIV_TIMEFRAMES, deriv_tf)}
            <button type="submit">Apply</button>
        </form>

        <table>
            <tr>
                <th>#</th>
                <th>Coin</th>
                <th>Price</th>
                <th>{scan_tf} Price %</th>
                <th>{scan_tf} Vol %</th>
                <th>{deriv_tf} OI Δ %</th>
                <th>{deriv_tf} Buy/Sell</th>
                <th>{deriv_tf} Buy/Sell Δ %</th>
                <th>{deriv_tf} L/S</th>
                <th>{deriv_tf} L/S Δ %</th>
                <th>{deriv_tf} Top Trader</th>
                <th>Smart Money</th>
                <th>Entry Bias</th>
                <th>Scalp Score</th>
                <th>Analyze</th>
                <th>Action</th>
            </tr>
            {rows_html if rows_html else "<tr><td colspan='16'>No favorites yet.</td></tr>"}
        </table>
    </body>
    </html>
    """


@app.get("/volume-explosions", response_class=HTMLResponse)
def volume_explosions(scan_tf: str = Query("1h"), deriv_tf: str = Query("5m")):
    if scan_tf not in SCAN_TIMEFRAMES:
        scan_tf = "1h"
    if deriv_tf not in DERIV_TIMEFRAMES:
        deriv_tf = "5m"

    rows = select_rows(scan_tf=scan_tf, deriv_tf=deriv_tf)
    rows.sort(key=lambda x: compute_explosion_score(x), reverse=True)

    rows_html = ""
    for i, row in enumerate(rows, start=1):
        explosion = compute_explosion_score(row)
        highlight = is_real_explosion(row)
        row_style = ' style="background:#4c1d95;"' if highlight else ""

        rows_html += f"""
        <tr{row_style}>
            <td>{i}</td>
            <td>{row['symbol']}</td>
            <td>{row['last_price']}</td>
            <td>{format_num(row['scan_price_change'], 2, '%')}</td>
            <td>{format_num(row['scan_volume_change'], 2, '%')}</td>
            <td>{format_num(row['oi_change_pct'], 2, '%')}</td>
            <td>{format_num(row['buy_sell_ratio'], 3)}</td>
            <td>{format_num(row['buy_sell_change_pct'], 2, '%')}</td>
            <td>{format_num(row['long_short_ratio'], 3)}</td>
            <td>{format_num(row['long_short_change_pct'], 2, '%')}</td>
            <td>{format_num(row['top_trader_ratio'], 3)}</td>
            <td>{format_num(explosion, 2)}</td>
            <td>{row['entry_bias']}</td>
            <td><a href="/analyze?symbol={row['symbol']}&scan_tf={scan_tf}&deriv_tf={deriv_tf}" style="color:#38bdf8;">Analyze</a></td>
            <td><a href="/favorite?symbol={row['symbol']}" style="color:#facc15;">⭐ Add</a></td>
        </tr>
        """

    return f"""
    <html>
    <head>
        <title>Volume Explosions</title>
        <meta http-equiv="refresh" content="60">
        <style>
            body {{ font-family: Arial, sans-serif; background: #0f172a; color: white; padding: 20px; }}
            h1 {{ color: #f97316; }}
            .nav a {{ margin-right: 20px; color: #38bdf8; text-decoration: none; font-weight: bold; }}
            table {{ width: 100%; border-collapse: collapse; background: #1e293b; font-size: 14px; }}
            th, td {{ padding: 10px; border: 1px solid #334155; text-align: left; }}
            th {{ background: #334155; }}
            .controls {{ margin: 16px 0; }}
        </style>
    </head>
    <body>
        <h1>⚡ Volume Explosions</h1>
        {nav(scan_tf, deriv_tf)}

        <form class="controls" method="get" action="/volume-explosions">
            <label>Scan TF:</label>
            {build_select("scan_tf", SCAN_TIMEFRAMES, scan_tf)}
            <label>Derivatives TF:</label>
            {build_select("deriv_tf", DERIV_TIMEFRAMES, deriv_tf)}
            <button type="submit">Apply</button>
        </form>

        <form method="get" action="/favorite" style="margin-bottom:16px;">
            <input name="symbol" placeholder="Add coin manually (SOL, ETH, BTC)" style="padding:8px;">
            <button type="submit">Add to Favorites</button>
        </form>

        <table>
            <tr>
                <th>Rank</th>
                <th>Coin</th>
                <th>Price</th>
                <th>{scan_tf} Price %</th>
                <th>{scan_tf} Vol %</th>
                <th>{deriv_tf} OI Δ %</th>
                <th>{deriv_tf} Buy/Sell</th>
                <th>{deriv_tf} Buy/Sell Δ %</th>
                <th>{deriv_tf} L/S</th>
                <th>{deriv_tf} L/S Δ %</th>
                <th>{deriv_tf} Top Trader</th>
                <th>Explosion Score</th>
                <th>Entry Bias</th>
                <th>Analyze</th>
                <th>Favorite</th>
            </tr>
            {rows_html if rows_html else "<tr><td colspan='15'>No data yet.</td></tr>"}
        </table>

        <p style="margin-top:20px;">
            Highlighted rows = Volume &gt; 200%, OI &gt; 1%, Buy/Sell &gt; 1.05
        </p>
    </body>
    </html>
    """


@app.get("/analyze", response_class=HTMLResponse)
def analyze(symbol: str = Query(...), scan_tf: str = Query("1h"), deriv_tf: str = Query("5m")):
    symbol = symbol.upper()
    if scan_tf not in SCAN_TIMEFRAMES:
        scan_tf = "1h"
    if deriv_tf not in DERIV_TIMEFRAMES:
        deriv_tf = "5m"

    alt_signals = get_altfins_signals(scan_tf=scan_tf)
    alt_item = None
    for item in alt_signals:
        if item.get("symbol", "").upper() == symbol:
            alt_item = item
            break

    if alt_item is None:
        alt_item = {
            "symbol": symbol,
            "signalName": "Favorite",
            "direction": "BULLISH",
            "marketCap": "N/A",
            "priceChange": "N/A",
        }

    row = build_coin_row(alt_item, scan_tf=scan_tf, deriv_tf=deriv_tf)
    if row is None:
        pair = f"{symbol}USDT"
        return f"""
        <html>
        <body style="font-family:Arial;background:#0f172a;color:white;padding:20px;">
            {nav(scan_tf, deriv_tf)}
            <h1>{symbol} Analysis</h1>
            <p>{pair} is not currently available on Binance Futures, so derivatives metrics are not available.</p>
        </body>
        </html>
        """

    invalidation = f"Below recent {scan_tf} low"
    entry_idea = "Wait for confirmation"

    if row["entry_bias"] == "Breakout watch":
        entry_idea = f"Break local {scan_tf} high with strong {deriv_tf} buy flow"
        invalidation = "Lose breakout level / OI stalls"
    elif row["entry_bias"] == "Pullback watch":
        entry_idea = f"Watch pullback and reclaim with strong {deriv_tf} buy flow"
        invalidation = "Break below pullback support"
    elif row["entry_bias"] == "Avoid":
        entry_idea = "Avoid long until flow improves"

    return f"""
    <html>
    <head>
        <title>{symbol} Analysis</title>
        <style>
            body {{ font-family: Arial, sans-serif; background: #0f172a; color: white; padding: 20px; }}
            h1 {{ color: #38bdf8; }}
            .card {{ background:#1e293b; padding:20px; margin-bottom:20px; border:1px solid #334155; }}
            .nav a {{ margin-right: 20px; color: #38bdf8; text-decoration: none; font-weight: bold; }}
            .controls {{ margin: 16px 0; }}
            button {{ padding: 8px 14px; }}
        </style>
    </head>
    <body>
        {nav(scan_tf, deriv_tf)}
        <h1>{symbol} Analysis</h1>

        <form class="controls" method="get" action="/analyze">
            <input type="hidden" name="symbol" value="{symbol}">
            <label>Scan TF:</label>
            {build_select("scan_tf", SCAN_TIMEFRAMES, scan_tf)}
            <label>Derivatives TF:</label>
            {build_select("deriv_tf", DERIV_TIMEFRAMES, deriv_tf)}
            <button type="submit">Apply</button>
        </form>

        <div class="card">
            <h2>Market</h2>
            <p>Pair: {row['pair']}</p>
            <p>Last Price: {row['last_price']}</p>
            <p>altFINS Signal: {row['signal']}</p>
            <p>altFINS Price Change: {row['alt_price_change']}</p>
            <p>Market Cap: {row['market_cap']}</p>
            <p>{scan_tf} Price Change: {format_num(row['scan_price_change'], 2, '%')}</p>
            <p>{scan_tf} Volume Change: {format_num(row['scan_volume_change'], 2, '%')}</p>
        </div>

        <div class="card">
            <h2>Derivatives ({deriv_tf})</h2>
            <p>Open Interest: {row['open_interest']}</p>
            <p>OI Change %: {format_num(row['oi_change_pct'], 2, '%')}</p>
            <p>Buy/Sell Ratio: {format_num(row['buy_sell_ratio'], 3)}</p>
            <p>Buy/Sell Change %: {format_num(row['buy_sell_change_pct'], 2, '%')}</p>
            <p>Long/Short Ratio: {format_num(row['long_short_ratio'], 3)}</p>
            <p>Long/Short Change %: {format_num(row['long_short_change_pct'], 2, '%')}</p>
            <p>Top Trader Ratio: {format_num(row['top_trader_ratio'], 3)}</p>
            <p>Smart Money: {row['smart_money']}</p>
        </div>

        <div class="card">
            <h2>Trade Guidance</h2>
            <p>Scalp Score ({scan_tf} only): {format_num(row['scalp_score'], 2)}</p>
            <p>Entry Bias: {row['entry_bias']}</p>
            <p>Entry Idea: {entry_idea}</p>
            <p>Invalidation: {invalidation}</p>
        </div>
    </body>
    </html>
    """
