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
            r = requests.get(
                "https://api.altfins.com/v1/signals",
                headers={"Authorization": f"Bearer {ALTFINS_KEY}"}
            )

            data = r.json()

            for coin in data[:5]:
                print("Signal:", coin)

        except Exception as e:
            print("Error:", e)

        await asyncio.sleep(600)


@app.on_event("startup")
async def start_worker():
    asyncio.create_task(altfins_worker())


@app.get("/")
def home():
    return {"status": "altFINS worker running"}
