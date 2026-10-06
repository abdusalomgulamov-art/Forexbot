import os
import numpy as np
import pandas as pd
import requests
import yfinance as yf

TOKEN = os.environ["BOT_TOKEN"]
CHAT = os.environ["CHAT_ID"]
SPREAD = 0.35
ATR_SL = 1.5
MAX_HOLD = 60
SETS = {"1h": "700d", "30m": "58d", "15m": "58d"}


def wilder(s):
    return s.ewm(alpha=1 / 14, adjust=False).mean()


def load(tf, period):
    d = yf.download("GC=F", period=period, interval=tf, progress=False, auto_adjust=False)
    if d.empty:
        return None
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d.dropna()
    idx = d.index
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    d["hour"] = idx.hour
    d = d.reset_index(drop=True)
    c, h, l = d["Close"], d["High"], d["Low"]
    d["ema20"] = c.ewm(span=20, adjust=False).mean()
    d["ema50"] = c.ewm(span=50, adjust=False).mean()
    d["ema200"] = c.ewm(span=200, adjust=False).mean()
    dl = c.diff()
    d["rsi"] = 100 - 100 / (1 + wilder(dl.clip(lower=0)) / wilder(-dl.clip(upper=0)))
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    d["atr"] = wilder(tr)
    upm, dnm = h.diff(), -l.diff()
    plus = ((upm > dnm) & (upm > 0)) * upm
    minus = ((dnm > upm) & (dnm > 0)) * dnm
    pdi = 100 * wilder(plus) / d["atr"]
    mdi = 100 * wilder(minus) / d["atr"]
    d["adx"] = wilder(100 * (pdi - mdi).abs() / (pdi + mdi))
    return d if len(d) > 300 else None


def signals(d):
    c, o, h, l = d.Close, d.Open, d.High, d.Low
    up = (d.ema20 > d.ema50) & (d.ema50 > d.ema200) & (c > d.ema200)
    dn = (d.ema20 < d.ema50) & (d.ema50 < d.ema200) & (c < d.ema200)
    sess = d.hour.between(7, 20)
    bull, bear = c > o, c < o
    pa = (up & ((l.shift() <= d.ema20.shift()) | (l <= d.ema20)) & (c > d.ema20)
          & bull & d.rsi.between(45, 68) & (d.rsi > d.rsi.shift()))
    pb = (dn & ((h.shift() >= d.ema20.shift()) | (h >= d.ema20)) & (c < d.ema20)
          & bear & d.rsi.between(32, 55) & (d.rsi < d.rsi.shift()))
    f = (d.adx > 20) & sess
    hi = h.rolling(20).max().shift()
    lo = l.rolling(20).min().shift()
    cb = (d.ema50 > d.ema200) & (c > hi) & (d.adx > 25) & sess
    cs = (d.ema50 < d.ema200) & (c < lo) & (d.adx > 25) & sess
    db = up & (d.adx > 22) & (l <= d.ema50) & (c > d.ema20) & bull & sess
    ds = dn & (d.adx > 22) & (h >= d.ema50) & (c < d.ema20) & bear & sess
    g = lambda b, s: b.astype(int) - s.astype(int)
    return {"A": g(pa, pb), "B": g(pa & f, pb & f), "C": g(cb, cs), "D": g(db, ds)}


def simulate(d, sig, rr):
    o, h, l, c = d.Open.values, d.High.values, d.Low.values, d.Close.values
    atr, s = d.atr.values, sig.values
    n = len(d)
    res = []
    i = 205
    while i < n - 2:
        if s[i] == 0 or np.isnan(atr[i]):
            i += 1
            continue
        buy = s[i] > 0
        entry = o[i + 1]
        risk = ATR_SL * atr[i]
        sl = entry - risk if buy else entry + risk
        tp = entry + risk * rr if buy else entry - risk * rr
        end = min(n - 1, i + MAX_HOLD)
        r, j = None, i + 1
        while j <= end:
            if buy:
                if l[j] <= sl:
                    r = -1.0
                    break
                if h[j] >= tp:
                    r = rr
                    break
            else:
                if h[j] >= sl:
                    r = -1.0
                    break
                if l[j] <= tp:
                    r = rr
                    break
            j += 1
        if r is None:
            j = end
            r = ((c[j] - entry) if buy else (entry - c[j])) / risk
        res.append((i, r - SPREAD / risk))
        i = j + 1
    return res


def stats(res, n):
    if len(res) < 30:
        return None
    arr = np.array([r for _, r in res])
    loss = -arr[arr <= 0].sum()
    return dict(n=len(arr), wr=(arr > 0).mean() * 100, avg=arr.mean(),
                pf=arr[arr > 0].sum() / loss if loss > 0 else 99,
                h1=sum(r for i, r in res if i < n / 2),
                h2=sum(r for i, r in res if i >= n / 2))


rows = []
for tf, period in SETS.items():
    try:
        d = load(tf, period)
        if d is None:
            continue
        for k, sig in signals(d).items():
            for rr in (1.5, 2.0):
                st = stats(simulate(d, sig, rr), len(d))
                if st:
                    rows.append((tf, k, rr, st))
    except Exception as e:
        print(tf, e)

rows.sort(key=lambda x: x[3]["avg"], reverse=True)
lines = [f"🥇 Бэктест золота (GC=F), спред ${SPREAD} учтён",
         "A откат EMA20 | B откат+ADX+сессия | C пробой 20 | D откат EMA50+ADX", ""]
for tf, k, rr, s in rows[:12]:
    lines.append(f"{tf} {k} RR1:{rr:g} | {s['n']} сд | {s['wr']:.0f}% | "
                 f"{s['avg']:+.2f}R | PF {s['pf']:.2f} | половины {s['h1']:+.0f}/{s['h2']:+.0f}R")
lines += ["", "Хороший вариант: плюс в обеих половинах и PF > 1.2.",
          "⚠️ Прошлое не гарантирует будущее."]
text = "\n".join(lines)
print(text)
requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
              data={"chat_id": CHAT, "text": text}, timeout=15)
