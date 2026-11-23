from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from statistics import median

from app.models import GapSettings, MqttMessage
from app.parser import topic_matches

# A topic needs this many intervals before its median is trusted as the "normal" period.
MIN_INTERVALS = 5


def topic_median_intervals(messages: Iterable[MqttMessage]) -> dict[str, float]:
    samples: dict[str, list[float]] = defaultdict(list)
    for msg in messages:
        if msg.dt_ms is not None and msg.dt_ms > 0:
            samples[msg.topic].append(msg.dt_ms)
    return {topic: median(values) for topic, values in samples.items() if len(values) >= MIN_INTERVALS}


def gap_thresholds(
    topics: Iterable[str], medians: dict[str, float], settings: GapSettings
) -> dict[str, float]:
    """Per-topic Δt (ms) above which a message counts as a gap."""
    if settings.mode == "off":
        return {}
    result: dict[str, float] = {}
    for topic in topics:
        if not topic_matches(settings.topic, topic):
            continue
        if settings.mode == "fixed":
            result[topic] = settings.fixed_ms
        elif topic in medians:
            result[topic] = round(medians[topic] * settings.factor, 3)
    return result


def is_gap(msg: MqttMessage, thresholds: dict[str, float]) -> bool:
    limit = thresholds.get(msg.topic)
    return limit is not None and msg.dt_ms is not None and msg.dt_ms > limit
