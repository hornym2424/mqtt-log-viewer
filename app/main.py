from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.exporter import to_csv, to_graph_csv, to_jsonl
from app.gaps import gap_thresholds, is_gap, topic_median_intervals
from app.models import (
    FieldMapping,
    FilterQuery,
    GapSettings,
    LiveConnectRequest,
    MqttMessage,
    ParseResult,
)
from app.mqtt_live import live_session
from app.parser import assign_topic_deltas, build_parse_result, filter_messages, parse_log_bytes

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
SAMPLE_DIR = ROOT / "sample_data"
DEFAULT_LIMIT = 5000

app = FastAPI(title="MQTT Log Viewer", version="0.3.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# in-memory store for uploaded / sample file mode
_file_messages: list[MqttMessage] = []
_file_medians: dict[str, float] = {}


@dataclass
class ViewQuery:
    source: str
    filters: FilterQuery
    gap: GapSettings
    gaps_only: bool


def view_params(
    topic: str = "",
    payload: str = "",
    qos: int | None = None,
    time_from: str = "",
    time_to: str = "",
    source: str = Query(default="file", pattern="^(file|live)$"),
    gap_mode: str = Query(default="auto", pattern="^(off|auto|fixed)$"),
    gap_factor: float = Query(default=1.5, gt=1.0, le=100.0),
    gap_ms: float = Query(default=1000.0, gt=0),
    gap_topic: str = "",
    gaps_only: bool = False,
) -> ViewQuery:
    return ViewQuery(
        source=source,
        filters=FilterQuery(
            topic=topic, payload=payload, qos=qos, time_from=time_from, time_to=time_to
        ),
        gap=GapSettings(mode=gap_mode, factor=gap_factor, fixed_ms=gap_ms, topic=gap_topic),
        gaps_only=gaps_only,
    )


@dataclass
class View:
    messages: list[MqttMessage]
    medians: dict[str, float]
    thresholds: dict[str, float]


def _view(query: ViewQuery) -> View:
    if query.source == "live":
        base = live_session.snapshot()
        medians = topic_median_intervals(base)
    else:
        base, medians = _file_messages, _file_medians
    thresholds = gap_thresholds({m.topic for m in base}, medians, query.gap)
    messages = filter_messages(base, query.filters)
    if query.gaps_only:
        messages = [m for m in messages if is_gap(m, thresholds)]
    return View(messages, medians, thresholds)


def _load_file_messages(messages: list[MqttMessage]) -> ParseResult:
    global _file_messages, _file_medians
    _file_messages = assign_topic_deltas(messages)
    _file_medians = topic_median_intervals(_file_messages)
    thresholds = gap_thresholds({m.topic for m in _file_messages}, _file_medians, GapSettings())
    return build_parse_result(_file_messages, DEFAULT_LIMIT, _file_medians, thresholds)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/upload", response_model=ParseResult)
async def upload_log(
    file: UploadFile = File(...),
    ts_key: str = Form(default=""),
    topic_key: str = Form(default=""),
    qos_key: str = Form(default=""),
    payload_key: str = Form(default=""),
) -> ParseResult:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    mapping = FieldMapping(
        ts=ts_key.strip(), topic=topic_key.strip(), qos=qos_key.strip(), payload=payload_key.strip()
    )
    try:
        messages = await run_in_threadpool(
            parse_log_bytes, file.filename or "log.jsonl", content, mapping
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"parse error: {exc}") from exc
    if not messages:
        raise HTTPException(status_code=400, detail="no MQTT messages found (check field names)")
    return _load_file_messages(messages)


@app.get("/api/samples")
def list_samples() -> list[str]:
    return sorted(p.name for p in SAMPLE_DIR.iterdir() if p.is_file())


@app.get("/api/sample", response_model=ParseResult)
def load_sample(name: str = Query(default="sample.jsonl")) -> ParseResult:
    safe = Path(name).name
    path = SAMPLE_DIR / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="sample not found")
    return _load_file_messages(parse_log_bytes(safe, path.read_bytes()))


@app.get("/api/messages", response_model=ParseResult)
def get_messages(
    query: ViewQuery = Depends(view_params),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=100000),
) -> ParseResult:
    view = _view(query)
    return build_parse_result(view.messages, limit, view.medians, view.thresholds)


@app.get("/api/export")
def export_messages(
    query: ViewQuery = Depends(view_params),
    format: str = Query(default="csv", pattern="^(csv|graph|jsonl)$"),
) -> Response:
    view = _view(query)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if format == "csv":
        body = to_csv(view.messages, view.thresholds)
        media, name = "text/csv; charset=utf-8", f"mqtt_export_{stamp}.csv"
    elif format == "graph":
        body = to_graph_csv(view.messages)
        media, name = "text/csv; charset=utf-8", f"mqtt_graph_{stamp}.csv"
    else:
        body = to_jsonl(view.messages)
        media, name = "application/x-ndjson; charset=utf-8", f"mqtt_export_{stamp}.jsonl"
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.post("/api/live/connect")
def live_connect(body: LiveConnectRequest) -> dict:
    try:
        live_session.connect(body)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return live_session.status()


@app.post("/api/live/disconnect")
def live_disconnect() -> dict:
    live_session.disconnect()
    return live_session.status()


@app.post("/api/live/clear")
def live_clear() -> dict:
    live_session.clear()
    return live_session.status()


@app.get("/api/live/status")
def live_status() -> dict:
    return live_session.status()
