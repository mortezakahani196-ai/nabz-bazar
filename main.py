
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
    version="0.4.0",
)

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "static" / "index.html"


RSS_SOURCES = {
    "ایسنا": "https://www.isna.ir/rss",
    "مهر": "https://www.mehrnews.com/rss",
    "تابناک": "https://www.tabnak.ir/fa/rss/allnews",
}


KEYWORDS = [
    "بورس",
    "سهام",
    "شاخص کل",
    "فرابورس",
    "اقتصاد",
    "بانک مرکزی",
    "نرخ ارز",
    "دلار",
    "طلا",
    "سکه",
    "تورم",
    "نفت",
    "تحریم",
    "نرخ بهره",
    "سیاست پولی",
    "خودرو",
    "فولاد",
    "پتروشیمی",
    "عرضه اولیه",
    "بازار سرمایه",
    "تالار شیشه‌ای",
]


class HealthResponse(BaseModel):
    status: str
    mode: str
    timestamp: str


def fix_text(value):
    """اصلاح احتمالی متن فارسی که با کدگذاری اشتباه نمایش داده شده است."""
    if not isinstance(value, str):
        return value

    for _ in range(2):
        if not any(
            marker in value
            for marker in ("Ø", "Ù", "Û", "Ã", "â€")
        ):
            break

        raw = bytearray()

        try:
            for char in value:
                try:
                    raw.extend(char.encode("cp1252"))
                except UnicodeEncodeError:
                    raw.extend(char.encode("latin-1"))

            repaired = raw.decode("utf-8")

            if repaired == value:
                break

            value = repaired

        except (UnicodeEncodeError, UnicodeDecodeError):
            break

    return value


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
        "message": (
            "اخبار قابل دریافت است؛ قیمت زنده و تحلیل معاملاتی "
            "هنوز متصل نیست."
        ),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/news")
def news():
    items = []
    errors = []

    headers = {
        "User-Agent": "Mozilla/5.0 NabzBazar/0.4"
    }

    for source, url in RSS_SOURCES.items():
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=12,
            )
            response.raise_for_status()

            # تلاش برای خواندن محتوای RSS با کدگذاری صحیح
            try:
                rss_text = response.content.decode("utf-8-sig")
            except UnicodeDecodeError:
                rss_text = response.content.decode(
                    response.encoding or "utf-8",
                    errors="replace",
                )

            parsed = feedparser.parse(rss_text)

            for entry in parsed.entries:
                title = fix_text(
                    unescape(entry.get("title", "")).strip()
                )

                link = entry.get("link", "").strip()

                summary = entry.get("summary", "")
                summary = re.sub(r"<[^>]+>", " ", summary)
                summary = fix_text(
                    unescape(summary).strip()
                )
                summary = re.sub(r"\s+", " ", summary)[:500]

                published = fix_text(
                    entry.get("published", "")
                )

                searchable = f"{title} {summary}"

                if not title:
                    continue

                if not any(
                    word in searchable
                    for word in KEYWORDS
                ):
                    continue

                items.append({
                    "title": title,
                    "link": link,
                    "source": source,
                    "published": published,
                    "summary": summary,
                })

        except Exception:
            errors.append(source)

    # حذف خبرهای تکراری بر اساس عنوان
    unique_items = []
    seen = set()

    for item in items:
        key = item["title"].strip().casefold()

        if key not in seen:
            seen.add(key)
            unique_items.append(item)

    # مرتب‌سازی بر اساس تاریخ انتشار
    unique_items.sort(
        key=lambda item: item.get("published", ""),
        reverse=True,
    )

    final_items = unique_items[:30]

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


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return INDEX.read_text(encoding="utf-8")
