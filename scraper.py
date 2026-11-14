"""UPSC news scraper.

Reads the public feeds of each portal, downloads the full text of every new
story and tags it with a UPSC syllabus section.

With SUPABASE_URL and SUPABASE_SERVICE_KEY set (the hourly GitHub workflow),
stories are saved to Supabase for the hosted page. Without them, everything is
written to data/ for the local page.

    .venv\\Scripts\\python.exe scraper.py
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
import trafilatura

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
ARTICLES = DATA / "articles"
STORE = DATA / "store.json"
NEWS_JS = DATA / "news.js"
LOG = DATA / "scraper.log"

KEEP_DAYS = 7              # how long stories stay on the page
MAX_NEW_PER_SOURCE = 150   # full-article downloads per portal per run
MAX_AGE_HOURS = 48         # ignore feed items older than this when first seen
MIN_FULL_CHARS = 500       # below this the article counts as "summary only"
DELAY = 0.4                # pause between downloads from the same portal

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# (feed url, section hint, is editorial/explainer)
SOURCES = [
    {"id": "guardian", "name": "The Guardian", "region": "world", "feeds": [
        ("https://www.theguardian.com/commentisfree/rss", "", True),
        ("https://www.theguardian.com/international/rss", "", False),
        ("https://www.theguardian.com/world/rss", "ir", False),
        ("https://www.theguardian.com/politics/rss", "", False),
        ("https://www.theguardian.com/us-news/rss", "", False),
        ("https://www.theguardian.com/business/rss", "economy", False),
        ("https://www.theguardian.com/business/economics/rss", "economy", False),
        ("https://www.theguardian.com/environment/rss", "environment", False),
        ("https://www.theguardian.com/science/rss", "scitech", False),
        ("https://www.theguardian.com/technology/rss", "scitech", False),
        ("https://www.theguardian.com/global-development/rss", "social", False),
        ("https://www.theguardian.com/society/rss", "social", False),
        ("https://www.theguardian.com/education/rss", "social", False),
        ("https://www.theguardian.com/law/rss", "", False),
    ]},
    {"id": "dw", "name": "DW", "region": "world", "feeds": [
        ("https://rss.dw.com/rdf/rss-en-top", "", False),
        ("https://rss.dw.com/rdf/rss-en-all", "", False),
        ("https://rss.dw.com/rdf/rss-en-world", "ir", False),
        ("https://rss.dw.com/rdf/rss-en-bus", "economy", False),
    ]},
    {"id": "npr", "name": "NPR", "region": "world", "feeds": [
        ("https://feeds.npr.org/1001/rss.xml", "", False),
        ("https://feeds.npr.org/1003/rss.xml", "", False),
        ("https://feeds.npr.org/1004/rss.xml", "ir", False),
        ("https://feeds.npr.org/1014/rss.xml", "", False),
        ("https://feeds.npr.org/1006/rss.xml", "economy", False),
        ("https://feeds.npr.org/1017/rss.xml", "economy", False),
        ("https://feeds.npr.org/1007/rss.xml", "scitech", False),
        ("https://feeds.npr.org/1019/rss.xml", "scitech", False),
        ("https://feeds.npr.org/1025/rss.xml", "environment", False),
        ("https://feeds.npr.org/1128/rss.xml", "social", False),
        ("https://feeds.npr.org/1013/rss.xml", "social", False),
    ]},
    {"id": "cbs", "name": "CBS News", "region": "world", "feeds": [
        ("https://www.cbsnews.com/latest/rss/main", "", False),
        ("https://www.cbsnews.com/latest/rss/world", "ir", False),
        ("https://www.cbsnews.com/latest/rss/us", "", False),
        ("https://www.cbsnews.com/latest/rss/politics", "", False),
        ("https://www.cbsnews.com/latest/rss/moneywatch", "economy", False),
        ("https://www.cbsnews.com/latest/rss/science", "scitech", False),
        ("https://www.cbsnews.com/latest/rss/technology", "scitech", False),
        ("https://www.cbsnews.com/latest/rss/health", "social", False),
    ]},
    {"id": "abc", "name": "ABC News (Australia)", "region": "world", "feeds": [
        ("https://www.abc.net.au/news/feed/51120/rss.xml", "", False),
        ("https://www.abc.net.au/news/feed/45910/rss.xml", "", False),
        ("https://www.abc.net.au/news/feed/51892/rss.xml", "economy", False),
        ("https://www.abc.net.au/news/feed/2942460/rss.xml", "", False),
    ]},
    {"id": "hindu", "name": "The Hindu", "region": "india", "feeds": [
        ("https://www.thehindu.com/opinion/editorial/feeder/default.rss", "", True),
        ("https://www.thehindu.com/opinion/lead/feeder/default.rss", "", True),
        ("https://www.thehindu.com/opinion/op-ed/feeder/default.rss", "", True),
        ("https://www.thehindu.com/news/feeder/default.rss", "", False),
        ("https://www.thehindu.com/news/national/feeder/default.rss", "", False),
        ("https://www.thehindu.com/news/international/feeder/default.rss", "ir", False),
        ("https://www.thehindu.com/news/states/feeder/default.rss", "", False),
        ("https://www.thehindu.com/news/cities/feeder/default.rss", "", False),
        ("https://www.thehindu.com/business/feeder/default.rss", "economy", False),
        ("https://www.thehindu.com/business/Economy/feeder/default.rss", "economy", False),
        ("https://www.thehindu.com/business/Industry/feeder/default.rss", "economy", False),
        ("https://www.thehindu.com/business/agri-business/feeder/default.rss", "economy", False),
        ("https://www.thehindu.com/sci-tech/feeder/default.rss", "scitech", False),
        ("https://www.thehindu.com/sci-tech/science/feeder/default.rss", "scitech", False),
        ("https://www.thehindu.com/sci-tech/technology/feeder/default.rss", "scitech", False),
        ("https://www.thehindu.com/sci-tech/health/feeder/default.rss", "social", False),
        ("https://www.thehindu.com/sci-tech/energy-and-environment/feeder/default.rss", "environment", False),
        ("https://www.thehindu.com/sci-tech/agriculture/feeder/default.rss", "economy", False),
        ("https://www.thehindu.com/education/feeder/default.rss", "social", False),
        ("https://www.thehindu.com/data/feeder/default.rss", "", False),
    ]},
    {"id": "ie", "name": "The Indian Express", "region": "india", "feeds": [
        ("https://indianexpress.com/section/explained/feed/", "", True),
        ("https://indianexpress.com/section/opinion/editorials/feed/", "", True),
        ("https://indianexpress.com/section/opinion/columns/feed/", "", True),
        ("https://indianexpress.com/section/upsc-current-affairs/feed/", "", True),
        ("https://indianexpress.com/feed/", "", False),
        ("https://indianexpress.com/section/india/feed/", "", False),
        ("https://indianexpress.com/section/political-pulse/feed/", "polity", False),
        ("https://indianexpress.com/section/world/feed/", "ir", False),
        ("https://indianexpress.com/section/business/feed/", "economy", False),
        ("https://indianexpress.com/section/business/economy/feed/", "economy", False),
        ("https://indianexpress.com/section/business/banking-and-finance/feed/", "economy", False),
        ("https://indianexpress.com/section/technology/feed/", "scitech", False),
        ("https://indianexpress.com/section/technology/science/feed/", "scitech", False),
        ("https://indianexpress.com/section/education/feed/", "social", False),
        ("https://indianexpress.com/section/cities/feed/", "", False),
    ]},
    {"id": "toi", "name": "The Times of India", "region": "india", "feeds": [
        ("https://timesofindia.indiatimes.com/rssfeedstopstories.cms", "", False),
        ("https://timesofindia.indiatimes.com/rssfeeds/-2128936835.cms", "", False),
        ("https://timesofindia.indiatimes.com/rssfeeds/296589292.cms", "ir", False),
        ("https://timesofindia.indiatimes.com/rssfeeds/30359486.cms", "ir", False),
        ("https://timesofindia.indiatimes.com/rssfeeds/3907412.cms", "ir", False),
        ("https://timesofindia.indiatimes.com/rssfeeds/1898055.cms", "economy", False),
        ("https://timesofindia.indiatimes.com/rssfeeds/2647163.cms", "environment", False),
        ("https://timesofindia.indiatimes.com/rssfeeds/-2128672765.cms", "scitech", False),
        ("https://timesofindia.indiatimes.com/rssfeeds/66949542.cms", "scitech", False),
        ("https://timesofindia.indiatimes.com/rssfeeds/913168846.cms", "social", False),
        ("https://timesofindia.indiatimes.com/rssfeeds/-2128932452.cms", "", False),
    ]},
    {"id": "pib", "name": "PIB", "region": "india", "feeds": [
    ]},  # handled by pib_items()
]

CATEGORIES = {
    "polity": "Polity & Governance",
    "economy": "Economy",
    "ir": "International Relations",
    "environment": "Environment",
    "scitech": "Science & Tech",
    "social": "Social Issues",
    "security": "Security",
    "general": "Other News",
}

KEYWORDS = {
    "polity": """parliament|lok sabha|rajya sabha|supreme court|high court|constitution\\w*|bill|ordinance|
        election commission|governor|cabinet|judiciary|verdict|amendment|scheme|panchayat|federal\\w*|
        reservation|niti aayog|cag|lokpal|rti|governance|legislat\\w+|chief justice|cji|collegium|
        assembly|speaker|president'?s rule|delimitation|uniform civil code|tribunal|article \\d+|
        fundamental rights?|electoral|by-?elections?|cm|chief minister|centre|union government|
        civil servants?|bureaucra\\w+|municipal\\w*|polls?""",
    "economy": """gdp|inflation|rbi|repo rate|fiscal|budget|tax\\w*|gst|trade|exports?|imports?|tariffs?|
        banks?|banking|markets?|sebi|rupee|econom\\w+|investment|fdi|msmes?|unemployment|cpi|wpi|imf|
        world bank|industr\\w+|manufacturing|agricultur\\w+|farmers?|msp|crops?|subsid\\w+|disinvestment|
        startups?|infrastructure|logistics|current account|forex|monetary|sensex|nifty|ipo|npa|
        insolvency|pli|wto|supply chain|recession|interest rates?|central bank|kharif|rabi""",
    "ir": """bilateral|summit|diplomat\\w*|foreign minister|external affairs|united nations|security council|
        g20|g7|brics|quad|sco|nato|treaty|sanctions?|ceasefire|embassy|china|chinese|pakistan|russia\\w*|
        ukrain\\w+|israel\\w*|gaza|iran\\w*|white house|foreign policy|asean|european union|peace talks|
        geopolit\\w+|indo-pacific|sri lanka|nepal|bangladesh|bhutan|maldives|myanmar|afghanistan|taliban|
        saarc|bimstec|envoy|ambassador|state visit|strategic partnership|free trade agreement|fta|
        west asia|middle east|taiwan|south china sea|refugees?""",
    "environment": """climate|cop\\d+|emissions?|carbon|pollution|air quality|aqi|wildlife|tigers?|forests?|
        biodiversity|cyclone|floods?|drought|heatwaves?|glaciers?|renewable\\w*|solar|conservation|
        species|wetlands?|ramsar|monsoon|earthquake|disaster|landslides?|deforestation|ecolog\\w+|
        ecosystem|national park|sanctuary|endangered|net zero|green hydrogen|coral|mangroves?|
        plastic|waste|groundwater|el ni[nñ]o|la ni[nñ]a|ozone|fossil fuels?|wind energy|elephants?""",
    "scitech": """isro|nasa|satellites?|space\\w*|artificial intelligence|ai|quantum|semiconductors?|
        vaccines?|genom\\w+|scientists?|researchers?|nuclear|technolog\\w+|5g|6g|telescope|
        biotech\\w*|gaganyaan|chandrayaan|aditya-l1|rockets?|crispr|supercomput\\w+|chips?|robot\\w*|
        machine learning|physics|astronom\\w+|nobel prize|clinical trial|antibiotic\\w*|fusion|
        lunar|mars|orbit\\w*|spacex|data centres?|deepfakes?""",
    "social": """health|education|poverty|women|child\\w*|tribal\\w*|caste|malnutrition|nep|schools?|
        universit\\w+|hospitals?|diseases?|welfare|gender|population|census|migra\\w+|hunger|sanitation|
        minorit\\w+|tuberculosis|maternal|anganwadi|nutrition|ayushman|pension|disabilit\\w+|elderly|
        dalits?|adivasi|scheduled (?:castes?|tribes?)|literacy|dropout|human rights|inequality|
        mental health|public health|who|unicef|labour|workers""",
    "security": """army|navy|air force|iaf|defence|defense|missiles?|drdo|terror\\w*|militants?|naxal\\w*|
        maoists?|border|lac|loc|insurgen\\w+|coast guard|cyber\\w*|bsf|crpf|nia|drones?|military|
        warships?|fighter jets?|submarines?|ceasefire violation|infiltrat\\w+|afspa|intelligence|
        espionage|smuggling|money laundering|radicali[sz]ation|joint exercise|tejas|brahmos|agni""",
}
KW = {k: re.compile(r"\b(?:" + re.sub(r"\s+", "", v) + r")\b", re.I) for k, v in KEYWORDS.items()}

SKIP_TITLE = re.compile(
    r"\b(cricket|ipl|t20|odi|football|soccer|tennis|badminton|hockey|olympics?|world cup|premier league|"
    r"bollywood|hollywood|actors?|actress|films?|movies?|box office|celebrit\w+|horoscope|recipes?|"
    r"fashion|grammys?|oscars?|netflix|web series|trailer|album|singer|gossip|quiz|crossword|wordle|"
    r"zodiac|astrolog\w+|lottery|sudoku|photos|in pictures|watch:|podcast|live updates)\b", re.I)
SKIP_URL = re.compile(
    r"/(sports?|entertainment|lifestyle|life-style|football|film|music|tv-and-radio|video|videos|live|"
    r"live-news|photos?|web-stories|podcasts?|cricket|games|fashion|food|travel|astrology|"
    r"lifeandstyle|culture|books|stage|crosswords|trending|viral)/", re.I)

# lines that are page furniture rather than article text
JUNK_LINE = re.compile(
    r"^(advertisement|related content|related|also read\b.*|read more\b.*|read also\b.*|published\s*-.*|"
    r"subscribe\b.*|sign up\b.*|follow us\b.*|click here\b.*|share this.*|copy link|comments?|"
    r"story continues below.*|this is a premium article.*|to read \d+ free.*|"
    r"\(?with (pti|ani|agency) inputs\)?|explore more.*|topics|newsletter.*|"
    r"stay updated with.*|get the latest.*|download the .* app.*|more from .*|trending|"
    r"photo credit:.*|file photo.*|\*+)$", re.I)

log = logging.getLogger("scraper")


# ---------------------------------------------------------------- helpers
def fetch(url: str, timeout: int = 25) -> requests.Response | None:
    for attempt in range(2):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r
            log.warning("HTTP %s for %s", r.status_code, url)
            return None
        except requests.RequestException as e:
            if attempt:
                log.warning("failed %s: %s", url, e)
            time.sleep(1.5)
    return None


def strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_date(s: str) -> float | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def make_id(url: str) -> str:
    url = url.split("#")[0]
    if "PRID=" not in url:  # PIB keeps the story number in the query string
        url = url.split("?")[0]
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def parse_feed(xml_text: str) -> list[dict]:
    """Return items from an RSS 2.0 / RDF feed."""
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError as e:
        log.warning("feed parse error: %s", e)
        return []
    out = []
    for el in root.iter():
        if local(el.tag) != "item":
            continue
        it = {"title": "", "link": "", "summary": "", "ts": None, "image": ""}
        best_w = -1
        for c in el.iter():
            if c is el:
                continue
            t = local(c.tag)
            text = (c.text or "").strip()
            if t == "title" and not it["title"] and c in list(el):
                it["title"] = strip_tags(text)
            elif t == "link" and not it["link"] and text:
                it["link"] = text
            elif t == "description" and not it["summary"] and c in list(el):
                it["summary"] = strip_tags(text)[:400]
            elif t in ("pubDate", "date") and it["ts"] is None:
                it["ts"] = parse_date(text)
            elif t in ("content", "thumbnail", "enclosure", "image"):
                url = c.attrib.get("url") or (text if text.startswith("http") else "")
                kind = c.attrib.get("type", "") + c.attrib.get("medium", "")
                if url and ("video" not in kind) and ("audio" not in kind):
                    try:
                        w = int(c.attrib.get("width", "0") or 0)
                    except ValueError:
                        w = 0
                    if w > best_w:
                        best_w, it["image"] = w, url
        if it["title"] and it["link"].startswith("http"):
            out.append(it)
    return out


def pib_items() -> list[dict]:
    """PIB has no working feed, so read its English 'all releases' page."""
    r = fetch("https://www.pib.gov.in/AllRelease.aspx?reg=3&lang=1", timeout=40)
    if not r:
        return []
    out = []
    pat = re.compile(r"<a[^>]+PRID=(\d+)[^>]*>(.*?)</a>\s*<span class='publishdatesmall'>Posted on:\s*([^<]+)", re.S)
    for pid, title, posted in pat.findall(r.text):
        try:
            day = datetime.strptime(posted.strip(), "%d %b %Y").replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
        except ValueError:
            continue
        title = strip_tags(title)
        if title.isupper():
            small = {"of", "in", "on", "for", "to", "and", "or", "the", "a", "an", "at", "by", "with", "from"}
            words = title.lower().split()
            title = " ".join(w if (n and w in small) else w[:1].upper() + w[1:] for n, w in enumerate(words))
        out.append({
            "title": title,
            "link": f"https://www.pib.gov.in/PressReleasePage.aspx?PRID={pid}",
            "summary": "", "image": "",
            # the listing only gives a day; noon keeps it sorted sensibly
            "ts": min(day.timestamp() + 12 * 3600, time.time()),
        })
    return out


OG_IMAGE = re.compile(r"""<meta[^>]+(?:property|name)=["'](?:og:image|twitter:image)["'][^>]+content=["']([^"']+)""", re.I)
OG_IMAGE2 = re.compile(r"""<meta[^>]+content=["']([^"']+)["'][^>]+(?:property|name)=["']og:image["']""", re.I)
OG_DESC = re.compile(r"""<meta[^>]+(?:property|name)=["'](?:og:description|description)["'][^>]+content=["']([^"']+)""", re.I)


def extract_article(page_html: str) -> list[str]:
    text = trafilatura.extract(page_html, include_comments=False, include_tables=False,
                               favor_recall=True) or ""
    paras = []
    for line in text.replace("\x00", "").split("\n"):  # Postgres rejects NUL characters
        line = line.strip()
        if not line or JUNK_LINE.match(line):
            continue
        if paras and line == paras[-1]:
            continue
        paras.append(line)
    return paras


def categorise(title: str, summary: str, body: str, hint: str, region: str, editorial: bool):
    """Return a section key ("general" when nothing fits), or None for sport/entertainment."""
    scores = {}
    head = f"{title} {summary}"
    for key, rx in KW.items():
        scores[key] = 3 * len(set(m.lower() for m in rx.findall(title))) \
            + 2 * len(set(m.lower() for m in rx.findall(summary))) \
            + min(4, len(set(m.lower() for m in rx.findall(body[:2500]))))
    if hint:
        scores[hint] = scores.get(hint, 0) + 3
    best = max(scores, key=scores.get)
    if SKIP_TITLE.search(head) and scores[best] < 8:
        return None   # sport and entertainment are the only things left out
    if scores[best] < 3:
        return "general"
    return best


# ---------------------------------------------------------------- per-portal work
def process_source(src: dict, store: dict) -> list[dict]:
    now = time.time()
    candidates: dict[str, dict] = {}
    if src["id"] == "pib":
        for it in pib_items():
            it["hint"], it["editorial"] = "", False
            candidates.setdefault(make_id(it["link"]), it)
    for url, hint, editorial in src["feeds"]:
        r = fetch(url)
        if not r:
            continue
        for it in parse_feed(r.content):
            it["hint"], it["editorial"] = hint, editorial
            candidates.setdefault(make_id(it["link"]), it)

    fresh = []
    for aid, it in candidates.items():
        if aid in store:
            continue
        if SKIP_URL.search(it["link"]):
            continue
        ts = it["ts"] or now
        if ts > now:
            ts = now
        if now - ts > MAX_AGE_HOURS * 3600:
            continue
        it["ts"], it["id"] = ts, aid
        fresh.append(it)
    # editorials/explainers first, then newest
    fresh.sort(key=lambda x: (not x["editorial"], -x["ts"]))
    fresh = fresh[:MAX_NEW_PER_SOURCE]

    done = []
    for it in fresh:
        r = fetch(it["link"])
        time.sleep(DELAY)
        if not r:
            continue  # not stored, so the next hourly run tries again
        paras, image, summary = [], it["image"], it["summary"]
        if r:
            page = r.text
            paras = extract_article(page)
            if not image:
                m = OG_IMAGE.search(page) or OG_IMAGE2.search(page)
                if m:
                    image = html.unescape(m.group(1))
            if not summary:
                m = OG_DESC.search(page)
                summary = strip_tags(m.group(1))[:400] if m else ""
        body = " ".join(paras)
        if not summary and paras:
            summary = body[:280].rsplit(" ", 1)[0] + "…"
        cat = categorise(it["title"], summary, body, it["hint"], src["region"], it["editorial"])
        full = len(body) >= MIN_FULL_CHARS
        meta = {
            "id": it["id"], "title": it["title"], "url": it["link"], "summary": summary,
            "image": image if image.startswith("http") else "",
            "ts": int(it["ts"]), "source": src["id"], "region": src["region"],
            "cat": cat, "editorial": bool(it["editorial"]), "full": full,
            "words": len(body.split()), "seen": int(now),
        }
        meta["_paras"] = paras if cat else []
        done.append(meta)
    log.info("%-8s feed items=%d new=%d kept=%d full=%d", src["id"], len(candidates), len(fresh),
             sum(1 for d in done if d["cat"]), sum(1 for d in done if d["cat"] and d["full"]))
    return done


def js_dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=True, separators=(",", ":")).replace("</", "<\\/")


def write_article(meta: dict, paras: list[str]) -> None:
    payload = {"id": meta["id"], "paras": paras}
    (ARTICLES / f"{meta['id']}.js").write_text(
        f"window.__article&&window.__article({js_dump(payload)});", encoding="utf-8")


# ---------------------------------------------------------------- ranking
STOP = set("the a an of in on for to and or with at by from as is are was were be been has have had will "
           "would after over into amid says say said new its his her their this that not but more than "
           "about what why how who when where india indian us".split())


def title_words(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", t.lower()) if w not in STOP}


def add_buzz(items: list[dict]) -> None:
    """buzz = how many other portals carry a similar headline (a stand-in for 'trending')."""
    recent = [i for i in items if time.time() - i["ts"] < 36 * 3600]
    words = {i["id"]: title_words(i["title"]) for i in recent}
    for i in items:
        i["buzz"] = 0
    for a in recent:
        wa = words[a["id"]]
        if len(wa) < 3:
            continue
        others = set()
        for b in recent:
            if b["source"] == a["source"]:
                continue
            wb = words[b["id"]]
            if len(wb) >= 3 and len(wa & wb) / min(len(wa), len(wb)) >= 0.5:
                others.add(b["source"])
        a["buzz"] = len(others)


# ---------------------------------------------------------------- main
def main() -> int:
    DATA.mkdir(exist_ok=True)
    ARTICLES.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, encoding="utf-8"), logging.StreamHandler(sys.stdout)])
    started = time.time()
    log.info("run started")

    db = Supabase.from_env()
    if db:
        return run_supabase(db, started)

    store: dict = {}
    if STORE.exists():
        try:
            store = json.loads(STORE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("store.json unreadable, starting fresh")

    with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        results = list(pool.map(lambda s: safe_process(s, store), SOURCES))
    for metas in results:
        for m in metas:
            paras = m.pop("_paras")
            if m["cat"]:
                write_article(m, paras)
            store[m["id"]] = m

    # drop old stories and their article files
    cutoff = time.time() - KEEP_DAYS * 86400
    for aid in [k for k, v in store.items() if v["ts"] < cutoff and v["seen"] < cutoff]:
        (ARTICLES / f"{aid}.js").unlink(missing_ok=True)
        del store[aid]

    visible = sorted((v for v in store.values() if v["cat"]), key=lambda v: -v["ts"])
    add_buzz(visible)
    out = {
        "generated": int(time.time()),
        "categories": CATEGORIES,
        "sources": [{"id": s["id"], "name": s["name"], "region": s["region"]} for s in SOURCES],
        "items": [{k: v[k] for k in ("id", "title", "url", "summary", "image", "ts", "source", "region",
                                     "cat", "editorial", "full", "words", "buzz")} for v in visible],
    }
    tmp = NEWS_JS.with_suffix(".tmp")
    tmp.write_text(f"window.NEWS_DATA={js_dump(out)};", encoding="utf-8")
    tmp.replace(NEWS_JS)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=True), encoding="utf-8")
    tmp.replace(STORE)

    # keep the log from growing forever
    lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    log.info("run finished in %.0fs: %d stories on the page (%d with full text)",
             time.time() - started, len(visible), sum(1 for v in visible if v["full"]))
    if len(lines) > 3000:
        logging.shutdown()
        LOG.write_text("\n".join(lines[-1500:]) + "\n", encoding="utf-8")
    return 0


# ---------------------------------------------------------------- Supabase
class Supabase:
    """Minimal client for Supabase's REST API, using the project's secret key."""

    def __init__(self, url: str, key: str):
        self.base = url.rstrip("/") + "/rest/v1/"
        self.headers = {"apikey": key, "Content-Type": "application/json"}
        if key.startswith("eyJ"):  # older service_role keys are JWTs and also go here
            self.headers["Authorization"] = "Bearer " + key

    @classmethod
    def from_env(cls) -> "Supabase | None":
        url = os.environ.get("SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
        return cls(url, key) if url and key else None

    def _req(self, method: str, path: str, prefer: str = "", **kw) -> requests.Response:
        headers = dict(self.headers)
        if prefer:
            headers["Prefer"] = prefer
        for attempt in range(3):
            try:
                r = requests.request(method, self.base + path, headers=headers, timeout=90, **kw)
            except requests.RequestException as e:
                if attempt == 2:
                    raise
                log.warning("Supabase retry after %s", e)
                time.sleep(3)
                continue
            if r.status_code < 300:
                return r
            if r.status_code < 500 or attempt == 2:
                raise RuntimeError(f"Supabase {method} {path.split('?')[0]} -> {r.status_code}: {r.text[:300]}")
            time.sleep(3)
        raise RuntimeError("unreachable")

    def known(self) -> dict:
        """Ids of every story already saved (so they are not downloaded again)."""
        out, offset = {}, 0
        while True:
            rows = self._req("GET", f"dailybrief_stories?select=id&order=id&limit=1000&offset={offset}").json()
            for row in rows:
                out[row["id"]] = True
            if len(rows) < 1000:
                return out
            offset += 1000

    def save(self, metas: list[dict]) -> None:
        rows = [{
            "id": m["id"], "title": m["title"], "url": m["url"], "summary": m["summary"],
            "image": m["image"], "ts": m["ts"], "seen": m["seen"], "source": m["source"],
            "region": m["region"], "cat": m["cat"], "editorial": m["editorial"],
            "has_full": m["full"], "words": m["words"], "paras": m["_paras"],
        } for m in metas]
        for i in range(0, len(rows), 25):
            self._req("POST", "dailybrief_stories?on_conflict=id", json=rows[i:i + 25],
                      prefer="resolution=merge-duplicates,return=minimal")

    def delete_older(self, cutoff: float) -> None:
        c = int(cutoff)
        self._req("DELETE", f"dailybrief_stories?ts=lt.{c}&seen=lt.{c}", prefer="return=minimal")

    def set_run(self, **fields) -> None:
        self._req("POST", "dailybrief_runs?on_conflict=id", json=[{"id": 1, **fields}],
                  prefer="resolution=merge-duplicates,return=minimal")


def run_supabase(db: Supabase, started: float) -> int:
    meta = {"categories": CATEGORIES,
            "sources": [{"id": s["id"], "name": s["name"], "region": s["region"]} for s in SOURCES]}
    db.set_run(running=True, started=int(started), meta=meta)
    try:
        store = db.known()
        log.info("Supabase has %d stories already", len(store))
        with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
            results = list(pool.map(lambda s: safe_process(s, store), SOURCES))
        new = [m for metas in results for m in metas]
        db.save(new)
        db.delete_older(time.time() - KEEP_DAYS * 86400)
    finally:
        db.set_run(running=False, generated=int(time.time()))
    log.info("run finished in %.0fs: %d new stories saved to Supabase (%d with full text)",
             time.time() - started, sum(1 for m in new if m["cat"]),
             sum(1 for m in new if m["cat"] and m["full"]))
    return 0


def safe_process(src: dict, store: dict) -> list[dict]:
    try:
        return process_source(src, store)
    except Exception:  # one broken portal must not stop the others
        log.exception("source %s failed", src["id"])
        return []


LOCK = DATA / "scrape.lock"


def run_once() -> int:
    """Run main() unless another run (hourly task or Refresh button) is already going."""
    DATA.mkdir(exist_ok=True)
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 20 * 60:
        print("another run is in progress; skipping")
        return 0
    LOCK.write_text(str(int(time.time())), encoding="utf-8")
    try:
        return main()
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(run_once())
