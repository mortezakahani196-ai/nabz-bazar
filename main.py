from datetime import datetime, timezone
from pathlib import Path
from html import unescape
import re

import feedparser
import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI(
    title="نبض بازار API",
    description="دستیار هوشمند بازار سرمایه ایران",
    version="0.2.0",
)

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "static" / "index.html"

RSS_SOURCES = {
    "ایسنا": "https://www.isna.ir/rss",
    "مهر": "https://www.mehrnews.com/rss",
    "تابناک": "https://www.tabnak.ir/fa/rss/allnews",
}

KEYWORDS = [
    "بورس", "سهام", "شاخص کل", "فرابورس",
    "اقتصاد", "بانک مرکزی", "نرخ ارز", "دلار",
    "طلا", "سکه", "تورم", "نفت", "تحریم",
    "نرخ بهره", "سیاست پولی", "خودرو",
    "فولاد", "پتروشیمی", "عرضه اولیه",
    "بازار سرمایه", "تالار شیشه‌ای",
]


class HealthResponse(BaseModel):
    status: str
    mode: str
    timestamp: str


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
        "message": "اخبار قابل دریافت است؛ داده زنده معاملات هنوز متصل نیست.",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/news")
def news():
    items = []
    errors = []

    headers = {
        "User-Agent": "NabzBazar/0.2"
    }

    for source, url in RSS_SOURCES.items():
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=7,
            )
            response.raise_for_status()

            parsed = feedparser.parse(response.content)

            for entry in parsed.entries:
                title = unescape(entry.get("title", "")).strip()
                link = entry.get("link", "")
                summary = re.sub(
                    r"<[^>]+>", " ",
                    entry.get("summary", "")
                )
                summary = unescape(summary).strip()
                summary = re.sub(r"\s+", " ", summary)[:500]

                searchable = f"{title} {summary}"

                if not title or not any(
                    word in searchable for word in KEYWORDS
                ):
                    continue

                items.append({
                    "title": title,
                    "link": link,
                    "source": source,
                    "published": entry.get("published", ""),
                    "summary": summary,
                })

        except Exception:
            errors.append(source)

    # حذف خبرهای تکراری بر اساس عنوان
    unique_items = []
    seen = set()

    for item in items:
        key = item["title"]
        if key not in seen:
            seen.add(key)
            unique_items.append(item)

    return {
        "items": unique_items[:30],
        "count": len(unique_items[:30]),
        "data_mode": "rss",
        "failed_sources": errors,
        "message": (
            "اخبار از منابع RSS دریافت شده‌اند."
            if unique_items
            else "خبری دریافت نشد؛ اتصال منابع RSS را بررسی کنید."
        ),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return INDEX.read_text(encoding="utf-8")
