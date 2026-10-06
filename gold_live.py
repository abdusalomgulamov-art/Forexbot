import json
import os
import time

import pandas as pd
import requests

TOKEN = os.environ["BOT_TOKEN"]
CHAT = os.environ["CHAT_ID"]
KEY = os.environ["TWELVE_KEY"]
SYMBOL = "XAU/USD"
RR, ATR_SL = 1.5, 1.5
RUN_MINUTES = 31
COOLDOWN_MIN = 60
STATE = "gold_state.json"


def send(text):
    r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      data={"chat_id": CHAT, "text": text}, timeout=15)
    print(r.status_code, text[:80])


def fetch():
    r = requests.get("https://api.twelvedata.com/time_series", params={
        "symbol": SYMBOL, "interval": "5min", "outputsize": 2500,
        "timezone": "UTC", "order": "ASC", "apikey": KEY}, timeout=25).json()
    if "values" not in r:
        raise RuntimeError(str(r)[:200])
    d = pd.DataFrame(r["values"])
    d["datetime"] = pd.to_datetime(d["datetime"])
    for c in ("open", "high", "low", "close"):
        d[c] = d[c].astype(float)
    return d.set_index("datetime")[["open", "high", "low", "close"]].sort_index()


def wilder(s):
    return s.ewm(alpha=1 / 14, adjust=False).mean()


def indicators(m):
    c, h, l = m.close, m.high, m.low
    m["ema50"] = c.ewm(span=50, adjust=False).mean()
    m["ema200"] = c.ewm(span=200, adjust=False).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    m["atr"] = wilder(tr)
    upm, dnm = h.diff(), -l.diff()
    plus = ((upm > dnm) & (upm > 0)) * upm
    minus = ((dnm > upm) & (dnm > 0)) * dnm
    pdi = 100 * wilder(plus) / m["atr"]
    mdi = 100 * wilder(minus) / m["atr"]
    m["adx"] = wilder(100 * (pdi - mdi).abs() / (pdi + mdi))
    m["hi"] = h.rolling(20).max().shift()
    m["lo"] = l.rolling(20).min().shift()
    return m


def process(slot, state):
    d = fetch()
    d = d[d.index < slot]
    if d.empty or d.index[-1] != slot - pd.Timedelta(minutes=5):
        return False  # данные закрытой свечи ещё не пришли
    m = pd.DataFrame({
        "open": d.open.resample("15min").first(),
        "high": d.high.resample("15min").max(),
        "low": d.low.resample("15min").min(),
        "close": d.close.resample("15min").last(),
    }).dropna()
    m = indicators(m)
    x = m.iloc[-1]
    t = m.index[-1]
    sess = 7 <= t.hour <= 20
    side = None
    if x.ema50 > x.ema200 and x.close > x.hi and x.adx > 25 and sess:
        side = "BUY"
    elif x.ema50 < x.ema200 and x.close < x.lo and x.adx > 25 and sess:
        side = "SELL"
    if not side:
        return True
    last = state.get("last")
    if last and (t - pd.Timestamp(last)) < pd.Timedelta(minutes=COOLDOWN_MIN):
        return True
    state["last"] = str(t)
    risk = ATR_SL * x.atr
    buy = side == "BUY"
    sl = x.close - risk if buy else x.close + risk
    tp = x.close + risk * RR if buy else x.close - risk * RR
    send(f"{'🟢' if buy else '🔴'} {side} XAU/USD [15m, пробой]\n"
         f"Вход: по рынку, около {x.close:.2f}\n"
         f"SL: {sl:.2f} (риск ${risk:.2f})\n"
         f"TP: {tp:.2f} (RR 1:{RR:g})\n"
         f"ADX {x.adx:.0f} | свеча {t:%H:%M} UTC закрыта")
    return True


def main():
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    if os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch":
        try:
            d = fetch()
            send(f"✅ Скринер золота подключён. Последний 5m бар {d.index[-1]} UTC, "
                 f"цена {d.close.iloc[-1]:.2f}")
        except Exception as e:
            send(f"❌ Нет данных золота из Twelve Data: {e}")
    end = time.time() + RUN_MINUTES * 60
    done, next_try = None, 0
    try:
        while time.time() < end:
            now = pd.Timestamp.utcnow().tz_localize(None)
            slot = now.floor("15min")
            age = (now - slot).total_seconds()
            cstart = slot - pd.Timedelta(minutes=15)
            active = slot.weekday() < 5 and 7 <= cstart.hour <= 20
            if slot != done and active and age >= 3 and time.time() >= next_try:
                try:
                    finished = process(slot, state)
                except Exception as e:
                    print("Ошибка:", e)
                    finished = False
                if finished or age > 90:
                    done = slot
                else:
                    next_try = time.time() + 15
            time.sleep(3)
    finally:
        json.dump(state, open(STATE, "w"))


main()
