from __future__ import annotations

import csv
import io
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.models import FieldMapping, FilterQuery, MqttMessage, ParseResult, TopicStat

TS_KEYS = ("ts", "timestamp", "time", "datetime", "date", "received_at")
TOPIC_KEYS = ("topic", "Topic", "TOPIC")
QOS_KEYS = ("qos", "QoS", "QOS")
PAYLOAD_KEYS = ("payload", "message", "msg", "data", "body")

LOCAL_TZ = timezone(timedelta(hours=9), "JST")

# Leading "2026-11-15 10:00:00.123" (optionally in brackets / with offset) in text logs.
TEXT_DATETIME_RE = re.compile(
    r"^\[?(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\]?\s+(?P<rest>\S.*)$"
)


def topic_matches(filter_pattern: str, topic: str) -> bool:
    """MQTT-style topic filter (+ single level, # multi level)."""
    pattern = filter_pattern.strip()
    if not pattern:
        return True
    if "+" not in pattern and "#" not in pattern:
        return pattern in topic

    regex_parts: list[str] = []
    levels = pattern.split("/")
    for i, level in enumerate(levels):
        if level == "#":
            if i != len(levels) - 1:
                return False
            regex_parts.append(".*")
            break
        if level == "+":
            regex_parts.append("[^/]+")
        else:
            regex_parts.append(re.escape(level))
    regex = "^" + "/".join(regex_parts) + "$"
    return re.match(regex, topic) is not None


def parse_timestamp(value: Any) -> float | None:
    """Return UNIX epoch seconds. Datetimes without an offset are treated as JST."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
    else:
        text = str(value).strip().strip("[]")
        try:
            number = float(text)
        except ValueError:
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=LOCAL_TZ)
            return dt.timestamp()
    if number > 1e17:
        return number / 1e9
    if number > 1e14:
        return number / 1e6
    if number > 1e11:
        return number / 1e3
    return number


def _get_path(row: dict[str, Any], path: str) -> Any:
    current: Any = row
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _pick(row: dict[str, Any], explicit: str, defaults: tuple[str, ...]) -> tuple[Any, str | None]:
    if explicit:
        return _get_path(row, explicit), explicit
    for key in defaults:
        if key in row and row[key] not in (None, ""):
            return row[key], key
    return None, None


def _normalize_payload(raw: Any) -> tuple[Any, str]:
    if isinstance(raw, (dict, list)):
        text = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
        return raw, text
    if raw is None:
        return "", ""
    text = str(raw)
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
            return parsed, json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
        except json.JSONDecodeError:
            pass
    return text, text


def _make_message(index: int, ts_raw: Any, topic: str, qos_raw: Any, payload_raw: Any) -> MqttMessage:
    try:
        qos = int(qos_raw) if qos_raw not in (None, "") else 0
    except (TypeError, ValueError):
        qos = 0
    payload, payload_text = _normalize_payload(payload_raw)
    ts_text = str(ts_raw).strip() if ts_raw not in (None, "") else f"row-{index}"
    # Every field is already normalized above; skipping validation keeps large logs fast.
    return MqttMessage.model_construct(
        id=index,
        ts=ts_text,
        ts_epoch=parse_timestamp(ts_raw),
        topic=topic,
        qos=qos,
        payload=payload,
        payload_text=payload_text,
        dt_ms=None,
    )


def _row_to_message(index: int, row: dict[str, Any], mapping: FieldMapping) -> MqttMessage | None:
    topic_raw, topic_key = _pick(row, mapping.topic, TOPIC_KEYS)
    topic = str(topic_raw or "").strip()
    if not topic:
        return None

    ts_raw, ts_key = _pick(row, mapping.ts, TS_KEYS)
    qos_raw, qos_key = _pick(row, mapping.qos, QOS_KEYS)
    payload_raw, payload_key = _pick(row, mapping.payload, PAYLOAD_KEYS)

    if payload_key is None or (mapping.payload and payload_raw is None):
        # Flat records: everything that is not ts/topic/qos is the payload.
        used = {k.split(".")[0] for k in (ts_key, topic_key, qos_key) if k}
        payload_raw = {k: v for k, v in row.items() if k not in used}

    return _make_message(index, ts_raw, topic, qos_raw, payload_raw)


def parse_jsonl(text: str, mapping: FieldMapping | None = None) -> list[MqttMessage]:
    mapping = mapping or FieldMapping()
    messages: list[MqttMessage] = []
    for i, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        msg = _row_to_message(i, row, mapping)
        if msg:
            messages.append(msg)
    return messages


def parse_json_array(text: str, mapping: FieldMapping | None = None) -> list[MqttMessage]:
    mapping = mapping or FieldMapping()
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("JSON root must be an array of objects")
    messages: list[MqttMessage] = []
    for i, row in enumerate(data, start=1):
        if not isinstance(row, dict):
            continue
        msg = _row_to_message(i, row, mapping)
        if msg:
            messages.append(msg)
    return messages


def parse_csv(text: str, mapping: FieldMapping | None = None) -> list[MqttMessage]:
    mapping = mapping or FieldMapping()
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    messages: list[MqttMessage] = []
    for i, row in enumerate(reader, start=1):
        msg = _row_to_message(i, {k.strip(): v for k, v in row.items() if k}, mapping)
        if msg:
            messages.append(msg)
    return messages


def parse_text(text: str) -> list[MqttMessage]:
    """Plain text lines: `[timestamp] topic payload` (e.g. `mosquitto_sub -v` output)."""
    messages: list[MqttMessage] = []
    for i, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        ts_raw: str | None = None
        match = TEXT_DATETIME_RE.match(line)
        if match:
            ts_raw, line = match.group("ts"), match.group("rest")
        else:
            parts = line.split(maxsplit=1)
            if len(parts) == 2 and parse_timestamp(parts[0]) is not None:
                ts_raw, line = parts[0], parts[1]
        parts = line.split(maxsplit=1)
        topic = parts[0]
        payload_raw = parts[1] if len(parts) == 2 else ""
        messages.append(_make_message(i, ts_raw, topic, 0, payload_raw))
    return messages


def _looks_like_csv(text: str) -> bool:
    first = next((line for line in text.splitlines() if line.strip()), "")
    header = [h.strip().strip('"').lower() for h in re.split(r"[,\t;]", first)]
    return "topic" in header


def parse_log_bytes(
    filename: str, content: bytes, mapping: FieldMapping | None = None
) -> list[MqttMessage]:
    text = content.decode("utf-8-sig", errors="replace")
    lower = filename.lower()
    stripped = text.lstrip()

    if lower.endswith((".jsonl", ".ndjson")):
        return parse_jsonl(text, mapping)
    if lower.endswith((".csv", ".tsv")):
        return parse_csv(text, mapping)
    if lower.endswith(".json"):
        return parse_json_array(text, mapping)

    if stripped.startswith("["):
        try:
            return parse_json_array(text, mapping)
        except (json.JSONDecodeError, ValueError):
            pass
    if stripped.startswith("{"):
        return parse_jsonl(text, mapping)
    if _looks_like_csv(text) or (mapping and mapping.topic):
        return parse_csv(text, mapping)
    return parse_text(text)


def filter_messages(messages: list[MqttMessage], query: FilterQuery) -> list[MqttMessage]:
    payload_q = query.payload.strip().lower()
    t_from = parse_timestamp(query.time_from) if query.time_from.strip() else None
    t_to = parse_timestamp(query.time_to) if query.time_to.strip() else None
    result: list[MqttMessage] = []
    for msg in messages:
        if not topic_matches(query.topic, msg.topic):
            continue
        if query.qos is not None and msg.qos != query.qos:
            continue
        if payload_q and payload_q not in msg.payload_text.lower():
            continue
        if t_from is not None and (msg.ts_epoch is None or msg.ts_epoch < t_from):
            continue
        if t_to is not None and (msg.ts_epoch is None or msg.ts_epoch > t_to):
            continue
        result.append(msg)
    return result


def delta_ms(prev_epoch: float | None, epoch: float | None) -> float | None:
    if prev_epoch is None or epoch is None:
        return None
    return round((epoch - prev_epoch) * 1000, 3)


def assign_topic_deltas(messages: list[MqttMessage]) -> list[MqttMessage]:
    """Set dt_ms in place: interval since the previous message on the same topic in the whole log."""
    last_seen: dict[str, float] = {}
    for msg in messages:
        msg.dt_ms = delta_ms(last_seen.get(msg.topic), msg.ts_epoch)
        if msg.ts_epoch is not None:
            last_seen[msg.topic] = msg.ts_epoch
    return messages


def build_parse_result(
    messages: list[MqttMessage],
    limit: int | None = None,
    medians: dict[str, float] | None = None,
    thresholds: dict[str, float] | None = None,
) -> ParseResult:
    medians = medians or {}
    thresholds = thresholds or {}
    counts: dict[str, int] = defaultdict(int)
    gaps: dict[str, int] = defaultdict(int)
    max_dt: dict[str, float] = {}
    first: dict[str, float] = {}
    last: dict[str, float] = {}
    for msg in messages:
        topic = msg.topic
        counts[topic] += 1
        if msg.ts_epoch is not None:
            first.setdefault(topic, msg.ts_epoch)
            last[topic] = msg.ts_epoch
        if msg.dt_ms is not None:
            if msg.dt_ms > max_dt.get(topic, float("-inf")):
                max_dt[topic] = msg.dt_ms
            limit_ms = thresholds.get(topic)
            if limit_ms is not None and msg.dt_ms > limit_ms:
                gaps[topic] += 1

    stats: list[TopicStat] = []
    for topic, count in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        span = last.get(topic, 0.0) - first.get(topic, 0.0)
        rate = round((count - 1) / span, 3) if count > 1 and span > 0 else None
        stats.append(
            TopicStat(
                topic=topic,
                count=count,
                rate_hz=rate,
                median_ms=medians.get(topic),
                threshold_ms=thresholds.get(topic),
                max_dt_ms=max_dt.get(topic),
                gap_count=gaps.get(topic, 0),
            )
        )

    shown = messages if limit is None else messages[:limit]
    return ParseResult(
        messages=shown,
        topic_stats=stats,
        total=len(messages),
        shown=len(shown),
        gap_total=sum(gaps.values()),
    )
