import hashlib
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import requests

TOKEN = os.environ["BOT_TOKEN"]
CHAT = os.environ["CHAT_ID"]
RUN_MINUTES = 31
POLL = 60
MAX_AGE_MIN = 45
MAX_PER_CYCLE = 3
STATE = "world_state.json"
TZ = ZoneInfo("Asia/Dushanbe")

FEEDS = {
    "Google News": "https://news.google.com/rss/search?q=(war+OR+missile+OR+airstrike+OR+invasion+OR+sanctions+OR+tariffs+OR+OPEC+OR+%22emergency+meeting%22+OR+default+OR+%22market+crash%22)+when:1h&hl=en-US&gl=US&ceid=US:en",
    "BBC": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    "CNBC": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
}

CATS = [
    ("⚔️ Война / конфликт",
     ["war", "invasion", "invade", "missile", "airstrike", "air strike", "drone attack",
      "shelling", "ceasefire", "nuclear", "bombing", "hostage", "troops"],
     "Обычно: страх на рынке. Золото, доллар и нефть растут, акции падают."),
    ("🏛 Политика / санкции",
     ["sanction", "tariff", "trade war", "embargo", "coup", "impeach", "martial law",
      "emergency meeting", "export ban"],
     "Обычно: неопределённость. Золото и защитные валюты (доллар, франк, иена) растут."),
    ("🏦 Центробанки",
     ["emergency rate", "rate cut", "rate hike", "fed chair", "powell", "intervention"],
     "Жёстче ожиданий: доллар сильнее, золото слабее. Мягче: наоборот."),
    ("🛢 Нефть / энергия",
     ["opec", "strait of hormuz", "pipeline", "oil price", "refinery"],
     "Нефть влияет на инфляцию и валюты. Рост нефти поддерживает CAD и NOK."),
    ("💥 Кризис на рынках",
     ["market crash", "plunge", "collapse", "default", "bank run", "circuit breaker",
      "sell-off", "selloff", "flash crash", "bankruptcy"],
     "Обычно бегство в защитные активы: золото и облигации. В панике возможны рывки в обе стороны."),
]
PATTERNS = [(n, re.compile(r"\b(?:%s)s?\b" % "|".join(re.escape(w) for w in ws), re.I), h)
            for n, ws, h in CATS]


def send(text):
    r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      data={"chat_id": CHAT, "text": text, "disable_web_page_preview": True},
                      timeout=15)
    print(r.status_code, text[:60].replace("\n", " "))


def fetch(url):
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        try:
            ts = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc)
        except Exception:
            ts = None
        out.append((title, link, ts))
    return out


def classify(title):
    for name, pat, hint in PATTERNS:
        if pat.search(title):
            return name, hint
    return None


def words(t):
    return set(re.findall(r"[a-z0-9]+", t.lower()))


def similar(a, b):
    return len(a & b) / max(1, len(a | b)) > 0.55


def poll(state, silent):
    now = datetime.now(timezone.utc)
    seen = set(state["seen"])
    new = []
    for name, url in FEEDS.items():
        try:
            items = fetch(url)
        except Exception as e:
            print(name, "ошибка:", e)
            continue
        for title, link, ts in items:
            key = hashlib.md5(title.encode()).hexdigest()[:12]
            if key in seen:
                continue
            seen.add(key)
            state["seen"].append(key)
            if silent or ts is None or (now - ts).total_seconds() > MAX_AGE_MIN * 60:
                continue
            cat = classify(title)
            if not cat:
                continue
            w = words(title)
            if any(similar(w, words(r)) for r in state["recent"]):
                continue
            state["recent"].append(title)
            new.append((ts, name, title, link, cat))
    state["seen"] = state["seen"][-3000:]
    state["recent"] = state["recent"][-150:]
    new.sort(key=lambda x: x[0])
    for ts, src, title, link, (cname, hint) in new[:MAX_PER_CYCLE]:
        clean = title.rsplit(" - ", 1)
        head = clean[0]
        origin = clean[1] if len(clean) > 1 and src == "Google News" else src
        send(f"{cname}\n{head}\n"
             f"Источник: {origin} | {ts.astimezone(TZ):%H:%M} по Душанбе\n{link}\n\n"
             f"{hint}\n"
             "Первые минуты спреды и скачки огромные. Не спеши входить.")
    if len(new) > MAX_PER_CYCLE:
        send(f"Ещё важных заголовков за эту минуту: {len(new) - MAX_PER_CYCLE}")


def check_feeds():
    lines = []
    for name, url in FEEDS.items():
        try:
            lines.append(f"✓ {name}: {len(fetch(url))} новостей")
        except Exception as e:
            lines.append(f"✗ {name}: {str(e)[:60]}")
    return "✅ Радар мировых новостей подключён.\n" + "\n".join(lines)


def main():
    first = not os.path.exists(STATE)
    state = {"seen": [], "recent": []} if first else json.load(open(STATE))
    if os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch":
        send(check_feeds())
    end = time.time() + RUN_MINUTES * 60
    try:
        while time.time() < end:
            try:
                poll(state, silent=first)
            except Exception as e:
                print("Ошибка:", e)
            first = False
            time.sleep(POLL)
    finally:
        json.dump(state, open(STATE, "w"))


main()
