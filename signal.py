# requirements: requests, pandas, numpy
import os, time, math, requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

BASE = "https://api.bitvavo.com/v2"
TOP_N = 80
NEAR_LOW_PCT = 3.0
RSI_MAX = 35.0
CANDLE_INTERVAL = "1h"
LOOKBACK_DAYS = 30
SLEEP_BETWEEN = 0.12

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

def get_markets():
    r = requests.get(f"{BASE}/ticker/24h", timeout=30)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    df = df[df["market"].str.endswith("-EUR")].copy()
    for col in ["last","low24h","high24h","volume","priceChange","priceChangePercentage"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values("volume", ascending=False)

def get_candles(market, interval="1h", days=30):
    end_ms = int(time.time()*1000)
    start_ms = end_ms - days*24*60*60*1000
    r = requests.get(f"{BASE}/{market}/candles",
                     params={"interval":interval,"start":start_ms,"end":end_ms},
                     timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data:
        return pd.DataFrame()
    cols = ["time","open","high","low","close","volume"]
    df = pd.DataFrame(data, columns=cols)
    for c in cols:
        if c != "time":
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

def rsi(series, period=14):
    delta = series.diff()
    up = np.where(delta > 0, delta, 0.0)
    down = np.where(delta < 0, -delta, 0.0)
    roll_up = pd.Series(up).rolling(period).mean()
    roll_down = pd.Series(down).rolling(period).mean()
    rs = roll_up / (roll_down + 1e-12)
    return 100.0 - (100.0 / (1.0 + rs))

def pick_candidate():
    markets = get_markets()
    picks = []
    for _, row in markets.head(TOP_N).iterrows():
        mkt = row["market"]
        candles = get_candles(mkt, CANDLE_INTERVAL, LOOKBACK_DAYS)
        if candles.empty or candles["close"].isna().all():
            continue
        close = candles["close"]
        thirty_low = close.min()
        last = close.iloc[-1]
        pct_above_low = (last - thirty_low) / max(thirty_low, 1e-12) * 100.0
        rsi14 = rsi(close).iloc[-1]
        if np.isfinite(rsi14) and pct_above_low <= NEAR_LOW_PCT and rsi14 < RSI_MAX:
            picks.append({
                "market": mkt,
                "last": last,
                "low30d": thirty_low,
                "rsi14": float(rsi14),
                "vol24h": float(row.get("volume", np.nan)),
                "pct_above_low": float(pct_above_low)
            })
        time.sleep(SLEEP_BETWEEN)
    if not picks:
        return None, []
    picks.sort(key=lambda x: (-math.isnan(x["vol24h"]), -x["vol24h"]))
    return picks[0], picks

def send_telegram(text: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN of TELEGRAM_CHAT_ID ontbreekt.")
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    r = requests.post(url, json=payload, timeout=30)
    r.raise_for_status()

def main():
    # Tijdstempel NL (Europe/Amsterdam). GitHub draait in UTC, dit is alleen voor de tekst.
    nl_now = datetime.utcnow().replace(tzinfo=timezone.utc).astimezone(
        tz=timezone(timedelta(hours=2)))
    top, picks = pick_candidate()
    if not top:
        send_telegram(f"🕘 {nl_now:%Y-%m-%d} — Geen kandidaat (≤{NEAR_LOW_PCT:.0f}% boven 30d-low & RSI<{RSI_MAX:.0f}).")
        return
    lines = [
        f"🕘 {nl_now:%Y-%m-%d} — <b>Dagelijks Bitvavo-signaal</b> (geen financieel advies)",
        f"• Markt: <b>{top['market']}</b>",
        f"• Laatste prijs: €{top['last']:.6f}",
        f"• 30d low: €{top['low30d']:.6f} (nu {top['pct_above_low']:.2f}% erboven)",
        f"• RSI(14): {top['rsi14']:.1f}",
    ]
    for alt in picks[1:3]:
        lines.append(f"◦ Alternatief: {alt['market']} (RSI {alt['rsi14']:.1f}, +{alt['pct_above_low']:.2f}% t.o.v. 30d-low)")
    send_telegram("\n".join(lines))

if __name__ == "__main__":
    main()
