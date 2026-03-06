import asyncio
import requests
import os
from fastapi import FastAPI

app = FastAPI()

ALTFINS_KEY = os.getenv("ALTFINS_API_KEY")

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
                items = data.get("data") or data.get("signals") or data.get("results")

            if not isinstance(items, list):
                print("Unexpected JSON shape:", data)
            else:
                for item in items[:5]:
                    print("Signal:", item)

        except Exception as e:
            print("Error:", e)

        await asyncio.sleep(600)


@app.on_event("startup")
async def start_worker():
    asyncio.create_task(altfins_worker())


@app.get("/")
def home():
    return {"status": "altFINS worker running"}
