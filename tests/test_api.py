from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_sample_then_filter_then_export_csv():
    assert client.get("/api/sample").status_code == 200

    res = client.get("/api/messages", params={"topic": "infra/#"})
    assert res.status_code == 200
    assert {m["topic"] for m in res.json()["messages"]} <= {
        "infra/rsu/12/signal",
        "infra/rsu/12/broadcast",
    }

    res = client.get("/api/export", params={"format": "csv", "topic": "ops/alert"})
    assert res.status_code == 200
    assert "attachment" in res.headers["content-disposition"]
    body = res.content.decode("utf-8-sig").splitlines()
    assert body[0] == "ts,topic,qos,dt_ms,gap,payload"
    assert len(body) == 2
    assert "LATENCY_SPIKE" in body[1]


def test_export_graph_csv_splits_payload_fields():
    client.get("/api/sample")
    res = client.get(
        "/api/export", params={"format": "graph", "topic": "fleet/vehicle/001/telemetry"}
    )
    assert res.status_code == 200
    assert "mqtt_graph_" in res.headers["content-disposition"]
    lines = res.content.decode("utf-8-sig").splitlines()
    assert lines[0] == "ts,elapsed_s,topic,speed_kmh,lat,lon,heading"
    assert lines[1] == "2026-11-15 10:00:00.120,0.0,fleet/vehicle/001/telemetry,32.4,35.6812,139.7671,90"
    assert lines[2].startswith("2026-11-15 10:00:01.480,1.36,")


def test_gaps_only_view_and_csv_flag():
    assert client.get("/api/sample", params={"name": "sample_periodic.log"}).status_code == 200

    res = client.get("/api/messages", params={"gaps_only": "true"})
    data = res.json()
    assert data["gap_total"] == 3
    assert [m["topic"] for m in data["messages"]] == [
        "fleet/vehicle/002/telemetry",
        "fleet/vehicle/001/telemetry",
        "infra/rsu/12/signal",
    ]

    res = client.get("/api/messages", params={"gaps_only": "true", "gap_topic": "infra/#"})
    assert [m["topic"] for m in res.json()["messages"]] == ["infra/rsu/12/signal"]

    res = client.get("/api/export", params={"format": "csv", "topic": "fleet/vehicle/002/telemetry"})
    rows = res.content.decode("utf-8-sig").splitlines()
    flagged = [r for r in rows[1:] if r.split(",")[4] == "1"]
    assert len(flagged) == 1
    assert flagged[0].startswith("2026-11-15 10:00:07.831,")

    assert client.get("/api/messages", params={"gap_mode": "bogus"}).status_code == 422


def test_export_jsonl_roundtrip():
    client.get("/api/sample")
    res = client.get("/api/export", params={"format": "jsonl", "payload": "near_miss"})
    assert res.status_code == 200
    lines = res.text.strip().splitlines()
    assert len(lines) == 1

    reupload = client.post(
        "/api/upload", files={"file": ("export.jsonl", res.content, "application/x-ndjson")}
    )
    assert reupload.status_code == 200
    assert reupload.json()["messages"][0]["payload"]["type"] == "near_miss"


def test_upload_with_mapping():
    content = b'{"when":"2026-08-11T10:00:00Z","ch":"x/y","v":1}\n'
    res = client.post(
        "/api/upload",
        files={"file": ("m.jsonl", content, "application/x-ndjson")},
        data={"ts_key": "when", "topic_key": "ch"},
    )
    assert res.status_code == 200
    msg = res.json()["messages"][0]
    assert msg["topic"] == "x/y"
    assert msg["payload"] == {"v": 1}


def test_upload_unparseable_returns_400():
    res = client.post(
        "/api/upload",
        files={"file": ("bad.jsonl", b"not json\n", "application/x-ndjson")},
    )
    assert res.status_code == 400
