from datetime import datetime, timezone
from pathlib import Path
from html import unescape
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from time import monotonic

import feedparser
import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel


logger = logging.getLogger("nabzbazar")
logging.basicConfig(level=logging.INFO)


app = FastAPI(
    title="نبض بازار API",
    description="دستیار هوشمند بازار سرمایه ایران",
    version="0.7.0",
)

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "static" / "index.html"


RSS_SOURCES = {
    "ایسنا": "https://www.isna.ir/rss",
    "مهر": "https://www.mehrnews.com/rss",
    "تابناک": "https://www.tabnak.ir/fa/rss/allnews",
}


KEYWORDS = [
    "بورس", "سهام", "شاخص کل", "فرابورس", "اقتصاد", "بانک مرکزی",
    "نرخ ارز", "دلار", "طلا", "سکه", "تورم", "نفت", "تحریم",
    "نرخ بهره", "سیاست پولی", "خودرو", "فولاد", "پتروشیمی",
    "عرضه اولیه", "بازار سرمایه", "تالار شیشه‌ای",
]


NEWS_CACHE = {"at": 0.0, "data": None}
NEWS_CACHE_TTL = 120
FETCH_TIMEOUT = 12
MAX_ITEMS = 30
SUMMARY_MAX_LEN = 500


MOJIBAKE_MARKERS = ("Ø", "Ù", "Û", "Ã", "â€")
PERSIAN_RE = re.compile(r"[\u0600-\u06FF]")
ENCODING_HINTS = ("cp1256", "windows-1256", "latin-1")


class HealthResponse(BaseModel):
    status: str
    mode: str
    timestamp: str


# ---------------------------------------------------------------------------
# اصلاح متن
# ---------------------------------------------------------------------------

def _count_markers(value: str) -> int:
    return sum(value.count(m) for m in MOJIBAKE_MARKERS)


def has_persian(text: str) -> bool:
    return bool(PERSIAN_RE.search(text or ""))


def fix_text(value):
    """اصلاح متن mojibake فارسی. idempotent و امن."""
    if not isinstance(value, str) or not value:
        return value

    if not any(m in value for m in MOJIBAKE_MARKERS):
        return value

    current = value

    for _ in range(3):
        if not any(m in current for m in MOJIBAKE_MARKERS):
            break

        candidates = []

        # تلاش ۱: cp1252 → utf-8
        try:
            raw = bytearray()
            for char in current:
                raw.extend(char.encode("cp1252"))
            candidates.append(raw.decode("utf-8"))
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass

        # تلاش ۲: latin-1 → utf-8
        try:
            candidates.append(current.encode("latin-1").decode("utf-8"))
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass

        # بهترین کاندید: بیشترین کاراکتر فارسی، کمترین نشانه خرابی
        if not candidates:
            break

        def score(s):
            return (len(PERSIAN_RE.findall(s)), -_count_markers(s))

        best = max(candidates, key=score)
        best_score = score(best)
        cur_score = score(current)

        if best_score > cur_score:
            current = best
        else:
            break

    return current


def decode_rss(response: requests.Response) -> str:
    content = response.content

    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass

    head = content[:2048].decode("ascii", errors="ignore")
    match = re.search(r'encoding=["\']([\w-]+)["\']', head)
    if match:
        try:
            return content.decode(match.group(1), errors="replace")
        except LookupError:
            pass

    for enc in ENCODING_HINTS:
        try:
            return content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue

    return content.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# پاکسازی متن — ترتیب مهم است: fix_text اول!
# ---------------------------------------------------------------------------

def strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text)


def clean_text(raw: str, max_len: int | None = None) -> str:
    if not raw:
        return ""

    text = fix_text(raw)          # ۱. اصلاح mojibake
    text = unescape(text)         # ۲. دیکد انتیتی‌ها
    text = strip_tags(text)       # ۳. حذف تگ
    text = fix_text(text)         # ۴. احتیاط: ممکن است انتیتی‌ها mojibake بوده باشند
    text = re.sub(r"\s+", " ", text).strip()

    if max_len is not None:
        text = text[:max_len]
    return text


def clean_title(raw: str) -> str:
    return clean_text(raw)


def clean_summary(raw: str) -> str:
    return clean_text(raw, max_len=SUMMARY_MAX_LEN)


def matches_keywords(*texts: str) -> bool:
    blob = " ".join(t for t in texts if t)
    return any(word in blob for word in KEYWORDS)


# ---------------------------------------------------------------------------
# دریافت منابع
# ---------------------------------------------------------------------------

def fetch_source(source: str, url: str) -> dict:
    headers = {"User-Agent": "Mozilla/5.0 NabzBazar/0.7"}

    try:
        response = requests.get(url, headers=headers, timeout=FETCH_TIMEOUT)
        response.raise_for_status()

        rss_text = decode_rss(response)
        parsed = feedparser.parse(rss_text)

        items = []
        for entry in parsed.entries:
            # اعمال fix_text بلافاصله روی همه فیلدها
            title = clean_title(entry.get("title", ""))
            if not title:
                continue

            link = entry.get("link", "").strip()
            summary = clean_summary(entry.get("summary", ""))
            published = fix_text(entry.get("published", ""))

            if not matches_keywords(title, summary):
                continue

            parsed_published = entry.get("published_parsed")

            items.append({
                "title": title,
                "link": link,
                "source": source,
                "published": published,
                "summary": summary,
                "_ts": tuple(parsed_published) if parsed_published else (0,),
            })

        return {"source": source, "items": items, "error": None}

    except Exception as exc:
        logger.warning("RSS fetch failed for %s: %r", source, exc)
        return {"source": source, "items": [], "error": repr(exc)}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/api/health", response_model=HealthResponse)
def health():
    return HealthResponse(
        status="ok",
        mode="rss_news_enabled",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/api/overview")
def overview():
    return {
        "market_status": "در انتظار اتصال به داده زنده بورس",
        "confirmed_opportunities": None,
        "data_mode": "news_rss",
        "message": "اخبار قابل دریافت است؛ قیمت زنده و تحلیل معاملاتی هنوز متصل نیست.",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _build_news_response() -> dict:
    errors = []
    all_items = []

    with ThreadPoolExecutor(max_workers=len(RSS_SOURCES)) as pool:
        futures = [
            pool.submit(fetch_source, source, url)
            for source, url in RSS_SOURCES.items()
        ]
        results = [f.result() for f in futures]

    for result in results:
        if result["error"]:
            errors.append(result["source"])
        all_items.extend(result["items"])

    unique_items = []
    seen = set()
    for item in all_items:
        key = item["title"].casefold()
        if key in seen:
            continue
        seen.add(key)
        unique_items.append(item)

    unique_items.sort(key=lambda i: i.get("_ts") or (0,), reverse=True)
    final_items = unique_items[:MAX_ITEMS]

    for item in final_items:
        item.pop("_ts", None)

    return {
        "items": final_items,
        "count": len(final_items),
        "data_mode": "rss",
        "failed_sources": errors,
        "message": (
            "اخبار از منابع RSS دریافت شده‌اند."
            if final_items
            else "خبری دریافت نشد؛ اتصال منابع RSS را بررسی کنید."
        ),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/news")
def news():
    now = monotonic()
    if NEWS_CACHE["data"] and now - NEWS_CACHE["at"] < NEWS_CACHE_TTL:
        return NEWS_CACHE["data"]

    result = _build_news_response()
    NEWS_CACHE["data"] = result
    NEWS_CACHE["at"] = now
    return result


@app.get("/", response_class=HTMLResponse)
def dashboard():
    if not INDEX.exists():
        raise HTTPException(
            status_code=503,
            detail="فایل داشبورد (static/index.html) یافت نشد.",
        )
    return INDEX.read_text(encoding="utf-8")
