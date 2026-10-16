# Fridge Controller

A temperature monitoring and control system based on an ESP32, DS18B20 temperature sensors, MQTT, and a Raspberry Pi.

## Components

- **ESP32:** Runs ESPHome firmware and publishes temperature measurements through MQTT.
- **Raspberry Pi:** Runs a Python application that processes sensor measurements and implements the controller.
- **Mosquitto:** MQTT broker connecting the components.
- **Shelly:** Switches the fridge through MQTT RPC, with a controller-requested automatic OFF timer.
- **systemd:** Manages the Python application as a background service.
- **Diagnostic watcher:** An optional script reports sensor update outages without controlling the fridge.

## Repository structure

- `controller/` — Python controller application
- `esphome/` — ESP32 firmware configuration
- `systemd/` — Service definitions
- `ansible/` — Controller deployment automation
- `ansible/tests/` — Disposable-container deployment tests
- `docs/` — Architecture and operational documentation
- `tests/` — Automated tests (planned)

## Existing behavior

ESPHome samples keg-wall and fridge-air temperatures every 10 seconds. The controller normally regulates keg-wall temperature around 11°C: cooling turns ON above 11.3°C and OFF below 10.7°C, retaining the current relay state between those thresholds.

If only the air sensor is fresh, the controller uses air temperature with the same configured thresholds. If only the keg sensor is fresh, keg regulation continues without the air safety cutoff. If neither sensor is fresh, it requests OFF. Fresh air readings at or below 4°C also request OFF.

Normal cycling uses a 300-second minimum OFF time and a 120-second minimum ON time. Safety OFF decisions override the minimum ON time. The current startup setting permits the first ON to bypass the OFF wait after OFF confirmation.

Every ON command requests a 60-second Shelly automatic OFF timer; the controller attempts to refresh it every 20 seconds while cooling is requested. Actual Shelly timer behavior still requires verification.

See [docs/architecture.md](docs/architecture.md) for exact boundaries, MQTT topics, and failure-handling limits.

## Runtime requirements and installation layout

The controller requires Python with `paho-mqtt>=2.1,<3` and `PyYAML>=6,<7`, as specified in `controller/requirements.txt`. The optional `controller/watch-fridge-sensors.py` also requires the external `mosquitto_sub` command.

The controller accepts a configuration path as its first argument, defaulting to `config.yaml` in the working directory. The service explicitly supplies `/home/josias/fridge-controller/config.yaml`; that configuration uses `/home/josias/fridge-controller/mqtt-password`. Keep actual secrets outside the repository.

The supplied service expects `controller.py`, `config.yaml`, `mqtt-password`, and `.venv/bin/python` under `/home/josias/fridge-controller`. Ansible installs the repository sources into this flat layout and preserves the existing password file. See [ansible/README.md](ansible/README.md) and the [production runbook](docs/production-deployment.md).

## Project status

The system was originally configured and developed directly on the devices.

The initial Ansible migration is complete. Deployment was verified on the production Pi, and an unchanged second run reported `changed=0`.

The files collected from the running system are the starting point for this migration.

Deployment is performed by explicitly running Ansible. Repository edits alone do not affect production. Deployment preserves the service's running/stopped and enabled/disabled states.

The configuration and unit were reconciled with production during deployment review. The production controller remains inactive and disabled. Firmware flashing, controller runtime verification, and physical actuator testing remain outside this migration.

## Development

Development takes place on Arch Linux. The production Pi runs Debian 13 (trixie), ARM64.

See `docs/architecture.md` for technical details and `AGENTS.md` for instructions to AI development agents.
