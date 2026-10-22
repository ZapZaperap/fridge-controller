"""Create the agreed fridge database without altering existing retention."""

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--token-file", default="/etc/influxdb3/telegraf_token")
    args = parser.parse_args()
    path = Path(args.token_file)
    metadata = path.lstat()
    require(
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == 0
        and stat.S_IMODE(metadata.st_mode) == 0o600,
        "Token file must be a regular root-owned file with mode 0600",
    )
    token = path.read_text().strip()
    require(bool(token), "Token file is empty")
    environment = os.environ.copy()
    environment["INFLUXDB3_AUTH_TOKEN"] = token
    environment["INFLUXDB3_HOST_URL"] = "http://127.0.0.1:8181"

    def call(arguments):
        result = subprocess.run(
            ["influxdb3", *arguments],
            env=environment, capture_output=True, text=True, timeout=30,
        )
        require(
            result.returncode == 0,
            f"InfluxDB command failed with exit code {result.returncode}; details withheld",
        )
        return result.stdout

    def retention():
        return {
            row["database_name"]: row["retention_period"]
            for row in json.loads(call(["show", "retention", "--format", "json"]))
        }

    def verify(values):
        require(values.get("telegraf") == "infinite",
                "Expected existing telegraf database with infinite retention")
        if "fridge" in values:
            require(
                re.fullmatch(r"30(?:\.0+)?d", values["fridge"]) is not None,
                "Existing fridge retention differs from 30 days; no migration attempted",
            )

    before = retention()
    verify(before)
    changed = "fridge" not in before
    action = "unchanged"
    if changed:
        action = "would_create" if args.check else "created"
        if not args.check:
            call(["create", "database", "--retention-period", "30d", "fridge"])
            after = retention()
            require("fridge" in after, "Created database was not found")
            verify(after)
    print(json.dumps({
        "changed": changed, "action": action,
        "database": "fridge", "retention": "30d",
    }))


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        raise SystemExit(str(error))
    except Exception as error:
        raise SystemExit(f"Database provisioning failed: {type(error).__name__}; details withheld")
