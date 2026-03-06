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

    if direction == "BULLISH":
        score += 50
    elif direction == "BEARISH":
        score -= 20

    score += min(price_change * 5, 20)

    if market_cap > 500000000:
        score += 20
    elif market_cap > 100000000:
        score += 10
    elif market_cap > 10000000:
        score += 5

    if "Bull Power" in signal_name:
        score += 15
    if "Oversold" in signal_name:
        score += 10
    if "Bear Power" in signal_name:
        score -= 10
    if "Overbought" in signal_name:
        score -= 10

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

            items = None
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = (
                    data.get("content")
                    or data.get("data")
                    or data.get("signals")
                    or data.get("results")
                )

            if not isinstance(items, list):
                print("Unexpected JSON shape:", data)
            else:
                scored = []
                for item in items:
                    item["score"] = score_signal(item)
                    scored.append(item)

                top_10 = sorted(scored, key=lambda x: x["score"], reverse=True)[:10]

                print("===== TOP 10 OPPORTUNITIES =====")
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
    return {"status": "top-10 altFINS scanner running"}
