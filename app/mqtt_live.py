from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any

import paho.mqtt.client as mqtt

from app.models import LiveConnectRequest, MqttMessage
from app.parser import LOCAL_TZ, _normalize_payload, delta_ms


class LiveMqttSession:
    """In-memory live MQTT capture for local demos."""

    def __init__(self, max_messages: int = 2000) -> None:
        self._lock = threading.Lock()
        self._messages: deque[MqttMessage] = deque(maxlen=max_messages)
        self._next_id = 1
        self._last_seen: dict[str, float] = {}
        self._client: mqtt.Client | None = None
        self._connected = False
        self._error: str | None = None
        self._config: LiveConnectRequest | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def error(self) -> str | None:
        return self._error

    def connect(self, config: LiveConnectRequest) -> None:
        self.disconnect()
        self._config = config
        self._error = None

        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=config.client_id,
        )
        if config.username:
            client.username_pw_set(config.username, config.password or "")

        def on_connect(client: mqtt.Client, _userdata: Any, _flags: Any, reason_code: Any, _props: Any = None) -> None:
            if reason_code != 0 and str(reason_code) not in {"Success", "0"}:
                self._connected = False
                self._error = f"connect failed: {reason_code}"
                return
            self._connected = True
            self._error = None
            client.subscribe(config.topic)

        def on_message(_client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
            try:
                raw_text = msg.payload.decode("utf-8", errors="replace")
            except Exception:
                raw_text = str(msg.payload)
            payload, payload_text = _normalize_payload(raw_text)
            now = datetime.now(LOCAL_TZ)
            ts = now.strftime("%Y-%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}"
            epoch = now.timestamp()
            with self._lock:
                item = MqttMessage(
                    id=self._next_id,
                    ts=ts,
                    ts_epoch=epoch,
                    topic=msg.topic,
                    qos=int(msg.qos),
                    payload=payload,
                    payload_text=payload_text,
                    dt_ms=delta_ms(self._last_seen.get(msg.topic), epoch),
                )
                self._last_seen[msg.topic] = epoch
                self._next_id += 1
                self._messages.append(item)

        def on_disconnect(_client: mqtt.Client, _userdata: Any, _flags: Any, reason_code: Any, _props: Any = None) -> None:
            self._connected = False
            if reason_code not in (0, "Normal disconnection", "Success"):
                self._error = f"disconnected: {reason_code}"

        client.on_connect = on_connect
        client.on_message = on_message
        client.on_disconnect = on_disconnect

        try:
            client.connect(config.host, config.port, keepalive=30)
        except Exception as exc:  # noqa: BLE001
            self._error = str(exc)
            self._connected = False
            raise

        client.loop_start()
        self._client = client
        # brief wait so UI can reflect connect result
        time.sleep(0.3)

    def disconnect(self) -> None:
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
        self._client = None
        self._connected = False

    def clear(self) -> None:
        with self._lock:
            self._messages.clear()
            self._last_seen.clear()
            self._next_id = 1

    def snapshot(self) -> list[MqttMessage]:
        with self._lock:
            return list(self._messages)

    def status(self) -> dict[str, Any]:
        return {
            "connected": self._connected,
            "error": self._error,
            "config": self._config.model_dump() if self._config else None,
            "count": len(self._messages),
        }


live_session = LiveMqttSession()
