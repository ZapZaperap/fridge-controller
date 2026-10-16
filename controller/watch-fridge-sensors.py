#!/usr/bin/env python3

import subprocess
import time
import select

STALE_AFTER = 30.0

TOPICS = {
    "keg": "fridge-sensor/sensor/keg_wall_temperature/state",
    "air": "fridge-sensor/sensor/fridge_air_temperature/state",
}

cmd = ["mosquitto_sub", "-v"]
for topic in TOPICS.values():
    cmd += ["-t", topic]

p = subprocess.Popen(
    cmd,
    stdout=subprocess.PIPE,
    text=True,
    bufsize=1,
)

last_seen = {
    "keg": time.monotonic(),
    "air": time.monotonic(),
}

last_temp = {
    "keg": None,
    "air": None,
}

previous_state = None


def current_state(now):
    keg_stale = now - last_seen["keg"] >= STALE_AFTER
    air_stale = now - last_seen["air"] >= STALE_AFTER

    if keg_stale and air_stale:
        return "BOTH_STALE"
    elif keg_stale:
        return "KEG_STALE"
    elif air_stale:
        return "AIR_STALE"
    else:
        return "OK"


def print_state_change(state, now):
    keg_age = now - last_seen["keg"]
    air_age = now - last_seen["air"]

    timestamp = time.strftime("%F %T")

    if state == "OK":
        print(
            f"{timestamp} RECOVERED "
            f"(keg age {keg_age:.0f}s, air age {air_age:.0f}s)"
        )

    elif state == "KEG_STALE":
        print(
            f"{timestamp} KEG STALE "
            f"(keg age {keg_age:.0f}s, air age {air_age:.0f}s)"
        )

    elif state == "AIR_STALE":
        print(
            f"{timestamp} AIR STALE "
            f"(keg age {keg_age:.0f}s, air age {air_age:.0f}s)"
        )

    elif state == "BOTH_STALE":
        print(
            f"{timestamp} BOTH STALE "
            f"(keg age {keg_age:.0f}s, air age {air_age:.0f}s)"
        )


while True:
    ready, _, _ = select.select([p.stdout], [], [], 1.0)

    if ready:
        line = p.stdout.readline()

        if not line:
            print("mosquitto_sub exited")
            break

        topic, value = line.strip().split(maxsplit=1)

        now = time.monotonic()

        if topic == TOPICS["keg"]:
            last_seen["keg"] = now
            last_temp["keg"] = value

        elif topic == TOPICS["air"]:
            last_seen["air"] = now
            last_temp["air"] = value

    now = time.monotonic()
    state = current_state(now)

    if state != previous_state:
        if previous_state is not None:
            print_state_change(state, now)

        previous_state = state
