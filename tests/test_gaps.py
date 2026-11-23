from pathlib import Path

from app.gaps import gap_thresholds, is_gap, topic_median_intervals
from app.models import GapSettings
from app.parser import assign_topic_deltas, build_parse_result, parse_log_bytes

PERIODIC = Path(__file__).resolve().parent.parent / "sample_data" / "sample_periodic.log"


def _load():
    messages = assign_topic_deltas(parse_log_bytes(PERIODIC.name, PERIODIC.read_bytes()))
    return messages, topic_median_intervals(messages)


def _gaps(settings: GapSettings):
    messages, medians = _load()
    thresholds = gap_thresholds({m.topic for m in messages}, medians, settings)
    return [m for m in messages if is_gap(m, thresholds)]


def test_auto_mode_finds_exactly_the_injected_anomalies():
    gaps = _gaps(GapSettings())
    assert [(m.ts, m.topic) for m in gaps] == [
        ("2026-11-15 10:00:07.831", "fleet/vehicle/002/telemetry"),
        ("2026-11-15 10:00:12.580", "fleet/vehicle/001/telemetry"),
        ("2026-11-15 10:00:16.500", "infra/rsu/12/signal"),
    ]


def test_irregular_topics_have_no_baseline():
    _, medians = _load()
    assert "ops/alert" not in medians
    assert "fleet/vehicle/002/event" not in medians
    assert round(medians["fleet/vehicle/001/telemetry"]) == 100
    assert round(medians["infra/rsu/12/signal"]) == 1000


def test_fixed_threshold_and_topic_target():
    gaps = _gaps(GapSettings(mode="fixed", fixed_ms=500, topic="fleet/vehicle/+/telemetry"))
    assert [m.topic for m in gaps] == ["fleet/vehicle/002/telemetry"]


def test_off_mode_reports_nothing():
    assert _gaps(GapSettings(mode="off")) == []


def test_stats_include_gap_counts():
    messages, medians = _load()
    thresholds = gap_thresholds({m.topic for m in messages}, medians, GapSettings())
    result = build_parse_result(messages, None, medians, thresholds)
    by_topic = {s.topic: s for s in result.topic_stats}
    assert result.gap_total == 3
    assert by_topic["fleet/vehicle/002/telemetry"].gap_count == 1
    assert by_topic["fleet/vehicle/002/telemetry"].max_dt_ms == 902.0
    assert by_topic["fleet/vehicle/003/telemetry"].gap_count == 0
    assert by_topic["ops/alert"].threshold_ms is None
