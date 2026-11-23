from pathlib import Path

from app.models import FieldMapping, FilterQuery
from app.parser import (
    build_parse_result,
    filter_messages,
    parse_jsonl,
    parse_log_bytes,
    parse_timestamp,
    assign_topic_deltas,
    topic_matches,
)

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data" / "sample.jsonl"


def test_topic_matches_exact_and_wildcards():
    assert topic_matches("fleet/vehicle/001/telemetry", "fleet/vehicle/001/telemetry")
    assert topic_matches("fleet/vehicle/+/telemetry", "fleet/vehicle/001/telemetry")
    assert not topic_matches("fleet/vehicle/+/telemetry", "fleet/vehicle/001/status")
    assert topic_matches("fleet/#", "fleet/vehicle/001/telemetry")
    assert topic_matches("telemetry", "fleet/vehicle/001/telemetry")


def test_parse_jsonl_sample():
    messages = parse_jsonl(SAMPLE.read_text(encoding="utf-8"))
    assert len(messages) >= 10
    assert messages[0].topic.startswith("fleet/")
    assert messages[0].ts_epoch is not None


def test_parse_timestamp_variants():
    iso = parse_timestamp("2026-11-15T01:00:00Z")
    assert parse_timestamp("2026-11-15T10:00:00+09:00") == iso
    assert parse_timestamp("2026-11-15 10:00:00") == iso
    assert abs(parse_timestamp("2026-11-15 10:00:00.123") - (iso + 0.123)) < 1e-6
    assert parse_timestamp(iso) == iso
    assert parse_timestamp(iso * 1000) == iso
    assert parse_timestamp(str(int(iso * 1000))) == iso
    assert parse_timestamp("not-a-time") is None


def test_text_format_like_mosquitto_sub():
    text = (
        'fleet/vehicle/001/status {"state":"moving"}\n'
        "1786442400.5 infra/rsu/12/broadcast RSU_HEARTBEAT\n"
    )
    messages = parse_log_bytes("capture.log", text.encode())
    assert [m.topic for m in messages] == ["fleet/vehicle/001/status", "infra/rsu/12/broadcast"]
    assert messages[0].payload == {"state": "moving"}
    assert messages[1].ts_epoch == 1786442400.5
    assert messages[1].payload == "RSU_HEARTBEAT"


def test_text_format_with_space_separated_datetime():
    text = (
        '2026-11-15 10:00:00.123 fleet/vehicle/001/telemetry {"speed_kmh":32.4}\n'
        "[2026-11-15 10:00:01.500] infra/rsu/12/broadcast RSU_HEARTBEAT\n"
    )
    messages = parse_log_bytes("capture.log", text.encode())
    assert [m.ts for m in messages] == ["2026-11-15 10:00:00.123", "2026-11-15 10:00:01.500"]
    assert [m.topic for m in messages] == ["fleet/vehicle/001/telemetry", "infra/rsu/12/broadcast"]
    assert messages[0].payload == {"speed_kmh": 32.4}
    assert abs((messages[1].ts_epoch - messages[0].ts_epoch) - 1.377) < 1e-6


def test_text_sample_matches_jsonl_sample():
    text_log = parse_log_bytes("sample.log", (SAMPLE.parent / "sample.log").read_bytes())
    jsonl = parse_jsonl(SAMPLE.read_text(encoding="utf-8"))
    assert [(m.ts, m.topic, m.payload) for m in text_log] == [(m.ts, m.topic, m.payload) for m in jsonl]


def test_field_mapping_nested_and_flat_payload():
    text = (
        '{"meta":{"time":"2026-11-15 10:00:00.000","t":"a/b"},"speed":1.5}\n'
        '{"meta":{"time":"2026-11-15 10:00:01.000","t":"a/c"},"speed":2.0}\n'
    )
    mapping = FieldMapping(ts="meta.time", topic="meta.t")
    messages = parse_log_bytes("x.jsonl", text.encode(), mapping)
    assert [m.topic for m in messages] == ["a/b", "a/c"]
    assert messages[0].payload == {"speed": 1.5}


def test_csv_with_custom_columns_and_semicolon():
    text = "time;channel;level\n2026-11-15 10:00:00.123;dev/1/temp;42\n"
    mapping = FieldMapping(ts="time", topic="channel", payload="level")
    messages = parse_log_bytes("x.csv", text.encode(), mapping)
    assert len(messages) == 1
    assert messages[0].topic == "dev/1/temp"
    assert messages[0].payload == "42"


def test_time_filter_and_deltas():
    messages = parse_jsonl(SAMPLE.read_text(encoding="utf-8"))
    query = FilterQuery(time_from="2026-11-15 10:00:01", time_to="2026-11-15 10:00:02.5")
    filtered = filter_messages(messages, query)
    assert filtered
    assert all("10:00:01" <= m.ts[11:19] <= "10:00:02" for m in filtered)

    result = build_parse_result(filter_messages(messages, FilterQuery(topic="fleet/vehicle/001/telemetry")))
    assert result.topic_stats[0].rate_hz is not None


def test_deltas_are_measured_on_the_whole_log():
    messages = assign_topic_deltas(parse_jsonl(SAMPLE.read_text(encoding="utf-8")))
    telemetry = filter_messages(messages, FilterQuery(topic="fleet/vehicle/001/telemetry"))
    assert [m.dt_ms for m in telemetry][:2] == [None, 1360.0]

    window = filter_messages(messages, FilterQuery(time_from="2026-11-15 10:00:01.4"))
    first_telemetry = next(m for m in window if m.topic == "fleet/vehicle/001/telemetry")
    assert first_telemetry.dt_ms == 1360.0


def test_limit_reports_total():
    messages = parse_jsonl(SAMPLE.read_text(encoding="utf-8"))
    result = build_parse_result(messages, limit=3)
    assert result.shown == 3
    assert result.total == len(messages)
