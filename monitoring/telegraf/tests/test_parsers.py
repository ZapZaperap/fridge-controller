"""Exercise the proposed fragment with Telegraf, synthetic files, and stdout only."""

import argparse
import copy
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def decode_metric(line):
    # These synthetic fixtures intentionally have no commas/spaces in strings.
    series, values, timestamp = line.split(" ", 2)
    name, *tags = series.split(",")
    fields = {}
    for pair in values.split(","):
        key, value = pair.split("=", 1)
        if value.startswith('"'):
            fields[key] = json.loads(value)
        elif value in ("true", "false"):
            fields[key] = value == "true"
        elif value.endswith("i"):
            fields[key] = int(value[:-1])
        else:
            fields[key] = float(value)
    assert int(timestamp) > 0
    return name, dict(tag.split("=", 1) for tag in tags), fields


def run_case(binary, block, payload, expected):
    # Convert only transport settings; retain parser, tags, and field filters.
    block = re.sub(
        r"(?m)^[ \t]*(servers|username|password|topics|qos|topic_tag)[ \t]*=.*\n?",
        "", block,
    ).replace("inputs.mqtt_consumer", "inputs.file")
    with tempfile.TemporaryDirectory(prefix="fridge-parser-") as directory:
        directory = Path(directory)
        message = directory / "sample.txt"
        message.write_text(payload + "\n")
        config = directory / "test.conf"
        config.write_text(
            '[agent]\n  hostname = "parser-test-host"\n'
            '[[inputs.file]]\n  files = [' + json.dumps(str(message)) + ']\n'
            + block
            + '\n[[outputs.file]]\n  files = ["stdout"]\n  data_format = "influx"\n'
        )
        empty = directory / "empty"
        empty.mkdir()
        result = subprocess.run(
            [binary, "--config", str(config), "--config-directory", str(empty), "--once"],
            capture_output=True, text=True, timeout=30,
        )
        lines = [line for line in result.stdout.splitlines() if line.startswith("fridge_")]
        if expected is None:
            assert not lines, result.stdout
            assert result.returncode != 0 or "E!" in result.stderr, result.stderr
            return
        assert result.returncode == 0, result.stderr
        assert "E!" not in result.stderr, result.stderr
        if expected == ():
            assert not lines, result.stdout
            return
        assert len(lines) == 1, result.stdout
        name, tags, fields = decode_metric(lines[0])
        wanted_name, wanted_tags, wanted_fields = expected
        assert name == wanted_name, name
        assert tags == wanted_tags, tags
        assert fields == wanted_fields, fields
        for key, value in wanted_fields.items():
            assert type(fields[key]) is type(value), (key, fields[key])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--telegraf", default="telegraf")
    args = parser.parse_args()
    binary = shutil.which(args.telegraf)
    if not binary:
        parser.error("Telegraf is required; no packages will be installed by this test.")
    version = subprocess.check_output([binary, "--version"], text=True).strip()
    print(version)
    text = (ROOT / "fridge.conf.example").read_text()
    parsed = tomllib.loads(text)
    assert set(parsed) == {"inputs"} and set(parsed["inputs"]) == {"mqtt_consumer"}
    blocks = re.split(r"(?m)^\[\[inputs\.mqtt_consumer\]\][ \t]*\n", text)[1:]
    assert len(blocks) == 4
    cases = []
    for index, role, value in ((0, "keg_wall_temperature", 11.4),
                               (1, "fridge_air_temperature", -2.5)):
        cases.append((role, blocks[index], str(value),
                      ("fridge_temperature", {"device": "fridge-sensor", "metric": role,
                                              "influxdb_database": "fridge"},
                       {"value": value})))
    cases.append(("invalid temperature", blocks[0], "not-a-temperature", None))

    normal = json.loads((ROOT / "tests/fixtures/normal.json").read_text())
    # Force expected JSON numeric fields to their intended Telegraf types.
    normal["setpoint"] = float(normal["setpoint"])
    normal["air_sensor_age"] = float(normal["air_sensor_age"])
    unknown = json.loads((ROOT / "tests/fixtures/unknown.json").read_text())
    unknown["setpoint"] = float(unknown["setpoint"])
    states = [("normal ON", normal), ("unknown actual and ages", unknown)]
    off = copy.deepcopy(normal)
    off.update(output_requested=False, output_actual=False)
    states.append(("confirmed OFF", off))
    future = copy.deepcopy(normal)
    future["future_field"] = "ignored"
    states.append(("ignore unselected fields", future))
    for label, payload in states:
        expected = {key: value for key, value in payload.items()
                    if value is not None and key != "future_field"}
        cases.append((label, blocks[2], json.dumps(payload),
                      ("fridge_controller", {"device": "fridge-controller",
                                             "influxdb_database": "fridge"}, expected)))
    cases.append(("malformed JSON", blocks[2], "{", None))
    missing = copy.deepcopy(normal)
    del missing["mode"]
    cases.append(("missing required mode", blocks[2], json.dumps(missing), None))

    def shelly_payload(component):
        return json.dumps({
            "src": "shellyplugmg3-70af09f9a6c4",
            "dst": "splug1/events",
            "method": "NotifyStatus",
            "params": {"ts": 1792312620.03, "switch:0": component},
        })

    shelly_tags = {
        "device": "splug1", "channel": "0",
        "influxdb_database": "fridge",
    }
    for label, component, fields in [
        ("electrical readings",
         {"apower": 85, "current": 0.4, "voltage": 232.3,
          "aenergy": {"total": 135.713}, "freq": 50.01},
         {"power_w": 85.0, "current_a": 0.4,
          "voltage_v": 232.3, "energy_wh": 135.713}),
        ("electrical zero readings",
         {"apower": 0, "current": 0},
         {"power_w": 0.0, "current_a": 0.0}),
        ("partial voltage update",
         {"voltage": 231.8}, {"voltage_v": 231.8}),
        ("energy counter reset",
         {"aenergy": {"total": 0}}, {"energy_wh": 0.0}),
    ]:
        cases.append((label, blocks[3], shelly_payload(component),
                      ("fridge_shelly", shelly_tags, fields)))
    cases.append(("ignore lifecycle counters", blocks[3],
                  shelly_payload({"counts": {"switch_on": 82}}), ()))
    cases.append(("ignore unrelated notification", blocks[3],
                  json.dumps({"method": "NotifyEvent", "params": {}}), ()))
    cases.append(("malformed Shelly JSON", blocks[3], "{", None))
    cases.append(("invalid electrical reading", blocks[3],
                  shelly_payload({"apower": "invalid"}), None))
    for label, block, payload, expected in cases:
        run_case(binary, block, payload, expected)
        print("PASS:", label)
    print(f"Passed {len(cases)} offline parser cases; no MQTT or database connections.")


if __name__ == "__main__":
    main()
