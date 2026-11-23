"""Generate sample_data/sample_periodic.log: periodic MQTT traffic with injected gaps.

Injected anomalies (JST):
- fleet/vehicle/002/telemetry: link drop, no messages 10:00:07.000-10:00:07.800
- fleet/vehicle/001/telemetry: seq 123-125 delayed and delivered as a burst around 10:00:12.58
- infra/rsu/12/signal: the 10:00:15 message is lost
"""

from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta
from pathlib import Path

START = datetime(2026, 11, 15, 10, 0, 0)
DURATION_S = 20
OUT = Path(__file__).resolve().parent.parent / "sample_data" / "sample_periodic.log"
DELAYED_001 = {123: 12.580, 124: 12.585, 125: 12.590}


def ts(offset_s: float) -> str:
    return (START + timedelta(seconds=offset_s)).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def main() -> None:
    rng = random.Random(42)
    rows: list[tuple[float, str, object]] = []

    for vid, phase in (("001", 0.000), ("002", 0.033), ("003", 0.066)):
        topic = f"fleet/vehicle/{vid}/telemetry"
        for seq in range(DURATION_S * 10):
            t = phase + seq * 0.1 + rng.uniform(-0.004, 0.004)
            if vid == "002" and 7.0 <= t < 7.8:
                continue
            if vid == "001" and seq in DELAYED_001:
                t = DELAYED_001[seq]
            speed = round(30 + 8 * math.sin(seq / 25 + int(vid)), 1)
            payload = {
                "seq": seq,
                "speed_kmh": speed,
                "lat": round(35.6810 + seq * 0.00001, 6),
                "lon": round(139.7670 + int(vid) * 0.001, 6),
            }
            rows.append((t, topic, payload))

    phases = ["green", "green", "green", "yellow", "red", "red", "red", "red"]
    for sec in range(DURATION_S):
        if sec == 15:
            continue
        rows.append((sec + 0.5, "infra/rsu/12/signal", {"phase": phases[sec % len(phases)]}))

    rows.append((7.850, "ops/alert", {"level": "warn", "code": "LINK_DOWN", "vehicle": "002"}))
    rows.append((7.900, "fleet/vehicle/002/event", {"type": "link_recovered"}))
    rows.append((12.600, "ops/alert", {"level": "warn", "code": "LATENCY_SPIKE", "vehicle": "001"}))

    rows.sort(key=lambda r: r[0])
    lines = [
        f"{ts(t)} {topic} {json.dumps(payload, separators=(',', ':'))}" for t, topic, payload in rows
    ]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {len(lines)} lines to {OUT}")


if __name__ == "__main__":
    main()
