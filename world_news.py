import hashlib
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import requests

TOKEN = os.environ["BOT_TOKEN"]
CHAT = os.environ["CHAT_ID"]
RUN_MINUTES = 31
POLL = 60
MAX_AGE_MIN = 90
MAX_PER_DAY = 3
MIN_GAP_MIN = 45
SCORE_SEND = 6
SCORE_SOLO = 9
CLUSTER_HOURS = 6
STATE = "world_state.json"
TZ = ZoneInfo("Asia/Dushanbe")

FEEDS = {
    "Google News": "https://news.google.com/rss/search?q=(war+OR+missile+OR+airstrike+OR+invasion+OR+sanctions+OR+tariffs+OR+OPEC+OR+%22emergency+meeting%22+OR+default+OR+%22market+crash%22)+when:2h&hl=en-US&gl=US&ceid=US:en",
    "Google Gold": "https://news.google.com/rss/search?q=(gold+price+OR+Fed+OR+%22dollar+index%22+OR+%22Treasury+yields%22+OR+%22central+bank%22)+when:2h&hl=en-US&gl=US&ceid=US:en",
    "BBC": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    "CNBC": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
}


def rx(words):
    return [re.compile(r"\b%ss?\b" % re.escape(w), re.I) for w in words]


A = rx(["emergency rate", "emergency meeting", "surprise rate", "declares war",
        "declaration of war", "nuclear", "bank collapse", "bank run", "banking crisis",
        "debt ceiling", "us default", "credit rating downgrade", "market crash",
        "flash crash", "circuit breaker", "trading halted", "financial crisis",
        "gold hits", "gold surges", "gold plunges", "gold slumps", "gold jumps",
        "gold tumbles", "gold record", "dollar plunges", "dollar tumbles",
        "dollar surges", "oil surges", "oil plunges", "strait of hormuz"])
B = rx(["invasion", "airstrike", "air strike", "missile strike", "missile attack",
        "ceasefire", "sanction", "tariff", "trade war", "opec", "rate cut", "rate hike",
        "rate decision", "federal reserve", "fed", "powell", "bank failure", "default",
        "downgrade", "plunge", "crash", "collapse", "sell-off", "selloff", "yields",
        "treasury", "inflation", "recession", "central bank", "taiwan", "blockade",
        "embargo"])
C = rx(["dollar", "gold", "oil", "iran", "israel", "russia", "china", "ukraine",
        "nato", "white house", "wall street", "stocks", "markets"])
NOISE = re.compile(r"\b(football|soccer|cricket|nba|nfl|tennis|olympic|world cup|premier league|"
                   r"celebrity|film|movie|album|concert|recipe|fashion|royal family|video game|trailer)s?\b", re.I)

CATS = [
    ("⚔️ Война / геополитика",
     re.compile(r"\b(invasion|airstrike|air strike|missile|nuclear|war|ceasefire|blockade|hormuz|taiwan)\b", re.I),
     "Обычно: страх на рынке, золото и доллар растут, акции падают. В первые минуты реакция резкая."),
    ("💥 Кризис на рынках",
     re.compile(r"\b(crash|collapse|bank run|default|downgrade|recession|plunge|flash|circuit breaker|financial crisis|banking crisis)\b", re.I),
     "Обычно бегство в защитные активы: золото растёт. В панике возможны рывки в обе стороны."),
    ("🏦 ФРС / ставки / доллар",
     re.compile(r"\b(fed|powell|rate|inflation|treasury|yields|central bank|dollar)\b", re.I),
     "Жёстче ожиданий: доллар сильнее, золото слабее. Мягче: золото растёт."),
    ("🛢 Нефть",
     re.compile(r"\b(opec|oil)\b", re.I),
     "Рост нефти разгоняет инфляцию: золото обычно поддерживается."),
    ("🥇 Золото",
     re.compile(r"\bgold\b", re.I),
     "Это движение самого золота. Проверь график, прежде чем входить."),
]


def score(t):
    a = sum(1 for p in A if p.search(t))
    b = sum(1 for p in B if p.search(t))
    c = sum(1 for p in C if p.search(t))
    return min(a, 2) * 5 + min(b, 3) * 3 + min(c, 3)


def category(t):
    for name, p, hint in CATS:
        if p.search(t):
            return name, hint
    return "🌍 Мировые рынки", "Следи за реакцией доллара и золота."


def words(t):
    return {x for x in re.findall(r"[a-z0-9]+", t.lower()) if len(x) >= 4}


def similar(a, b):
    return len(a & b) / max(1, len(a | b)) >= 0.3


def send(text):
    r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      data={"chat_id": CHAT, "text": text, "disable_web_page_preview": True},
                      timeout=15)
    print(r.status_code, text[:60].replace("\n", " "))


def fetch(name, url):
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    out = []
    for it in ET.fromstring(r.content).iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        try:
            ts = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc)
        except Exception:
            ts = None
        src = name
        if name.startswith("Google") and " - " in title:
            title, src = title.rsplit(" - ", 1)
        out.append((title.strip(), link, ts, src.strip()))
    return out


def poll(state, silent):
    now = datetime.now(timezone.utc)
    seen = set(state["seen"])
    cut = now - timedelta(hours=CLUSTER_HOURS)
    state["clusters"] = [c for c in state["clusters"] if datetime.fromisoformat(c["ts"]) > cut]
    cl = state["clusters"]
    for name, url in FEEDS.items():
        try:
            items = fetch(name, url)
        except Exception as e:
            print(name, "ошибка:", e)
            continue
        for title, link, ts, src in items:
            key = hashlib.md5(title.encode()).hexdigest()[:12]
            if key in seen:
                continue
            seen.add(key)
            state["seen"].append(key)
            if silent or ts is None or (now - ts).total_seconds() > MAX_AGE_MIN * 60:
                continue
            if NOISE.search(title):
                continue
            sc = score(title)
            w = words(title)
            hit = next((c for c in cl if similar(w, set(c["w"]))), None)
            if hit:
                if src not in hit["src"]:
                    hit["src"].append(src)
                if sc > hit["score"]:
                    hit["score"], hit["title"], hit["link"] = sc, title, link
            elif sc >= 4:
                cl.append({"title": title, "w": sorted(w), "src": [src], "score": sc,
                           "link": link, "ts": now.isoformat(), "sent": False})
    state["seen"] = state["seen"][-3000:]

    day = str(now.astimezone(TZ).date())
    if state["day"]["d"] != day:
        state["day"] = {"d": day, "n": 0}
    last = datetime.fromisoformat(state["last"]) if state["last"] else None
    ready = [c for c in cl if not c["sent"] and
             (c["score"] >= SCORE_SOLO or (c["score"] >= SCORE_SEND and len(c["src"]) >= 2))]
    ready.sort(key=lambda c: -c["score"])
    for c in ready:
        if state["day"]["n"] >= MAX_PER_DAY:
            break
        if last and (now - last) < timedelta(minutes=MIN_GAP_MIN):
            break
        cat, hint = category(c["title"])
        send(f"🚨 Сильная новость для золота\n{cat}\n{c['title']}\n"
             f"Источники: {', '.join(c['src'][:4])}\n"
             f"{now.astimezone(TZ):%H:%M} по Душанбе\n{c['link']}\n\n{hint}\n"
             "Первые минуты спреды и скачки огромные. Не входи сразу, дождись, пока цена успокоится.")
        c["sent"] = True
        state["day"]["n"] += 1
        last = now
        state["last"] = now.isoformat()


def check_feeds():
    lines = []
    for name, url in FEEDS.items():
        try:
            lines.append(f"✓ {name}: {len(fetch(name, url))} новостей")
        except Exception as e:
            lines.append(f"✗ {name}: {str(e)[:60]}")
    return ("✅ Радар сильных новостей подключён (до 3 в сутки).\n" + "\n".join(lines))


def main():
    first = not os.path.exists(STATE)
    state = json.load(open(STATE)) if not first else {}
    state.setdefault("seen", [])
    state.setdefault("clusters", [])
    state.setdefault("day", {"d": "", "n": 0})
    state.setdefault("last", "")
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
