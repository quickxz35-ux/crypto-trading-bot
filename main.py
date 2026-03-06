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
    return float(str(value).replace(",", "").replace("%", "").strip())


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


async def altfins_worker():
    global latest_top_10

    while True:
        print("Checking altFINS signals...")

        try:
            r = requests.post(
                "https://altfins.com/api/v2/public/signals-feed/search-requests",
                headers={"X-API-KEY": ALTFINS_KEY},
                json={
                    "timeRange": {"from": "now-2h", "to": "now"},
                    "direction": "BULLISH"
                },
                timeout=20
            )

            print("Status:", r.status_code)

            data = r.json()
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

            print("===== TOP 10 TRADE OPPORTUNITIES =====")
            for i, item in enumerate(latest_top_10, start=1):
                print(
                    f"{i}. {item.get('symbol')} | "
                    f"{item.get('direction')} | "
                    f"{item.get('signalName')} | "
                    f"Change: {item.get('priceChange')} | "
                    f"MC: {item.get('marketCap')} | "
                    f"Score: {item.get('score')}"
                )

        except Exception as e:
            print("Error:", e)

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

    exists = any(item["symbol"] == symbol for item in favorites)

    if not exists:
        favorites.append({
            "symbol": symbol,
            "signal": signal,
            "priceChange": change,
            "marketCap": market_cap,
            "score": score
        })

    return RedirectResponse(url="/favorites", status_code=302)


@app.get("/remove_favorite")
def remove_favorite(symbol: str = Query(...)):
    global favorites
    favorites = [item for item in favorites if item["symbol"] != symbol]
    return RedirectResponse(url="/favorites", status_code=302)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    rows = ""

    for i, item in enumerate(latest_top_10, start=1):
        rows += f"""
        <tr>
            <td>{i}</td>
            <td>{item.get('symbol')}</td>
            <td style="color:#16a34a;font-weight:bold;">{item.get('direction')}</td>
            <td>{item.get('signalName')}</td>
            <td>{item.get('priceChange')}</td>
            <td>{item.get('marketCap')}</td>
            <td>{item.get('score')}</td>
            <td>
                <a href="/favorite?symbol={item.get('symbol')}&signal={item.get('signalName')}&change={item.get('priceChange')}&market_cap={item.get('marketCap')}&score={item.get('score')}"
                   style="color:#38bdf8;text-decoration:none;font-weight:bold;">
                   ⭐ Add
                </a>
            </td>
        </tr>
        """

    return f"""
    <html>
    <head>
        <title>Crypto Dashboard</title>
        <meta http-equiv="refresh" content="30">
        <style>
            body {{
                font-family: Arial, sans-serif;
                background: #0f172a;
                color: white;
                padding: 20px;
            }}
            h1 {{
                color: #38bdf8;
            }}
            .nav a {{
                margin-right: 20px;
                color: #38bdf8;
                text-decoration: none;
                font-weight: bold;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                background: #1e293b;
            }}
            th, td {{
                padding: 12px;
                border: 1px solid #334155;
                text-align: left;
            }}
            th {{
                background: #334155;
            }}
            tr:hover {{
                background: #273549;
            }}
        </style>
    </head>
    <body>
        <h1>Top 10 Crypto Opportunities</h1>
        <div class="nav">
            <a href="/dashboard">📊 New Scan</a>
            <a href="/favorites">⭐ Favorites</a>
        </div>
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
        rows += f"""
        <tr>
            <td>{i}</td>
            <td>{item.get('symbol')}</td>
            <td>{item.get('signal')}</td>
            <td>{item.get('priceChange')}</td>
            <td>{item.get('marketCap')}</td>
            <td>{item.get('score')}</td>
            <td>
                <a href="/remove_favorite?symbol={item.get('symbol')}"
                   style="color:#f87171;text-decoration:none;font-weight:bold;">
                   Remove
                </a>
            </td>
        </tr>
        """

    return f"""
    <html>
    <head>
        <title>Favorites</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                background: #0f172a;
                color: white;
                padding: 20px;
            }}
            h1 {{
                color: #facc15;
            }}
            .nav a {{
                margin-right: 20px;
                color: #38bdf8;
                text-decoration: none;
                font-weight: bold;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                background: #1e293b;
            }}
            th, td {{
                padding: 12px;
                border: 1px solid #334155;
                text-align: left;
            }}
            th {{
                background: #334155;
            }}
            tr:hover {{
                background: #273549;
            }}
        </style>
    </head>
    <body>
        <h1>⭐ Favorite Coins</h1>
        <div class="nav">
            <a href="/dashboard">📊 New Scan</a>
            <a href="/favorites">⭐ Favorites</a>
        </div>
        <table>
            <tr>
                <th>#</th>
                <th>Coin</th>
                <th>Signal</th>
                <th>Price Change</th>
                <th>Market Cap</th>
                <th>Score</th>
                <th>Action</th>
            </tr>
            {rows if rows else "<tr><td colspan='7'>No favorites yet.</td></tr>"}
        </table>
    </body>
    </html>
    """
