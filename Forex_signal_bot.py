import asyncio
import json
import os

import pandas as pd
import yfinance as yf
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SYMBOLS = {
    "EURUSD=X": "EUR/USD", "GBPUSD=X": "GBP/USD", "USDJPY=X": "USD/JPY",
    "AUDUSD=X": "AUD/USD", "USDCAD=X": "USD/CAD", "USDCHF=X": "USD/CHF",
    "GC=F": "XAU/USD (золото)",
}
TIMEFRAMES = ["5m", "15m", "30m"]
SCAN_EVERY = 60
ATR_SL = 1.5
RR = 2.0
SUBS_FILE = "subscribers.json"

sent = set()
first_run = True


def load_subs():
    if os.path.exists(SUBS_FILE):
        with open(SUBS_FILE) as f:
            return set(json.load(f))
    return set()


def save_subs(subs):
    with open(SUBS_FILE, "w") as f:
        json.dump(list(subs), f)


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
    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["rsi"] = 100 - 100 / (1 + up / dn)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    return df if len(df) > 210 else None


def trend(row):
    if row.ema20 > row.ema50 > row.ema200 and row.Close > row.ema200:
        return "BUY"
    if row.ema20 < row.ema50 < row.ema200 and row.Close < row.ema200:
        return "SELL"
    return None


def check(df):
    prev, last = df.iloc[-3], df.iloc[-2]
    t = trend(last)
    if t == "BUY":
        pullback = prev.Low <= prev.ema20 or last.Low <= last.ema20
        confirm = last.Close > last.ema20 and last.Close > last.Open
        if pullback and confirm and 45 <= last.rsi <= 68 and last.rsi > prev.rsi:
            return "BUY", last
    if t == "SELL":
        pullback = prev.High >= prev.ema20 or last.High >= last.ema20
        confirm = last.Close < last.ema20 and last.Close < last.Open
        if pullback and confirm and 32 <= last.rsi <= 55 and last.rsi < prev.rsi:
            return "SELL", last
    return None, last


def scan():
    global first_run
    messages = []
    for sym, name in SYMBOLS.items():
        found, trends = [], {}
        for tf in TIMEFRAMES:
            try:
                df = load(sym, tf)
                if df is None:
                    continue
                side, last = check(df)
                trends[tf] = trend(last)
                if side:
                    found.append((tf, side, last, df.index[-2]))
            except Exception as e:
                print(f"{name} {tf}: {e}")
        for tf, side, last, ts in found:
            key = (sym, tf, str(ts))
            if key in sent:
                continue
            sent.add(key)
            if first_run:
                continue
            agree = sum(1 for v in trends.values() if v == side)
            price, atr = float(last.Close), float(last.atr)
            sl = price - ATR_SL * atr if side == "BUY" else price + ATR_SL * atr
            tp = price + ATR_SL * atr * RR if side == "BUY" else price - ATR_SL * atr * RR
            strength = {3: "🔥 СИЛЬНЫЙ", 2: "✅ хороший"}.get(agree, "⚠️ слабый")
            messages.append(
                f"{'🟢' if side == 'BUY' else '🔴'} {side} {name} [{tf}]\n"
                f"Вход: {price:.5f}\nSL: {sl:.5f}\nTP: {tp:.5f} (RR 1:{RR:g})\n"
                f"RSI: {last.rsi:.0f} | Тренд совпадает на {agree}/3 ТФ → {strength}"
            )
    first_run = False
    return messages


async def scan_job(context: ContextTypes.DEFAULT_TYPE):
    messages = await asyncio.to_thread(scan)
    for chat_id in load_subs():
        for m in messages:
            try:
                await context.bot.send_message(chat_id, m)
            except Exception as e:
                print("Не отправилось", chat_id, e)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я ищу входы по тренду на форексе и золоте (5m, 15m, 30m).\n\n"
        "/on — включить уведомления\n/off — выключить\n/status — состояние\n\n"
        "⚠️ Это не финансовый совет. Тестируй на демо."
    )


async def on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subs = load_subs()
    subs.add(update.effective_chat.id)
    save_subs(subs)
    await update.message.reply_text("✅ Уведомления включены.")


async def off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subs = load_subs()
    subs.discard(update.effective_chat.id)
    save_subs(subs)
    await update.message.reply_text("🔕 Уведомления выключены.")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active = update.effective_chat.id in load_subs()
    await update.message.reply_text(
        f"Уведомления: {'включены ✅' if active else 'выключены 🔕'}\n"
        f"Инструменты: {', '.join(SYMBOLS.values())}\n"
        f"Таймфреймы: {', '.join(TIMEFRAMES)}\nПроверка: каждые {SCAN_EVERY} сек."
    )


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("on", on))
    app.add_handler(CommandHandler("off", off))
    app.add_handler(CommandHandler("status", status))
    app.job_queue.run_repeating(scan_job, interval=SCAN_EVERY, first=5)
    print("Бот запущен.")
    app.run_polling()


if __name__ == "__main__":
    main()
