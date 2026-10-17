from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI(
    title="نبض بازار API",
    description="اسکلت اولیه دستیار هوشمند بازار سرمایه ایران",
    version="0.1.0",
)

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "static" / "index.html"


class HealthResponse(BaseModel):
    status: str
    mode: str
    timestamp: str


@app.get("/api/health", response_model=HealthResponse)
def health():
    return HealthResponse(
        status="ok",
        mode="prototype_mock_data",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/api/overview")
def overview():
    # Deliberately no live market data in this prototype.
    return {
        "market_status": "در انتظار اتصال به منبع داده",
        "confirmed_opportunities": None,
        "data_mode": "mock",
        "message": "هنوز داده زنده یا تحلیل معاملاتی اجرا نشده است.",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/news")
def news():
    return {
        "items": [],
        "message": "هنوز منبع خبری متصل نشده است؛ این فهرست فعلاً خالی است.",
    }


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return INDEX.read_text(encoding="utf-8")
