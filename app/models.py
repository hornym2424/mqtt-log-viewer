from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class MqttMessage(BaseModel):
    id: int
    ts: str
    ts_epoch: float | None = None
    topic: str
    qos: int = 0
    payload: Any
    payload_text: str = ""
    dt_ms: float | None = None


class FieldMapping(BaseModel):
    """Key names (dotted paths allowed) used to read each column from a log record."""

    ts: str = ""
    topic: str = ""
    qos: str = ""
    payload: str = ""


class FilterQuery(BaseModel):
    topic: str = ""
    payload: str = ""
    qos: int | None = None
    time_from: str = ""
    time_to: str = ""


class GapSettings(BaseModel):
    """How to decide that a message arrived too late after the previous one on its topic."""

    mode: Literal["off", "auto", "fixed"] = "auto"
    factor: float = 1.5
    fixed_ms: float = 1000.0
    topic: str = ""


class TopicStat(BaseModel):
    topic: str
    count: int
    rate_hz: float | None = None
    median_ms: float | None = None
    threshold_ms: float | None = None
    max_dt_ms: float | None = None
    gap_count: int = 0


class ParseResult(BaseModel):
    messages: list[MqttMessage]
    topic_stats: list[TopicStat]
    total: int
    shown: int
    gap_total: int = 0


class LiveConnectRequest(BaseModel):
    host: str = "localhost"
    port: int = 1883
    topic: str = "#"
    username: str | None = None
    password: str | None = None
    client_id: str = Field(default="mqtt-log-viewer")
