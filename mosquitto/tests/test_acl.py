"""Test the candidate ACL inside a disposable, network-isolated container."""

from pathlib import Path
import json
import os
import queue
import runpy
import secrets
import socket
import subprocess
import tempfile
import threading
import time


ACL = Path(__file__).resolve().parents[1] / "fridge.acl.example"
USERS = ("hydrom", "telegraf", "fridge-sensor", "splug", "fridge-controller")
PORT = "18883"
PASSWORDS = {}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def client(command, user=None):
    args = [
        command, "-h", "127.0.0.1", "-p", PORT,
        "-V", "mqttv5",
    ]
    if user:
        args += ["-u", user, "-P", PASSWORDS[user]]
    return args


def publish(user, topic, payload, retain=False, client_id=None):
    args = client("mosquitto_pub", user)
    args += ["-q", "1", "-t", topic, "-m", payload]
    if retain:
        args.append("-r")
    if client_id:
        args += ["-i", client_id]
    return subprocess.run(
        args, capture_output=True, text=True, timeout=5,
    )


class Observer:
    def __init__(self, user="telegraf", topic="#", will=None):
        self.lines = queue.Queue()
        args = ["stdbuf", "-oL"] + client("mosquitto_sub", user)
        args += ["-q", "1", "-t", topic, "-F", "%p", "-d"]
        if will:
            args += [
                "--will-topic", will[0],
                "--will-payload", will[1],
                "--will-qos", "1",
                "--will-retain",
            ]
        self.process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        threading.Thread(target=self.read, daemon=True).start()
        try:
            self.wait_for(lambda line: "received SUBACK" in line, 5)
        except Exception:
            self.close()
            raise

    def read(self):
        for line in self.process.stdout:
            self.lines.put(line.rstrip("\n"))

    def wait_for(self, predicate, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=deadline - time.monotonic())
            except queue.Empty:
                break
            if predicate(line):
                return True
        raise RuntimeError("Expected subscriber event was not observed")

    def received(self, payload, timeout=1):
        try:
            return self.wait_for(lambda line: line == payload, timeout)
        except RuntimeError:
            return False

    def close(self):
        stop(self.process)
        self.process.stdout.close()


def check_write(observer, user, topic, allowed, retain=False, client_id=None):
    marker = secrets.token_hex(16)
    result = publish(user, topic, marker, retain, client_id)
    if allowed:
        require(result.returncode == 0, "Allowed publisher failed")
    require(
        observer.received(marker) == allowed,
        f"Unexpected delivery: user={user}, topic={topic}",
    )
    # Prove that the observer and broker still work after every attempt.
    control = secrets.token_hex(16)
    result = publish("fridge-controller", "fridge-controller/status", control)
    require(result.returncode == 0, "Positive-control publication failed")
    require(observer.received(control), "Positive control was not delivered")

    if retain:
        fresh = Observer(topic=topic)
        try:
            require(
                fresh.received(marker) == allowed,
                f"Unexpected retained delivery: user={user}, topic={topic}",
            )
            control = secrets.token_hex(16)
            result = publish("fridge-controller", topic, control)
            require(result.returncode == 0, "Fresh-observer control failed")
            require(fresh.received(control), "Fresh observer was not healthy")
        finally:
            fresh.close()
        result = publish("fridge-controller", topic, "", retain=True)
        require(result.returncode == 0, "Retained test cleanup failed")


def run_tests():
    # An allowed controller publication establishes initial observer health.
    observer = Observer()
    try:
        for user in (None, "telegraf"):
            if user:
                original = PASSWORDS[user]
                PASSWORDS[user] = secrets.token_hex(32)
            try:
                result = publish(user, "fridge-controller/status", "auth-test")
                require(result.returncode != 0, "Invalid authentication succeeded")
            finally:
                if user:
                    PASSWORDS[user] = original
        print("PASS: anonymous and wrong-password rejection")

        # Every account must be able to receive telemetry.
        for user in USERS:
            reader = Observer(user)
            try:
                marker = secrets.token_hex(16)
                result = publish("fridge-sensor", "fridge-sensor/status", marker)
                require(result.returncode == 0, "Read-test publisher failed")
                require(reader.received(marker), f"Read failed for {user}")
            finally:
                reader.close()
        print("PASS: broad authenticated reads")

        routes = {
            "sensors/hydrom/Hydrom_2d699e/temperature": {"hydrom"},
            "sensors/hydrom/another-device/temperature": set(),
            "fridge-sensor/sensor/keg_wall_temperature/state": {"fridge-sensor"},
            "fridge-sensor/status": {"fridge-sensor"},
            "fridge-controller/status": {"fridge-controller"},
            "fridge-controller/online": {"fridge-controller"},
            "fridge-controller/rpc": {"fridge-controller", "splug"},
            "splug1/rpc": {"fridge-controller"},
            "splug1/online": {"splug"},
            "splug1/events/rpc": {"splug"},
            "splug1/status/switch:0": {"splug"},
            "splug1/command": set(),
            "splug1/command/switch:0": set(),
            "unrelated/status": set(),
        }
        for topic, writers in routes.items():
            for user in USERS:
                check_write(observer, user, topic, user in writers)
        print("PASS: allowed and denied publication matrix")

        for user in USERS:
            check_write(
                observer, user, "splug1/rpc",
                user == "fridge-controller", retain=True,
            )
            check_write(
                observer, user, "splug1/rpc",
                user == "fridge-controller", client_id="fridge-controller",
            )
        print("PASS: retained control writes and client-ID impersonation")

        # Routing simulation only; no physical device or controller is started.
        check_write(observer, "fridge-controller", "splug1/rpc", True)
        check_write(observer, "splug", "fridge-controller/rpc", True)
        print("PASS: simulated request/response routes")

        for user, topic in (
            ("fridge-sensor", "fridge-sensor/status"),
            ("fridge-controller", "fridge-controller/online"),
            ("splug", "splug1/online"),
        ):
            marker = secrets.token_hex(16)
            device = Observer(user, will=(topic, marker))
            try:
                device.process.kill()
                device.process.wait(timeout=5)
                require(
                    observer.received(marker, timeout=5),
                    f"Last Will was not delivered for {user}",
                )
                fresh = Observer(topic=topic)
                try:
                    require(
                        fresh.received(marker),
                        f"Retained Last Will was not delivered for {user}",
                    )
                finally:
                    fresh.close()
            finally:
                device.close()
                result = publish(user, topic, "", retain=True)
                require(result.returncode == 0, "Last Will cleanup failed")
        print("PASS: availability Last Wills and retained delivery")
    finally:
        observer.close()


def test_telegraf(directory):
    root = Path("/tmp/fridge-telemetry")
    binary = "/tmp/telegraf"
    version = subprocess.run(
        [binary, "--version"], capture_output=True, text=True, timeout=5,
    )
    require(
        version.returncode == 0
        and version.stdout.startswith("Telegraf 1.39.3 "),
        "Telegraf 1.39.3 is required",
    )
    decode = runpy.run_path(
        str(root / "tests/test_parsers.py")
    )["decode_metric"]
    metrics = directory / "metrics.lp"
    config = directory / "telegraf.conf"
    config.write_text(
        '[agent]\n  flush_interval = "1s"\n'
        + (root / "fridge.conf.example").read_text()
        + '\n[[outputs.file]]\n'
        + '  files = [' + json.dumps(str(metrics)) + ']\n'
        + '  data_format = "influx"\n'
    )
    empty = directory / "telegraf-empty"
    empty.mkdir()
    environment = os.environ.copy()
    environment.update(
        FRIDGE_MQTT_SERVER=f"tcp://127.0.0.1:{PORT}",
        FRIDGE_MQTT_USERNAME="telegraf",
        FRIDGE_MQTT_PASSWORD=PASSWORDS["telegraf"],
    )
    expected = []
    messages = []
    for metric, value in (
        ("keg_wall_temperature", 11.4),
        ("fridge_air_temperature", 8.2),
    ):
        messages.append((
            "fridge-sensor", f"fridge-sensor/sensor/{metric}/state", str(value),
        ))
        expected.append((
            "fridge_temperature",
            {"device": "fridge-sensor", "metric": metric,
             "influxdb_database": "fridge"},
            {"value": value},
        ))
    for fixture in ("normal.json", "unknown.json"):
        payload = json.loads((root / "tests/fixtures" / fixture).read_text())
        messages.append((
            "fridge-controller", "fridge-controller/status", json.dumps(payload),
        ))
        fields = {key: value for key, value in payload.items() if value is not None}
        for key in ("setpoint", "keg_sensor_age", "air_sensor_age"):
            if key in fields:
                fields[key] = float(fields[key])
        expected.append((
            "fridge_controller", {"device": "fridge-controller",
                                  "influxdb_database": "fridge"}, fields,
        ))

    log_path = directory / "telegraf.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [binary, "--config", str(config), "--config-directory", str(empty)],
            env=environment, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 10
            while log_path.read_text().count(
                f"Connected [tcp://127.0.0.1:{PORT}]"
            ) < 4:
                require(process.poll() is None, "Telegraf exited during startup")
                require(time.monotonic() < deadline, "Telegraf connection timed out")
                time.sleep(0.1)
            for user, topic, payload in messages:
                result = publish(user, topic, payload)
                require(result.returncode == 0, "Telemetry fixture publication failed")

            deadline = time.monotonic() + 10
            while True:
                require(process.poll() is None, "Telegraf exited during ingestion")
                records = []
                if metrics.exists():
                    text = metrics.read_text()
                    # Ignore a final line until its write is complete.
                    records = [
                        decode(line) for line in text.splitlines(keepends=True)
                        if line.endswith("\n") and line.startswith("fridge_")
                    ]
                if all(wanted in records for wanted in expected):
                    for name, tags, fields in expected:
                        matches = [
                            record for record in records
                            if record == (name, tags, fields)
                        ]
                        require(
                            any(
                                all(type(record[2][key]) is type(value)
                                    for key, value in fields.items())
                                for record in matches
                            ),
                            f"Unexpected field types in {name}",
                        )
                    break
                require(time.monotonic() < deadline, "Expected telemetry metrics missing")
                time.sleep(0.1)
            require("E!" not in log_path.read_text(), "Telegraf logged an error")
        finally:
            stop(process)
    print("PASS: authenticated Telegraf ingestion, field types, and unknown values")


def main():
    require(Path("/run/.containerenv").exists(), "Disposable Podman container required")
    require(
        {path.name for path in Path("/sys/class/net").iterdir()} == {"lo"},
        "Container must have only loopback networking; use --network none",
    )
    version = subprocess.run(
        ["mosquitto", "-h"], capture_output=True, text=True, timeout=5,
    )
    require(
        version.stdout.splitlines()[0] == "mosquitto version 2.0.21",
        "Mosquitto 2.0.21 is required",
    )
    require(ACL.is_file(), "Candidate ACL is missing")
    print("Testing Mosquitto 2.0.21 with generated container-only credentials")

    with tempfile.TemporaryDirectory(prefix="fridge-acl-") as temporary:
        directory = Path(temporary)
        directory.chmod(0o755)
        password_file = directory / "passwd"
        for index, user in enumerate(USERS):
            PASSWORDS[user] = secrets.token_hex(32)
            args = ["mosquitto_passwd", "-b"]
            if index == 0:
                args.append("-c")
            result = subprocess.run(
                args + [str(password_file), user, PASSWORDS[user]],
                capture_output=True, timeout=5,
            )
            require(result.returncode == 0, "Dummy account creation failed")
        # Contains only generated dummy password hashes, never production data.
        password_file.chmod(0o644)
        config = directory / "broker.conf"
        config.write_text(
            f"listener {PORT} 127.0.0.1\n"
            "user mosquitto\nallow_anonymous false\npersistence false\n"
            f"password_file {password_file}\nacl_file {ACL}\n"
        )
        with (directory / "broker.log").open("w") as log:
            broker = subprocess.Popen(
                ["mosquitto", "-c", str(config)],
                stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 5
                while True:
                    require(broker.poll() is None, "Test broker exited during startup")
                    try:
                        with socket.create_connection(("127.0.0.1", int(PORT)), 0.2):
                            break
                    except OSError:
                        require(time.monotonic() < deadline, "Test broker startup timed out")
                        time.sleep(0.05)
                run_tests()
                test_telegraf(directory)
            finally:
                stop(broker)
    print("All ACL checks passed; temporary broker and files removed")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(f"FAIL: {error}")
        raise SystemExit(1)
    except Exception as error:
        # Do not print subprocess arguments, which contain dummy passwords.
        print(f"FAIL: unexpected {type(error).__name__}; details withheld")
        raise SystemExit(1)
