#!/usr/bin/env python3

import json
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import paho.mqtt.client as mqtt
import yaml


LOG = logging.getLogger("fridge-controller")


@dataclass
class Reading:
    value: Optional[float] = None
    received_at: Optional[float] = None

    def age(self, now: float) -> Optional[float]:
        if self.received_at is None:
            return None
        return max(0.0, now - self.received_at)


class Controller:
    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.config: dict[str, Any] = {}
        self.config_mtime: Optional[float] = None
        self.last_config_check = 0.0

        self.keg = Reading()
        self.air = Reading()

        self.mode = "STARTUP"
        self.reason = "starting"
        self.output_requested = False
        self.output_actual: Optional[bool] = None

        self.last_on_at: Optional[float] = None
        self.last_off_at: Optional[float] = None
        self.started_at = time.monotonic()
        # If startup minimum-off enforcement is disabled, only the first ON
        # after a confirmed startup OFF may bypass the normal minimum-off timer.
        self.startup_min_off_bypass_available = False

        self.stop_event = threading.Event()
        self.mqtt_connected = False

        self.next_rpc_id = 1
        self.pending_rpc: dict[int, dict[str, Any]] = {}
        self.last_command_attempt = 0.0
        self.last_shelly_status_request = 0.0
        self.last_status_publish = 0.0
        self.last_status_snapshot: Optional[str] = None
        self.command_failures = 0
        self.last_failsafe_refresh: Optional[float] = None

        self.client: Optional[mqtt.Client] = None

        self.load_config(initial=True)
        self.setup_mqtt()

    # ---------- Configuration ----------

    def load_config(self, initial: bool = False) -> bool:
        try:
            text = self.config_path.read_text()
            candidate = yaml.safe_load(text)
            self.validate_config(candidate)
            mtime = self.config_path.stat().st_mtime
        except Exception as exc:
            if initial:
                raise
            LOG.error("Config reload failed; keeping previous config: %s", exc)
            return False

        old_config = self.config
        self.config = candidate
        self.config_mtime = mtime

        if initial:
            self.startup_min_off_bypass_available = not bool(
                self.config["startup"]["enforce_minimum_off_time"]
            )
            LOG.info("Loaded configuration from %s", self.config_path)
        else:
            LOG.info("Reloaded configuration from %s", self.config_path)
            # Changing this switch at runtime changes whether the first post-startup
            # ON may bypass the minimum-off timer.
            self.startup_min_off_bypass_available = not bool(
                self.config["startup"]["enforce_minimum_off_time"]
            ) and self.last_on_at is None

            if old_config.get("control", {}).get("setpoint") != self.config["control"]["setpoint"]:
                LOG.info("Setpoint changed to %.3f °C", self.config["control"]["setpoint"])

        return True

    @staticmethod
    def validate_config(cfg: Any) -> None:
        if not isinstance(cfg, dict):
            raise ValueError("config must be a YAML mapping")

        for section in ("mqtt", "control", "startup", "runtime"):
            if section not in cfg or not isinstance(cfg[section], dict):
                raise ValueError(f"missing section: {section}")

        if "shelly" not in cfg or not isinstance(cfg["shelly"], dict):
            raise ValueError("missing section: shelly")

        c = cfg["control"]
        required_control = (
            "setpoint", "hysteresis", "degraded_air_setpoint", "degraded_air_hysteresis",
            "minimum_off_time", "minimum_on_time", "sensor_stale_after",
            "minimum_air_temperature", "valid_temperature_min", "valid_temperature_max",
        )
        for key in required_control:
            if key not in c:
                raise ValueError(f"missing control.{key}")

        for hname in ("hysteresis", "degraded_air_hysteresis"):
            h = c[hname]
            if not isinstance(h, dict) or "on_above" not in h or "off_below" not in h:
                raise ValueError(f"{hname} must contain on_above and off_below")
            if float(h["on_above"]) < 0 or float(h["off_below"]) < 0:
                raise ValueError(f"{hname} values must be >= 0")

        for key in ("minimum_off_time", "minimum_on_time", "sensor_stale_after"):
            if float(c[key]) < 0:
                raise ValueError(f"control.{key} must be >= 0")

        if float(c["valid_temperature_min"]) >= float(c["valid_temperature_max"]):
            raise ValueError("valid_temperature_min must be below valid_temperature_max")

        m = cfg["mqtt"]
        for key in (
            "broker", "port", "username", "password_file", "client_id", "keg_topic",
            "air_topic", "shelly_rpc_topic", "shelly_response_topic", "status_prefix",
        ):
            if key not in m:
                raise ValueError(f"missing mqtt.{key}")

        s = cfg["shelly"]
        for key in ("failsafe_timeout", "failsafe_refresh_interval"):
            if key not in s:
                raise ValueError(f"missing shelly.{key}")
            if float(s[key]) <= 0:
                raise ValueError(f"shelly.{key} must be > 0")

    def maybe_reload_config(self, now: float) -> None:
        interval = float(self.config["runtime"]["config_reload_interval"])
        if now - self.last_config_check < interval:
            return
        self.last_config_check = now
        try:
            mtime = self.config_path.stat().st_mtime
        except OSError as exc:
            LOG.error("Cannot stat config file: %s", exc)
            return
        if self.config_mtime is None or mtime != self.config_mtime:
            self.load_config(initial=False)

    # ---------- MQTT ----------

    def setup_mqtt(self) -> None:
        m = self.config["mqtt"]
        password = Path(m["password_file"]).read_text().strip()
        if not password:
            raise ValueError("MQTT password file is empty")

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=str(m["client_id"]),
            clean_session=True,
        )
        client.username_pw_set(str(m["username"]), password)

        prefix = str(m["status_prefix"])
        client.will_set(f"{prefix}/online", payload="false", qos=1, retain=True)

        client.on_connect = self.on_connect
        client.on_disconnect = self.on_disconnect
        client.on_message = self.on_message
        self.client = client

    def on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            LOG.error("MQTT connection failed: %s", reason_code)
            return

        self.mqtt_connected = True
        m = self.config["mqtt"]
        client.subscribe(str(m["keg_topic"]), qos=1)
        client.subscribe(str(m["air_topic"]), qos=1)
        client.subscribe(str(m["shelly_response_topic"]), qos=1)

        prefix = str(m["status_prefix"])
        client.publish(f"{prefix}/online", "true", qos=1, retain=True)
        LOG.info("Connected to MQTT broker and subscribed to inputs")

        # Establish a conservative, known actuator state on every controller connection/start.
        self.output_requested = False
        self.send_switch(False, force=True)
        self.send_get_status(force=True)

    def on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties) -> None:
        self.mqtt_connected = False
        LOG.warning("Disconnected from MQTT broker: %s", reason_code)

    def on_message(self, client, userdata, message) -> None:
        topic = message.topic
        payload = message.payload.decode("utf-8", errors="replace")
        now = time.monotonic()
        m = self.config["mqtt"]

        if topic == m["keg_topic"]:
            self.handle_temperature("keg", payload, now)
        elif topic == m["air_topic"]:
            self.handle_temperature("air", payload, now)
        elif topic == m["shelly_response_topic"]:
            self.handle_rpc_response(payload, now)

    def handle_temperature(self, which: str, payload: str, now: float) -> None:
        try:
            value = float(payload)
        except ValueError:
            LOG.warning("Invalid %s temperature payload: %r", which, payload)
            return

        c = self.config["control"]
        if not (float(c["valid_temperature_min"]) <= value <= float(c["valid_temperature_max"])):
            LOG.warning("Rejected implausible %s temperature %.3f °C", which, value)
            return

        reading = self.keg if which == "keg" else self.air
        reading.value = value
        reading.received_at = now
        LOG.debug("%s temperature: %.3f °C", which, value)

    # ---------- Shelly RPC ----------

    def new_rpc_id(self) -> int:
        rid = self.next_rpc_id
        self.next_rpc_id += 1
        if self.next_rpc_id > 2_000_000_000:
            self.next_rpc_id = 1
        return rid

    def send_rpc(self, method: str, params: Optional[dict[str, Any]] = None,
                 context: Optional[dict[str, Any]] = None) -> Optional[int]:
        if not self.mqtt_connected or self.client is None:
            return None

        rid = self.new_rpc_id()
        payload: dict[str, Any] = {
            "id": rid,
            "src": "fridge-controller",
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        info = self.client.publish(
            str(self.config["mqtt"]["shelly_rpc_topic"]),
            json.dumps(payload, separators=(",", ":")),
            qos=1,
            retain=False,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            LOG.error("Failed to queue RPC %s: rc=%s", method, info.rc)
            return None

        self.pending_rpc[rid] = {
            "method": method,
            "sent_at": time.monotonic(),
            "context": context or {},
        }
        return rid

    def send_switch(self, on: bool, force: bool = False) -> None:
        now = time.monotonic()
        retry_delay = float(self.config["runtime"]["command_retry_delay"])
        if not force and now - self.last_command_attempt < retry_delay:
            return

        # Don't stack multiple Switch.Set calls.
        if any(v["method"] == "Switch.Set" for v in self.pending_rpc.values()):
            return

        self.last_command_attempt = now
        params: dict[str, Any] = {"id": 0, "on": bool(on)}
        if on:
            params["toggle_after"] = float(self.config["shelly"]["failsafe_timeout"])
        rid = self.send_rpc(
            "Switch.Set",
            params,
            context={"desired": bool(on)},
        )
        if rid is not None:
            LOG.info("Requested Shelly output %s (RPC id %d)", "ON" if on else "OFF", rid)

        if on:
            self.last_failsafe_refresh = now
        elif not on:
            self.last_failsafe_refresh = None

    def send_get_status(self, force: bool = False) -> None:
        now = time.monotonic()
        interval = float(self.config["runtime"]["shelly_status_interval"])
        if not force and now - self.last_shelly_status_request < interval:
            return
        if any(v["method"] == "Shelly.GetStatus" for v in self.pending_rpc.values()):
            return
        self.last_shelly_status_request = now
        self.send_rpc("Shelly.GetStatus")

    def handle_rpc_response(self, payload: str, now: float) -> None:
        try:
            msg = json.loads(payload)
        except json.JSONDecodeError:
            LOG.warning("Invalid Shelly RPC JSON: %r", payload)
            return

        rid = msg.get("id")
        if not isinstance(rid, int):
            LOG.debug("Ignoring RPC message without integer id: %s", msg)
            return

        pending = self.pending_rpc.pop(rid, None)
        if pending is None:
            LOG.debug("RPC response for unknown id %s", rid)
            return

        if "error" in msg:
            self.command_failures += 1
            LOG.error("Shelly RPC %s failed: %s", pending["method"], msg["error"])
            return

        result = msg.get("result", {})
        method = pending["method"]

        if method == "Switch.Set":
            desired = bool(pending["context"].get("desired"))
            # A successful Switch.Set RPC means the relay command was accepted. We still
            # periodically verify it via Shelly.GetStatus.
            self.update_actual_output(desired, now, source="Switch.Set RPC")
            self.command_failures = 0
            self.send_get_status(force=True)

        elif method == "Shelly.GetStatus":
            switch = result.get("switch:0")
            if isinstance(switch, dict) and isinstance(switch.get("output"), bool):
                self.update_actual_output(bool(switch["output"]), now, source="GetStatus")
            else:
                LOG.warning("GetStatus result contained no switch:0.output")

    def update_actual_output(self, new_state: bool, now: float, source: str) -> None:
        old = self.output_actual
        self.output_actual = new_state
        if old is None or old != new_state:
            if new_state:
                self.last_on_at = now
                self.startup_min_off_bypass_available = False
            else:
                self.last_off_at = now
            LOG.info("Shelly actual output is now %s (%s)", "ON" if new_state else "OFF", source)

    def expire_rpc_requests(self, now: float) -> None:
        timeout = float(self.config["runtime"]["rpc_timeout"])
        expired = [rid for rid, p in self.pending_rpc.items() if now - p["sent_at"] > timeout]
        for rid in expired:
            pending = self.pending_rpc.pop(rid)
            self.command_failures += 1
            LOG.error("Shelly RPC timeout: %s (id %s)", pending["method"], rid)

    # ---------- Control logic ----------

    def is_fresh(self, reading: Reading, now: float) -> bool:
        age = reading.age(now)
        return age is not None and age <= float(self.config["control"]["sensor_stale_after"])

    def determine_mode(self, now: float) -> str:
        keg_fresh = self.is_fresh(self.keg, now)
        air_fresh = self.is_fresh(self.air, now)

        if self.startup_hold_active(now):
            return "STARTUP"
        if keg_fresh and air_fresh:
            return "NORMAL"
        if keg_fresh and not air_fresh:
            return "DEGRADED_KEG_ONLY"
        if not keg_fresh and air_fresh:
            return "DEGRADED_AIR"
        return "FAULT"

    def startup_hold_active(self, now: float) -> bool:
        if not self.config["startup"]["enforce_minimum_off_time"]:
            return False
        if self.last_off_at is None:
            # We have not yet confirmed that the Shelly output is OFF.
            return True
        return (now - self.last_off_at) < float(self.config["control"]["minimum_off_time"])

    @staticmethod
    def hysteresis_desired(temp: float, current: bool, setpoint: float,
                           on_above: float, off_below: float) -> tuple[bool, str]:
        upper = setpoint + on_above
        lower = setpoint - off_below
        if temp > upper:
            return True, "above_upper_threshold"
        if temp < lower:
            return False, "below_lower_threshold"
        return current, "within_hysteresis"

    def evaluate(self, now: float) -> None:
        c = self.config["control"]
        new_mode = self.determine_mode(now)
        current_for_logic = bool(self.output_actual) if self.output_actual is not None else False

        # Safety conditions always win and may override minimum_on_time.
        if new_mode == "FAULT":
            desired = False
            reason = "all_sensors_stale"
            safety_override = True

        elif new_mode == "STARTUP":
            desired = False
            if not self.is_fresh(self.keg, now) and not self.is_fresh(self.air, now):
                reason = "startup_waiting_for_sensor_data"
            else:
                reason = "startup_minimum_off_time"
            safety_override = True

        elif self.is_fresh(self.air, now) and self.air.value is not None and \
                self.air.value <= float(c["minimum_air_temperature"]):
            desired = False
            reason = "air_safety_cutoff"
            safety_override = True

        elif new_mode in ("NORMAL", "DEGRADED_KEG_ONLY"):
            assert self.keg.value is not None
            h = c["hysteresis"]
            desired, why = self.hysteresis_desired(
                self.keg.value,
                current_for_logic,
                float(c["setpoint"]),
                float(h["on_above"]),
                float(h["off_below"]),
            )
            reason = f"keg_{why}"
            safety_override = False

        else:  # DEGRADED_AIR
            assert self.air.value is not None
            h = c["degraded_air_hysteresis"]
            desired, why = self.hysteresis_desired(
                self.air.value,
                current_for_logic,
                float(c["degraded_air_setpoint"]),
                float(h["on_above"]),
                float(h["off_below"]),
            )
            reason = f"air_fallback_{why}"
            safety_override = False

        # Compressor cycle protection. Safety OFF decisions override minimum_on_time.
        if desired and not current_for_logic:
            if self.startup_min_off_bypass_available:
                # Explicitly configured exception: the first ON after startup may
                # ignore minimum_off_time, but only after OFF has been confirmed.
                if self.last_off_at is None:
                    desired = False
                    reason = "startup_waiting_for_off_confirmation"
            elif self.last_off_at is None:
                # Conservative when state history is unknown.
                desired = False
                reason = "minimum_off_time_unknown_history"
            else:
                elapsed = now - self.last_off_at
                if elapsed < float(c["minimum_off_time"]):
                    desired = False
                    reason = "minimum_off_time"

        elif not desired and current_for_logic and not safety_override:
            if self.last_on_at is not None:
                elapsed = now - self.last_on_at
                if elapsed < float(c["minimum_on_time"]):
                    desired = True
                    reason = "minimum_on_time"

        self.mode = new_mode
        self.reason = reason
        self.output_requested = desired

        if self.output_actual is None or desired != self.output_actual:
            self.send_switch(desired)

        refresh_interval = float(self.config["shelly"]["failsafe_refresh_interval"])
        if desired and (
            self.last_failsafe_refresh is None
            or now - self.last_failsafe_refresh >= refresh_interval
        ):
            self.send_switch(True, force=True)
            self.last_failsafe_refresh = now
        elif not desired:
            self.last_failsafe_refresh = None

    # ---------- Status publishing ----------

    @staticmethod
    def rounded_age(reading: Reading, now: float) -> Optional[float]:
        age = reading.age(now)
        return None if age is None else round(age, 1)

    def make_status(self, now: float) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "reason": self.reason,
            "setpoint": float(self.config["control"]["setpoint"]),
            "output_requested": self.output_requested,
            "output_actual": self.output_actual,
            "keg_sensor_age": self.rounded_age(self.keg, now),
            "air_sensor_age": self.rounded_age(self.air, now),
            "air_safety_active": self.reason == "air_safety_cutoff",
            "mqtt_connected": self.mqtt_connected,
            "shelly_command_failures": self.command_failures,
        }

    def publish_status(self, now: float, force: bool = False) -> None:
        if not self.mqtt_connected or self.client is None:
            return

        interval = float(self.config["runtime"]["status_publish_interval"])
        status = self.make_status(now)
        snapshot = json.dumps(status, sort_keys=True, separators=(",", ":"))
        changed = snapshot != self.last_status_snapshot
        if not force and not changed and now - self.last_status_publish < interval:
            return

        prefix = str(self.config["mqtt"]["status_prefix"])
        self.client.publish(f"{prefix}/status", snapshot, qos=1, retain=True)
        self.client.publish(f"{prefix}/mode", self.mode, qos=1, retain=True)
        self.client.publish(f"{prefix}/reason", self.reason, qos=1, retain=True)
        self.client.publish(f"{prefix}/setpoint", str(status["setpoint"]), qos=1, retain=True)
        self.client.publish(
            f"{prefix}/output_requested",
            "on" if self.output_requested else "off",
            qos=1,
            retain=True,
        )
        actual = "unknown" if self.output_actual is None else ("on" if self.output_actual else "off")
        self.client.publish(f"{prefix}/output_actual", actual, qos=1, retain=True)

        self.last_status_snapshot = snapshot
        self.last_status_publish = now

    # ---------- Main loop / shutdown ----------

    def run(self) -> None:
        m = self.config["mqtt"]
        LOG.info("Connecting to MQTT broker %s:%s", m["broker"], m["port"])
        self.client.connect(str(m["broker"]), int(m["port"]), keepalive=30)
        self.client.loop_start()

        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                self.maybe_reload_config(now)
                self.expire_rpc_requests(now)
                self.send_get_status()
                self.evaluate(now)
                self.publish_status(now)
                self.stop_event.wait(float(self.config["runtime"]["loop_interval"]))
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        LOG.info("Shutting down: requesting fridge OFF")
        if self.mqtt_connected:
            self.output_requested = False
            self.reason = "controller_shutdown"
            self.mode = "FAULT"
            self.publish_status(time.monotonic(), force=True)
            self.send_switch(False, force=True)
            # Give QoS 1 publish / RPC response a short chance to complete.
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and self.pending_rpc:
                time.sleep(0.05)
            prefix = str(self.config["mqtt"]["status_prefix"])
            self.client.publish(f"{prefix}/online", "false", qos=1, retain=True).wait_for_publish(timeout=1.0)

        if self.client is not None:
            try:
                self.client.disconnect()
            except Exception:
                pass
            self.client.loop_stop()

    def request_stop(self, signum=None, frame=None) -> None:
        LOG.info("Stop requested")
        self.stop_event.set()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    controller = Controller(config_path)
    signal.signal(signal.SIGTERM, controller.request_stop)
    signal.signal(signal.SIGINT, controller.request_stop)
    controller.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
