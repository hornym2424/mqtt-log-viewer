from __future__ import annotations

import csv
import io
import json
from typing import Any

from app.gaps import is_gap
from app.models import MqttMessage

EXPORT_COLUMNS = ("ts", "topic", "qos", "dt_ms", "gap", "payload")


def _csv_bytes(rows: list[list[Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerows(rows)
    # BOM so Excel opens UTF-8 (Japanese text) correctly.
    return buffer.getvalue().encode("utf-8-sig")


def to_csv(messages: list[MqttMessage], thresholds: dict[str, float] | None = None) -> bytes:
    thresholds = thresholds or {}
    rows: list[list[Any]] = [list(EXPORT_COLUMNS)]
    for msg in messages:
        rows.append(
            [
                msg.ts,
                msg.topic,
                msg.qos,
                "" if msg.dt_ms is None else msg.dt_ms,
                1 if is_gap(msg, thresholds) else 0,
                msg.payload_text,
            ]
        )
    return _csv_bytes(rows)


def flatten_payload(payload: Any, prefix: str = "") -> dict[str, Any]:
    """{"pos": {"lat": 1}} -> {"pos.lat": 1}. Lists stay as JSON text; non-JSON goes to "payload"."""
    if not isinstance(payload, dict):
        if isinstance(payload, list):
            return {prefix or "payload": json.dumps(payload, ensure_ascii=False)}
        return {prefix or "payload": payload}
    flat: dict[str, Any] = {}
    for key, value in payload.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flat.update(flatten_payload(value, name))
        elif isinstance(value, list):
            flat[name] = json.dumps(value, ensure_ascii=False)
        else:
            flat[name] = value
    return flat


def to_graph_csv(messages: list[MqttMessage]) -> bytes:
    """One column per payload field, plus elapsed seconds, for plotting in Excel etc."""
    flattened = [flatten_payload(msg.payload) for msg in messages]
    fields: list[str] = []
    seen: set[str] = set()
    for flat in flattened:
        for key in flat:
            if key not in seen:
                seen.add(key)
                fields.append(key)

    start = next((m.ts_epoch for m in messages if m.ts_epoch is not None), None)
    rows: list[list[Any]] = [["ts", "elapsed_s", "topic", *fields]]
    for msg, flat in zip(messages, flattened):
        elapsed = "" if start is None or msg.ts_epoch is None else round(msg.ts_epoch - start, 3)
        values = ["" if flat.get(f) is None else _cell(flat[f]) for f in fields]
        rows.append([msg.ts, elapsed, msg.topic, *values])
    return _csv_bytes(rows)


def _cell(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    return value


def to_jsonl(messages: list[MqttMessage]) -> bytes:
    lines = [
        json.dumps(
            {"ts": msg.ts, "topic": msg.topic, "qos": msg.qos, "payload": msg.payload},
            ensure_ascii=False,
        )
        for msg in messages
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")
