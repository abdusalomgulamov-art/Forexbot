import json, os
from datetime import datetime, timedelta, timezone
import pandas as pd
import requests
import yfinance as yf

TOKEN = os.environ["BOT_TOKEN"]
CHAT = os.environ["CHAT_ID"]
SYMBOLS = {
    "EURUSD=X": "EUR/USD", "GBPUSD=X": "GBP/USD", "USDJPY=X": "USD/JPY",
    "AUDUSD=X": "AUD/USD", "USDCAD=X": "USD/CAD", "USDCHF=X": "USD/CHF",
    "GC=F": "XAU/USD (золото)",
}
TF = {"5m": 5, "15m": 15, "30m": 30}
ATR_SL, RR = 1.5, 2.0
FILE = "sent.json"


def send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT, "text": text}, timeout=15)
    print(r.status_code, r.text[:100])


def load(symbol, tf):
    df = yf.download(symbol, period="10d", interval=tf, progress=False, auto_adjust=False)
    if df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    c, h, l = df["Close"], df["High"], df["Low"]
    df["ema20"] = c.ewm(span=20, adjust=False).mean()
    df["ema50"] = c.ewm(span=50, adjust=False).mean()
    df["ema200"] = c.ewm(span=200, adjust=False).mean()
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["rsi"] = 100 - 100 / (1 + up / dn)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    return df if len(df) > 210 else None


def trend(r):
    if r.ema20 > r.ema50 > r.ema200 and r.Close > r.ema200:
        return "BUY"
    if r.ema20 < r.ema50 < r.ema200 and r.Close < r.ema200:
        return "SELL"
    return None


def check(df):
    prev, last = df.iloc[-3], df.iloc[-2]
    t = trend(last)
    if t == "BUY":
        pb = prev.Low <= prev.ema20 or last.Low <= last.ema20
        ok = last.Close > last.ema20 and last.Close > last.Open
        if pb and ok and 45 <= last.rsi <= 68 and last.rsi > prev.rsi:
            return "BUY", last
    if t == "SELL":
        pb = prev.High >= prev.ema20 or last.High >= last.ema20
        ok = last.Close < last.ema20 and last.Close < last.Open
        if pb and ok and 32 <= last.rsi <= 55 and last.rsi < prev.rsi:
            return "SELL", last
    return None, last


def main():
    now = datetime.now(timezone.utc)
    sent = json.load(open(FILE)) if os.path.exists(FILE) else []
    if os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch":
        send("✅ Скринер подключён. Сигналы придут, когда появятся входы.")
    for sym, name in SYMBOLS.items():
        found, trends = [], {}
        for tf in TF:
            try:
                df = load(sym, tf)
                if df is None:
                    continue
                side, last = check(df)
                trends[tf] = trend(last)
                if side:
                    found.append((tf, side, last, df.index[-2]))
            except Exception as e:
                print(name, tf, e)
        for tf, side, last, ts in found:
            key = f"{sym}|{tf}|{ts}"
            if key in sent:
                continue
            t = ts.to_pydatetime()
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if now - (t + timedelta(minutes=TF[tf])) > timedelta(minutes=2 * TF[tf]):
                continue
            sent.append(key)
            agree = sum(1 for v in trends.values() if v == side)
            price, atr = float(last.Close), float(last.atr)
            k = ATR_SL * atr
            sl = price - k if side == "BUY" else price + k
            tp = price + k * RR if side == "BUY" else price - k * RR
            s = {3: "🔥 СИЛЬНЫЙ", 2: "✅ хороший"}.get(agree, "⚠️ слабый")
            send(f"{'🟢' if side == 'BUY' else '🔴'} {side} {name} [{tf}]\n"
                 f"Вход: {price:.5f}\nSL: {sl:.5f}\nTP: {tp:.5f} (RR 1:{RR:g})\n"
                 f"RSI: {last.rsi:.0f} | Тренд совпадает на {agree}/3 ТФ → {s}")
    json.dump(sent[-300:], open(FILE, "w"))


main()
