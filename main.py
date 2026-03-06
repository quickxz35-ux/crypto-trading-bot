import asyncio
import os
import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

ALTFINS_KEY = os.getenv("ALTFINS_API_KEY")
latest_top_10 = []


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

    if price_change <= 0:
        return -999

    if market_cap < 25000000:
        return -999

    if direction == "BULLISH":
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
    return {"status": "dashboard running", "dashboard": "/dashboard"}


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    rows = ""

    for i, item in enumerate(latest_top_10, start=1):
        color = "#16a34a" if item.get("direction") == "BULLISH" else "#dc2626"
        rows += f"""
        <tr>
            <td>{i}</td>
            <td>{item.get('symbol')}</td>
            <td style="color:{color};font-weight:bold;">{item.get('direction')}</td>
            <td>{item.get('signalName')}</td>
            <td>{item.get('priceChange')}</td>
            <td>{item.get('marketCap')}</td>
            <td>{item.get('score')}</td>
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
            </tr>
            {rows}
        </table>
    </body>
    </html>
    """
