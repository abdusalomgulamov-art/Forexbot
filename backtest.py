import os
import pandas as pd
import requests
import yfinance as yf

TOKEN = os.environ["BOT_TOKEN"]
CHAT = os.environ["CHAT_ID"]
SYMBOLS = {
    "EURUSD=X": "EUR/USD", "GBPUSD=X": "GBP/USD", "USDJPY=X": "USD/JPY",
    "AUDUSD=X": "AUD/USD", "USDCAD=X": "USD/CAD", "USDCHF=X": "USD/CHF",
    "GC=F": "XAU/USD",
}
TFS = ["5m", "15m", "30m"]
ATR_SL, RR, MAX_HOLD = 1.5, 2.0, 60


def load(symbol, tf):
    df = yf.download(symbol, period="58d", interval=tf, progress=False, auto_adjust=False)
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
    return df.reset_index(drop=True) if len(df) > 260 else None


def signal(prev, last):
    if last.ema20 > last.ema50 > last.ema200 and last.Close > last.ema200:
        pb = prev.Low <= prev.ema20 or last.Low <= last.ema20
        ok = last.Close > last.ema20 and last.Close > last.Open
        if pb and ok and 45 <= last.rsi <= 68 and last.rsi > prev.rsi:
            return "BUY"
    if last.ema20 < last.ema50 < last.ema200 and last.Close < last.ema200:
        pb = prev.High >= prev.ema20 or last.High >= last.ema20
        ok = last.Close < last.ema20 and last.Close < last.Open
        if pb and ok and 32 <= last.rsi <= 55 and last.rsi < prev.rsi:
            return "SELL"
    return None


def backtest(df):
    res = []
    n = len(df)
    i = 205
    while i < n - 2:
        side = signal(df.iloc[i - 1], df.iloc[i])
        if not side:
            i += 1
            continue
        entry = df.iloc[i + 1].Open
        risk = ATR_SL * df.iloc[i].atr
        buy = side == "BUY"
        sl = entry - risk if buy else entry + risk
        tp = entry + risk * RR if buy else entry - risk * RR
        end = min(n - 1, i + MAX_HOLD)
        r, j = None, i + 1
        while j <= end:
            c = df.iloc[j]
            if buy:
                if c.Low <= sl:
                    r = -1.0
                    break
                if c.High >= tp:
                    r = RR
                    break
            else:
                if c.High >= sl:
                    r = -1.0
                    break
                if c.Low <= tp:
                    r = RR
                    break
            j += 1
        if r is None:
            j = end
            c = df.iloc[end]
            r = ((c.Close - entry) if buy else (entry - c.Close)) / risk
        res.append(r)
        i = j + 1
    return res


def stats(rs):
    if not rs:
        return "сделок нет"
    wins = [x for x in rs if x > 0]
    loss = [x for x in rs if x <= 0]
    pf = sum(wins) / abs(sum(loss)) if loss and sum(loss) != 0 else float("inf")
    eq, peak, dd = 0.0, 0.0, 0.0
    for x in rs:
        eq += x
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return (f"сделок {len(rs)} | винрейт {len(wins) / len(rs) * 100:.0f}% | "
            f"ср. {sum(rs) / len(rs):+.2f}R | итого {sum(rs):+.1f}R | "
            f"PF {pf:.2f} | просадка {dd:.1f}R")


by_tf = {tf: [] for tf in TFS}
by_sym = {}
for sym, name in SYMBOLS.items():
    by_sym[name] = []
    for tf in TFS:
        try:
            df = load(sym, tf)
            if df is None:
                continue
            r = backtest(df)
            by_tf[tf] += r
            by_sym[name] += r
        except Exception as e:
            print(name, tf, e)

allr = [x for v in by_tf.values() for x in v]
lines = ["📊 Бэктест за ~58 дней (SL 1.5 ATR, TP 1:2)",
         "Безубыток при винрейте 33%. R = размер риска на сделку.", "",
         "По таймфреймам:"]
lines += [f"• {tf}: {stats(by_tf[tf])}" for tf in TFS]
lines += ["", "По инструментам:"]
lines += [f"• {k}: {stats(v)}" for k, v in by_sym.items()]
lines += ["", f"ВСЕГО: {stats(allr)}",
          "", "⚠️ Спред и комиссии не учтены. Выборка небольшая."]
text = "\n".join(lines)
print(text)
requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
              data={"chat_id": CHAT, "text": text}, timeout=15)
