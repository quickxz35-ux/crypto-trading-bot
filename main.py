import asyncio
import os
import requests
from fastapi import FastAPI

app = FastAPI()

ALTFINS_KEY = os.getenv("ALTFINS_API_KEY")


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

    # Ignore weak setups
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
                    item["score"] = score
                    scored.append(item)

            top_10 = sorted(scored, key=lambda x: x["score"], reverse=True)[:10]

            print("===== TOP 10 TRADE OPPORTUNITIES =====")

            for i, item in enumerate(top_10, start=1):
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
    return {"status": "filtered top-10 altFINS scanner running"}
