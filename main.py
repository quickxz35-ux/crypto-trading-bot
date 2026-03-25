import os
import time
import requests
import threading
import json
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
SESSION = requests.Session()

# 🔥 TRADE FLOW STORAGE
trade_flow = {}

# 🔥 START TRADE STREAM
def start_trade_stream(symbol):
    url = f"wss://fstream.binance.com/ws/{symbol.lower()}@aggTrade"

    def on_message(ws, message):
        data = json.loads(message)

        qty = float(data["q"])
        is_sell = data["m"]

        if symbol not in trade_flow:
            trade_flow[symbol] = {"buy": 0.0, "sell": 0.0}

        if is_sell:
            trade_flow[symbol]["sell"] += qty
        else:
            trade_flow[symbol]["buy"] += qty

    def run():
        ws = websocket.WebSocketApp(url, on_message=on_message)
        ws.run_forever()

    threading.Thread(target=run, daemon=True).start()

# 🔥 RESET EVERY 60s
def reset_trade_flow():
    while True:
        time.sleep(60)
        for k in trade_flow:
            trade_flow[k] = {"buy": 0.0, "sell": 0.0}

threading.Thread(target=reset_trade_flow, daemon=True).start()


# ---------------- HELPERS ----------------

def safe_get_json(url, params=None):
    try:
        r = SESSION.get(url, params=params, timeout=10)
        return r.json() if r.status_code == 200 else None
    except:
        return None


def parse_float(v, d=None):
    try:
        return float(v)
    except:
        return d


def avg(v):
    v = [x for x in v if x is not None]
    return sum(v)/len(v) if v else None


def clamp(v, a, b):
    return max(a, min(v, b))


def format_num(v, d=2, s=""):
    try:
        return f"{float(v):.{d}f}{s}"
    except:
        return "N/A"


# ---------------- BINANCE ----------------

def get_binance_candles(symbol, tf):
    return safe_get_json(
        "https://fapi.binance.com/fapi/v1/klines",
        {"symbol": symbol, "interval": tf, "limit": 25},
    ) or []


# ---------------- ANALYSIS ----------------

def analyze_coin(coin, tf):
    symbol = f"{coin}USDT"

    candles = get_binance_candles(symbol, tf)
    if len(candles) < 21:
        return None

    latest = candles[-1]
    prev = candles[-21:-1]

    open_p = parse_float(latest[1])
    close_p = parse_float(latest[4])

    price_change = ((close_p - open_p) / open_p) * 100

    bias = "Bullish" if price_change > 0 else "Bearish"

    # 🔥 TRADE FLOW CALC
    flow = trade_flow.get(symbol, {"buy": 0.0, "sell": 0.0})
    buy_vol = flow["buy"]
    sell_vol = flow["sell"]
    delta = buy_vol - sell_vol

    flow_bias = "Neutral"
    if delta > 0:
        flow_bias = "Buyers"
    elif delta < 0:
        flow_bias = "Sellers"

    return {
        "coin": coin,
        "bias": bias,
        "price_change_pct": round(price_change, 2),

        # 🔥 TRADE FLOW OUTPUT
        "buy_volume": round(buy_vol, 2),
        "sell_volume": round(sell_vol, 2),
        "delta": round(delta, 2),
        "flow_bias": flow_bias,
    }


# ---------------- UI ----------------

def render_coin_row(coin, tf, i):
    row = analyze_coin(coin, tf)

    if not row:
        return f"<div>No data {coin}</div>"

    flow_text = f"{row['flow_bias']} · Δ {format_num(row['delta'])}"

    return f"""
    <div class="coin-card" id="coin-card-{coin}">
        <div><b>{i}. {coin}</b> | {row['bias']} | {row['price_change_pct']}%</div>

        <div style="margin-top:10px;">
            <b>📊 Trade Flow:</b> {flow_text}
        </div>
    </div>
    """


# ---------------- ROUTES ----------------

@app.get("/")
def home():
    return RedirectResponse("/favorites")


@app.get("/favorites", response_class=HTMLResponse)
def favorites_page(tf: str = "15m"):
    rows = "".join([render_coin_row(c, tf, i+1) for i, c in enumerate(favorites)])

    return f"""
    <h1>Favorites</h1>

    <form action="/add">
        <input name="coin" placeholder="BTC">
        <button>Add</button>
    </form>

    {rows}
    """


@app.get("/add")
def add_coin(coin: str):
    coin = coin.upper()

    if coin not in favorites:
        favorites.append(coin)

        # 🔥 START STREAM HERE
        start_trade_stream(f"{coin}USDT")

    return RedirectResponse("/favorites")
