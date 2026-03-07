import requests
from cachetools import TTLCache
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

app = FastAPI(title="Crypto Favorites Watchlist")

TIMEFRAME_OPTIONS = ["1d", "4h", "1h", "30m", "15m", "5m"]
favorites = []

data_cache = TTLCache(maxsize=3000, ttl=30)


# -------------------------------------------------
# HELPERS
# -------------------------------------------------

def safe_get_json(url, params=None, timeout=10):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, params=params, headers=headers, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print("GET ERROR:", url, str(e))
    return None


def parse_float(value, default=None):
    try:
        if value in (None, "", "null"):
            return default
        return float(value)
    except Exception:
        return default


def avg(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def clamp(value, min_value, max_value):
    return max(min_value, min(value, max_value))


def format_num(value, decimals=2, suffix=""):
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.{decimals}f}{suffix}"
    except Exception:
        return "N/A"


def tf_to_binance(tf):
    return tf


# -------------------------------------------------
# UNIVERSAL COLOR SYSTEM
# -------------------------------------------------

def band_color(score):
    s = clamp(float(score), 0, 100)
    if s < 25:
        return "#64748b", "Weak"
    if s < 50:
        return "#3b82f6", "Building"
    if s < 75:
        return "#22c55e", "Strong"
    return "#a855f7", "Extreme"


def bias_badge(bias):
    color = "#22c55e" if bias == "Bullish" else "#ef4444" if bias == "Bearish" else "#eab308"
    return f'<span class="bias" style="color:{color};">{bias}</span>'


# -------------------------------------------------
# BINANCE FUTURES DATA
# -------------------------------------------------

def get_binance_candles(symbol, tf, limit=25):
    cache_key = f"klines::{symbol}::{tf}::{limit}"
    if cache_key in data_cache:
        return data_cache[cache_key]

    data = safe_get_json(
        "https://fapi.binance.com/fapi/v1/klines",
        params={"symbol": symbol, "interval": tf_to_binance(tf), "limit": limit},
    )

    result = data if isinstance(data, list) else []
    data_cache[cache_key] = result
    return result


def get_binance_funding(symbol):
    cache_key = f"funding::{symbol}"
    if cache_key in data_cache:
        return data_cache[cache_key]

    data = safe_get_json(
        "https://fapi.binance.com/fapi/v1/premiumIndex",
        params={"symbol": symbol},
    )
    value = parse_float(data.get("lastFundingRate"), None) if isinstance(data, dict) else None
    data_cache[cache_key] = value
    return value


def get_binance_oi_change(symbol, tf):
    cache_key = f"oi::{symbol}::{tf}"
    if cache_key in data_cache:
        return data_cache[cache_key]

    data = safe_get_json(
        "https://fapi.binance.com/futures/data/openInterestHist",
        params={"symbol": symbol, "period": tf, "limit": 2},
    )

    result = (None, None)
    if isinstance(data, list) and len(data) >= 2:
        prev_oi = parse_float(data[-2].get("sumOpenInterest"), None)
        last_oi = parse_float(data[-1].get("sumOpenInterest"), None)
        if prev_oi and prev_oi > 0 and last_oi is not None:
            oi_change_pct = ((last_oi - prev_oi) / prev_oi) * 100.0
            result = (oi_change_pct, last_oi)

    data_cache[cache_key] = result
    return result


# -------------------------------------------------
# ANALYSIS
# -------------------------------------------------

def analyze_coin(coin, tf):
    coin = coin.upper().strip()
    symbol = f"{coin}USDT"

    candles = get_binance_candles(symbol, tf, limit=25)
    if len(candles) < 21:
        return None

    latest = candles[-1]
    prev_20 = candles[-21:-1]

    open_price = parse_float(latest[1], None)
    high_price = parse_float(latest[2], None)
    low_price = parse_float(latest[3], None)
    close_price = parse_float(latest[4], None)
    quote_volume = parse_float(latest[7], None)

    if None in (open_price, high_price, low_price, close_price, quote_volume):
        return None
    if open_price <= 0 or low_price <= 0:
        return None

    prev_quote_volumes = [parse_float(c[7], None) for c in prev_20]
    prev_ranges_pct = []
    prev_moves_pct = []

    for c in prev_20:
        o = parse_float(c[1], None)
        h = parse_float(c[2], None)
        l = parse_float(c[3], None)
        cl = parse_float(c[4], None)
        if o and o > 0 and l and l > 0 and h is not None and cl is not None:
            prev_moves_pct.append(abs((cl - o) / o) * 100.0)
            prev_ranges_pct.append(((h - l) / l) * 100.0)

    avg_prev_volume = avg(prev_quote_volumes)
    avg_prev_range = avg(prev_ranges_pct)
    avg_prev_move = avg(prev_moves_pct)

    price_change_pct = ((close_price - open_price) / open_price) * 100.0
    volatility_pct = ((high_price - low_price) / low_price) * 100.0

    volume_change_pct = None
    rel_volume = None
    momentum_strength = None
    compression_score = None

    funding_rate = get_binance_funding(symbol)
    oi_change_pct, open_interest = get_binance_oi_change(symbol, tf)

    if avg_prev_volume and avg_prev_volume > 0:
        volume_change_pct = ((quote_volume - avg_prev_volume) / avg_prev_volume) * 100.0
        rel_volume = quote_volume / avg_prev_volume

    if avg_prev_move and avg_prev_move > 0:
        momentum_strength = abs(price_change_pct) / avg_prev_move

    if avg_prev_range and avg_prev_range > 0:
        compression_ratio = volatility_pct / avg_prev_range
        compression_score = clamp((1.0 - min(compression_ratio, 1.0)) * 100.0, 0.0, 100.0)

    bias = "Neutral"
    if price_change_pct > 0.15:
        bias = "Bullish"
    elif price_change_pct < -0.15:
        bias = "Bearish"

    momentum_score = clamp((momentum_strength or 0) / 3.0 * 100.0, 0.0, 100.0)
    volume_score = clamp((rel_volume or 0) / 3.0 * 100.0, 0.0, 100.0)

    vol_expansion = None
    if avg_prev_range and avg_prev_range > 0:
        vol_expansion = volatility_pct / avg_prev_range

    volatility_score = clamp((vol_expansion or 0) / 3.0 * 100.0, 0.0, 100.0)

    oi_abs = abs(oi_change_pct or 0)
    oi_score = clamp((oi_abs / 1.0) * 100.0, 0.0, 100.0)

    breakout_pressure = 0
    if (compression_score or 0) >= 70:
        breakout_pressure += 25
    if volume_score >= 50:
        breakout_pressure += 25
    if momentum_score >= 50:
        breakout_pressure += 25
    if volatility_score >= 40:
        breakout_pressure += 25
    breakout_pressure = clamp(breakout_pressure, 0.0, 100.0)

    liquidation_pressure = 0
    if (compression_score or 0) >= 65:
        liquidation_pressure += 30
    if volume_score >= 50:
        liquidation_pressure += 25
    if oi_score >= 40:
        liquidation_pressure += 25
    if volatility_score <= 40:
        liquidation_pressure += 20
    liquidation_pressure = clamp(liquidation_pressure, 0.0, 100.0)

    setup_score = (
        momentum_score * 0.25 +
        volume_score * 0.20 +
        volatility_score * 0.15 +
        (compression_score or 0) * 0.12 +
        oi_score * 0.08 +
        breakout_pressure * 0.10 +
        liquidation_pressure * 0.10
    )
    setup_score = round(clamp(setup_score, 0.0, 100.0), 1)

    return {
        "coin": coin,
        "symbol": symbol,
        "bias": bias,
        "price_change_pct": round(price_change_pct, 2),
        "volume_change_pct": round(volume_change_pct, 2) if volume_change_pct is not None else None,
        "volatility_pct": round(volatility_pct, 2),
        "funding_rate": funding_rate,
        "oi_change_pct": round(oi_change_pct, 2) if oi_change_pct is not None else None,
        "open_interest": open_interest,
        "momentum_strength": round(momentum_strength, 2) if momentum_strength is not None else None,
        "rel_volume": round(rel_volume, 2) if rel_volume is not None else None,
        "compression_score": round(compression_score, 1) if compression_score is not None else None,
        "momentum_score": round(momentum_score, 1),
        "volume_score": round(volume_score, 1),
        "volatility_score": round(volatility_score, 1),
        "oi_score": round(oi_score, 1),
        "breakout_pressure": round(breakout_pressure, 1),
        "liquidation_pressure": round(liquidation_pressure, 1),
        "setup_score": setup_score,
    }


# -------------------------------------------------
# BAR HTML
# -------------------------------------------------

def metric_bar(score, subtitle, width=150, height=10):
    if score is None:
        return """
        <div class="bar-wrap">
            <div class="bar"><div class="bar-fill" style="width:0;"></div></div>
            <div class="tiny">N/A</div>
        </div>
        """
    v = clamp(float(score), 0, 100)
    fill = (v / 100.0) * width
    color, band = band_color(v)
    return f"""
    <div class="bar-wrap">
        <div class="bar" style="width:{width}px;height:{height}px;">
            <div class="bar-fill" style="width:{fill}px;background:{color};"></div>
        </div>
        <div class="tiny">{band} · {subtitle}</div>
    </div>
    """


def oi_centered_bar(score, raw_value, width=150, height=10):
    v = clamp(float(score or 0), 0, 100)
    color, band = band_color(v)

    raw = raw_value or 0.0
    max_abs = 1.0
    clipped = clamp(float(raw), -max_abs, max_abs)

    half = width / 2
    fill = (abs(clipped) / max_abs) * half
    left = half if clipped >= 0 else half - fill

    arrow = "▲" if raw > 0 else "▼" if raw < 0 else "•"
    meaning = "Opening" if raw > 0 else "Closing" if raw < 0 else "Flat"

    return f"""
    <div class="bar-wrap">
        <div class="bar centered" style="width:{width}px;height:{height}px;">
            <div class="bar-mid"></div>
            <div class="bar-fill abs" style="left:{left}px;width:{fill}px;background:{color};"></div>
        </div>
        <div class="tiny">{band} · {arrow} {format_num(raw, 2, '%')} · {meaning}</div>
    </div>
    """


# -------------------------------------------------
# RENDER
# -------------------------------------------------

def render_coin_row(coin, tf, index):
    row = analyze_coin(coin, tf)

    if row is None:
        return f"""
        <div class="coin-card" id="coin-card-{coin}">
            <div class="coin-header">
                <div>
                    <div class="coin-name">{index}. {coin}</div>
                    <div class="coin-sub muted">No data returned</div>
                </div>
                <div><a class="danger-link" href="/remove?coin={coin}&tf={tf}">Remove</a></div>
            </div>
        </div>
        """

    momentum_sub = f"{format_num(row['momentum_strength'], 2)}x normal" if row["momentum_strength"] is not None else "N/A"
    volume_sub = f"{format_num(row['rel_volume'], 2)}x normal" if row["rel_volume"] is not None else "N/A"
    vol_sub = format_num(row["volatility_pct"], 2, "%")
    comp_sub = format_num(row["compression_score"], 0, "%") if row["compression_score"] is not None else "N/A"
    setup_sub = format_num(row["setup_score"], 1)
    break_sub = format_num(row["breakout_pressure"], 0, "%")
    liq_sub = format_num(row["liquidation_pressure"], 0, "%")
    price_sub = format_num(row["price_change_pct"], 2, "%")

    return f"""
    <div class="coin-card" id="coin-card-{coin}">
        <div class="coin-header">
            <div>
                <div class="coin-name">{index}. {row['coin']}</div>
                <div class="coin-sub">{bias_badge(row['bias'])} · Price {price_sub}</div>
            </div>
            <div class="coin-actions">
                <a class="danger-link" href="/remove?coin={row['coin']}&tf={tf}">Remove</a>
            </div>
        </div>

        <div class="bars-grid">
            <div class="metric">
                <div class="metric-title">⚡ Momentum</div>
                {metric_bar(row["momentum_score"], momentum_sub)}
            </div>

            <div class="metric">
                <div class="metric-title">🐋 Volume</div>
                {metric_bar(row["volume_score"], volume_sub)}
            </div>

            <div class="metric">
                <div class="metric-title">🔥 Volatility</div>
                {metric_bar(row["volatility_score"], vol_sub)}
            </div>

            <div class="metric">
                <div class="metric-title">📦 Compression</div>
                {metric_bar(row["compression_score"], comp_sub)}
            </div>

            <div class="metric">
                <div class="metric-title">🧲 OI Flow</div>
                {oi_centered_bar(row["oi_score"], row["oi_change_pct"])}
            </div>

            <div class="metric">
                <div class="metric-title">⭐ Setup Score</div>
                {metric_bar(row["setup_score"], setup_sub)}
            </div>

            <div class="metric">
                <div class="metric-title">🚀 Breakout Pressure</div>
                {metric_bar(row["breakout_pressure"], break_sub)}
            </div>

            <div class="metric">
                <div class="metric-title">💣 Liquidation Pressure</div>
                {metric_bar(row["liquidation_pressure"], liq_sub)}
            </div>
        </div>
    </div>
    """


# -------------------------------------------------
# LAYOUT
# -------------------------------------------------

def base_layout(title, body):
    return f"""
    <html>
    <head>
        <title>{title}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <style>
            body {{
                font-family: Arial, sans-serif;
                background: #0f172a;
                color: white;
                padding: 20px;
                margin: 0;
            }}
            h1 {{
                color: #38bdf8;
                margin-bottom: 10px;
            }}
            .card {{
                background: #1e293b;
                border: 1px solid #334155;
                border-radius: 14px;
                padding: 16px;
                margin-bottom: 16px;
            }}
            .controls {{
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                align-items: center;
            }}
            input, select, button {{
                padding: 9px 12px;
                border-radius: 10px;
                border: 1px solid #334155;
                background: #0f172a;
                color: white;
            }}
            button {{
                cursor: pointer;
            }}
            .muted {{
                color: #94a3b8;
            }}
            .coin-list {{
                display: flex;
                flex-direction: column;
                gap: 14px;
            }}
            .coin-card {{
                background: #1e293b;
                border: 1px solid #334155;
                border-radius: 16px;
                padding: 16px;
                transition: box-shadow 0.55s ease, border-color 0.55s ease, transform 0.35s ease;
            }}
            .coin-card.updated {{
                box-shadow: 0 0 18px rgba(56, 189, 248, 0.55);
                border-color: #38bdf8;
                transform: translateY(-1px);
            }}
            .coin-header {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 12px;
                margin-bottom: 14px;
            }}
            .coin-name {{
                font-size: 20px;
                font-weight: 700;
            }}
            .coin-sub {{
                color: #cbd5e1;
                font-size: 13px;
                margin-top: 4px;
            }}
            .danger-link {{
                color: #f87171;
                font-weight: 700;
                text-decoration: none;
            }}
            .bars-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
                gap: 14px;
            }}
            .metric {{
                background: #0f172a;
                border: 1px solid #334155;
                border-radius: 12px;
                padding: 12px;
            }}
            .metric-title {{
                font-size: 13px;
                color: #cbd5e1;
                font-weight: 700;
                margin-bottom: 8px;
            }}
            .bar-wrap {{
                display: flex;
                flex-direction: column;
                gap: 6px;
            }}
            .bar {{
                position: relative;
                background: #020617;
                border: 1px solid #334155;
                border-radius: 999px;
                overflow: hidden;
            }}
            .bar.centered {{
                background: linear-gradient(
                    to right,
                    rgba(239,68,68,0.08) 0%,
                    rgba(2,6,23,1) 50%,
                    rgba(34,197,94,0.08) 100%
                );
            }}
            .bar-mid {{
                position: absolute;
                left: 50%;
                top: 0;
                width: 1px;
                height: 100%;
                background: #64748b;
                z-index: 2;
            }}
            .bar-fill {{
                position: relative;
                height: 100%;
                border-radius: 999px;
                transition: width 0.45s ease, background-color 0.35s ease;
            }}
            .bar-fill.abs {{
                position: absolute;
                top: 0;
                transition: width 0.45s ease, left 0.45s ease, background-color 0.35s ease;
            }}
            .tiny {{
                font-size: 11px;
                color: #94a3b8;
            }}
            .bias {{
                font-weight: 700;
            }}
            .status-row {{
                display: flex;
                flex-wrap: wrap;
                gap: 14px;
                font-size: 12px;
                color: #94a3b8;
                margin-top: 8px;
            }}
        </style>
    </head>
    <body>
        {body}
    </body>
    </html>
    """


# -------------------------------------------------
# ROUTES
# -------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home():
    return RedirectResponse(url="/favorites")


@app.get("/favorites", response_class=HTMLResponse)
def favorites_page(tf: str = Query("15m")):
    if tf not in TIMEFRAME_OPTIONS:
        tf = "15m"

    rows_html = ""
    for i, coin in enumerate(favorites, start=1):
        rows_html += render_coin_row(coin, tf, i)

    body = f"""
        <h1>⭐ Favorites Watchlist</h1>
        <div class="muted" style="margin-bottom:12px;">Background refresh updates one coin at a time.</div>

        <div class="card">
            <form class="controls" method="get" action="/favorites">
                <label>Timeframe:</label>
                <select name="tf">
                    {"".join([f'<option value="{x}" {"selected" if x == tf else ""}>{x}</option>' for x in TIMEFRAME_OPTIONS])}
                </select>
                <button type="submit">Refresh View</button>
            </form>

            <form class="controls" method="get" action="/add" style="margin-top:10px;">
                <input name="coin" placeholder="BTC, ETH, SOL" />
                <input type="hidden" name="tf" value="{tf}" />
                <button type="submit">Add Coin</button>
            </form>

            <div class="status-row">
                <div>Gray = weak</div>
                <div>Blue = building</div>
                <div>Green = strong</div>
                <div>Purple = extreme</div>
            </div>
        </div>

        <div class="coin-list" id="coin-list">
            {rows_html if rows_html else '<div class="card">No favorites yet. Add a coin above.</div>'}
        </div>

        <script>
            const favorites = {favorites};
            const timeframe = "{tf}";
            let refreshIndex = 0;

            async function refreshOneCoin() {{
                if (!favorites.length) return;

                const coin = favorites[refreshIndex % favorites.length];
                refreshIndex += 1;

                try {{
                    const res = await fetch(`/api/coin?coin=${{encodeURIComponent(coin)}}&tf=${{encodeURIComponent(timeframe)}}`);
                    if (!res.ok) return;

                    const html = await res.text();
                    const wrapper = document.createElement("div");
                    wrapper.innerHTML = html.trim();

                    const newCard = wrapper.firstElementChild;
                    const oldCard = document.getElementById(`coin-card-${{coin}}`);

                    if (oldCard && newCard) {{
                        oldCard.replaceWith(newCard);
                        newCard.classList.add("updated");
                        setTimeout(() => {{
                            newCard.classList.remove("updated");
                        }}, 650);
                    }}
                }} catch (e) {{
                    console.log("refresh error", coin, e);
                }}
            }}

            setInterval(refreshOneCoin, 2000);
        </script>
    """
    return base_layout("Favorites Watchlist", body)


@app.get("/api/coin", response_class=HTMLResponse)
def api_coin(coin: str = Query(...), tf: str = Query("15m")):
    coin = coin.upper().strip()
    if tf not in TIMEFRAME_OPTIONS:
        tf = "15m"

    index = favorites.index(coin) + 1 if coin in favorites else 1
    return HTMLResponse(render_coin_row(coin, tf, index))


@app.get("/add")
def add_coin(coin: str = Query(...), tf: str = Query("15m")):
    coin = coin.upper().strip()
    if coin and coin not in favorites:
        favorites.append(coin)
    return RedirectResponse(url=f"/favorites?tf={tf}", status_code=302)


@app.get("/remove")
def remove_coin(coin: str = Query(...), tf: str = Query("15m")):
    coin = coin.upper().strip()
    if coin in favorites:
        favorites.remove(coin)
    return RedirectResponse(url=f"/favorites?tf={tf}", status_code=302)


@app.get("/debug", response_class=JSONResponse)
def debug(coin: str = Query("BTC"), tf: str = Query("15m")):
    return {
        "favorites": favorites,
        "timeframe": tf,
        "coin_result": analyze_coin(coin.upper().strip(), tf),
    }
