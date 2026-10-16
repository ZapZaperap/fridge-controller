# System Architecture

## Overview

This document describes the collected source and configuration files, which the project owner has confirmed are identical to those used in production. Physical hardware details and operational behavior have not been independently verified. Values below are the current repository settings.

The fridge controller consists of an ESP32 measurement device, Mosquitto, a Python controller on a Raspberry Pi, and a Shelly switch controlling the fridge.

```text
DS18B20 sensors
      |
      v
ESP32 / ESPHome
      |
      | MQTT
      v
Mosquitto broker
      |
      | MQTT
      v
Python controller (Raspberry Pi)
      |
      | MQTT RPC through Mosquitto (commands and responses)
      v
Shelly switch, channel 0
      |
      v
Fridge
```

## Components

### ESP32

- Source: `esphome/fridge-sensor/fridge-sensor.yaml`.
- Node: `fridge-sensor`; configured board: `esp32dev`, using the Arduino framework.
- Keg-wall DS18B20: OneWire bus `bus_keg` on GPIO18.
- Fridge-air DS18B20: OneWire bus `bus_air` on GPIO16.
- Both sensors update every 10 seconds; sensor addresses are not explicitly assigned.
- MQTT broker: `192.168.178.29`; credentials use secret references.
- Wi-Fi power saving is disabled; fallback AP and captive portal are configured.
- OTA and encrypted native API access use secret references.
- MQTT logging is disabled with `log_topic: null`; `idf_send_async` is enabled.
- Both MQTT and API sections specify `reboot_timeout: 15min`.

The earlier GPIO4 description does not match this YAML. The physical module, sensor identities, pull-up resistors, and wiring require confirmation. Sensor MQTT topics are not explicitly assigned in the YAML; the controller's expected topics are listed below.

### Raspberry Pi

- Operating system: Raspberry Pi OS
- Application: Python temperature controller
- Service management: systemd
- Communication: MQTT through Mosquitto

The controller connects to `localhost:1883`. Together with the ESPHome broker address, this suggests Mosquitto runs on the Raspberry Pi, but broker location and configuration are not established by these files.

### Controller settings and modes

Sources: `controller/controller.py` and `controller/config.yaml`.

| Setting | Current value |
|---|---|
| Keg / fallback air setpoint | 11°C / 11°C |
| ON / OFF hysteresis offsets, both modes | +0.3°C / −0.3°C |
| Minimum OFF / ON time | 300 s / 120 s |
| Sensor freshness limit | 60 s |
| Air safety cutoff | At or below 4°C |
| Accepted temperature range | −20°C through 50°C, inclusive |
| Startup minimum OFF enforcement | Disabled |
| Main loop / configuration check | 1 s / 2 s |
| Status publication interval | 10 s, unless status changes sooner |
| Shelly status poll | 15 s; successful switch commands also request status |
| RPC timeout / switch retry delay | 5 s / 5 s |
| Shelly automatic OFF timer / ON refresh | 60 s / 20 s |

Payloads are parsed as floating-point temperatures. Invalid and out-of-range readings do not refresh sensor age. Age uses monotonic time since receipt of the last accepted message; readings are fresh through 60 seconds.

| Mode | Selection and behavior |
|---|---|
| `STARTUP` | Startup OFF enforcement enabled and OFF not confirmed or minimum wait incomplete; request OFF |
| `NORMAL` | Both sensors fresh; regulate using keg temperature |
| `DEGRADED_KEG_ONLY` | Only keg fresh; regulate using keg temperature without air cutoff |
| `DEGRADED_AIR` | Only air fresh; regulate using fallback air settings |
| `FAULT` | Neither sensor fresh; request OFF |

Startup takes precedence over freshness-based modes. Otherwise, fresh air at or below 4°C requests OFF before regulation.

Cooling turns ON strictly above setpoint plus the ON offset and OFF strictly below setpoint minus the OFF offset. At either threshold and between them, the actual relay state is retained. Unknown actual state is treated as OFF for this calculation.

Normal transitions enforce cycle timers from observed relay state changes. Startup, all-sensor failure, and air cutoff decisions override minimum ON time.

Every MQTT connection requests OFF and actuator status. With startup enforcement disabled, the first ON may bypass minimum OFF time only after OFF confirmation. Once ON has been observed, subsequent starts require the normal OFF wait. With enforcement enabled, the startup wait begins at confirmed OFF. Relay history is in memory and is not persisted across restarts.

### MQTT contract

The controller uses client ID `fridge-controller`, a clean session, username/password authentication, and a 30-second keepalive. It does not configure TLS.

| Topic | Direction and payload |
|---|---|
| `fridge-sensor/sensor/keg_wall_temperature/state` | Input: numeric temperature text |
| `fridge-sensor/sensor/fridge_air_temperature/state` | Input: numeric temperature text |
| `splug1/rpc` | Output: JSON Shelly RPC requests |
| `fridge-controller/rpc` | Input: JSON RPC responses |
| `fridge-controller/online` | Retained `true` or `false`; false is also the last will |
| `fridge-controller/status` | Retained JSON status |
| `fridge-controller/mode` | Retained mode text |
| `fridge-controller/reason` | Retained decision reason |
| `fridge-controller/setpoint` | Retained numeric setpoint text |
| `fridge-controller/output_requested` | Retained `on` or `off` |
| `fridge-controller/output_actual` | Retained `on`, `off`, or `unknown` |

Controller subscriptions and publications use QoS 1. RPC commands are not retained. Sensor publisher QoS and retention are not explicitly set in the YAML.

Requests contain integer `id`, fixed `src: fridge-controller`, and `method`. `Switch.Set` supplies `params` with switch `id: 0`, boolean `on`, and `toggle_after: 60` for ON. `Shelly.GetStatus` requests status. Responses are matched to pending IDs; status polls read `result["switch:0"]["output"]`.

Status JSON fields are `mode`, `reason`, `setpoint`, `output_requested`, `output_actual`, `keg_sensor_age`, `air_sensor_age`, `air_safety_active`, `mqtt_connected`, and `shelly_command_failures`. Unknown output and missing ages are null; temperatures are not included.

Changed status publishes immediately. Sensor ages usually change each loop, so publishing can occur approximately every second despite the ten-second interval.

### Configuration and reload

The first command-line argument selects the YAML configuration, defaulting to `config.yaml`. The service explicitly supplies `/home/josias/fridge-controller/config.yaml`. The configured external password file is `/home/josias/fridge-controller/mqtt-password`.

Modification time is checked every two seconds. Rejected reloads keep the previous configuration; initial configuration errors stop startup. Validation is partial and does not check every runtime field or cross-field timing relationship.

Control settings use reloaded configuration. MQTT setup is not rebuilt: broker connection, credentials, client identity, and last will are not recreated, and existing subscriptions are not replaced. MQTT changes require a restart rather than assuming full live reload.

### systemd and installation layout

`systemd/fridge-controller.service` runs as user and group `josias`, with working directory `/home/josias/fridge-controller`. It invokes the virtual-environment Python with `controller.py` and the explicit configuration path `/home/josias/fridge-controller/config.yaml`.

It uses `Type=simple`, unbuffered output, `Restart=on-failure`, a five-second restart delay, a ten-second stop timeout, and `KillSignal=SIGTERM`. It wants `network-online.target`, requires `mosquitto.service`, and orders itself after both. The install target is `multi-user.target`.

Ansible copies application and configuration files into the service's flat installation layout and manages the virtual environment. The existing password file is preserved; provisioning secrets for replacement hardware remains deferred.

## Reliability and failure handling

The project has previously experienced intermittent loss of temperature sensor updates.

Successful `Switch.Set` responses are treated as confirmation of the requested relay state; status polls verify it. This does not independently verify compressor operation.

Every ON command requests a 60-second automatic OFF timer, refreshed approximately every 20 seconds while cooling is requested. Outage protection depends on the installed Shelly accepting and resetting the timer.

Only one `Switch.Set` may be pending. It can defer another command, including safety OFF, until it resolves or expires. RPC errors and timeouts increment a failure counter; successful switch responses reset it. This counter does not itself force a fault mode.

MQTT disconnection marks the connection flag false but does not immediately clear remembered relay state or force `FAULT`. Retained sensor readings can appear fresh on receipt because payloads have no sample timestamp.

SIGTERM and SIGINT initiate shutdown: if connected, request OFF, allow up to three seconds for pending RPCs, publish offline, and disconnect. OFF is best effort and can be deferred by a pending switch command.

### Diagnostic watcher

`controller/watch-fridge-sensors.py` launches `mosquitto_sub -v` on the two sensor topics without explicit broker or credential arguments. It reports transitions among `OK`, `KEG_STALE`, `AIR_STALE`, and `BOTH_STALE`.

Its stale threshold is 30 seconds, compared with the controller's 60 seconds. Any received payload refreshes age without temperature validation. Both last-seen times start at process startup; the initial state is not printed. It stops when the subscriber exits. The supplied service does not run it.

## Deployment

The original installation was performed manually.

Ansible deployment has been implemented and exercised on production. It preserves service runtime state and boot enablement. Production remained inactive and disabled, and the second deployment reported changed=0. See the [production runbook](production-deployment.md).

### Deferred broker improvements

- Plan and test MQTT topic authorization using Mosquitto ACLs while retaining password-based authentication. Account for all applications using the shared broker before enabling restrictions.
- Evaluate an `include_dir` for modular broker configuration. Determine which fridge-specific settings can be separated without changing behavior for other applications.

These improvements are outside the initial Ansible migration. The initial deployment will preserve the shared broker configuration and password file. Broker changes and restarts require separate review and authorization.

## Unresolved questions and operational documentation

Production deployment and metadata verification were performed by the project owner using the reviewed procedure. The controller was left stopped; firmware flashing and physical actuator testing were not performed.

- What are the exact ESP32 module, sensor identities, physical wiring, and pull-up resistors?
- What are the Shelly model, firmware, MQTT settings, response routing, and power-on relay behavior?
- Do repeated ON requests reset its automatic OFF timer as intended?
- Where is Mosquitto hosted, and what are its authentication, persistence, and retention settings?
- How are repository files installed into the service layout and secrets provisioned?
- Is bypassing the first startup OFF wait intentional, and is keg-only operation without an air cutoff acceptable?
- Is the native ESPHome API used, and are both reboot timeouts intentional?
- What are the established installation, compilation, flashing, update, recovery, and outage-verification procedures?

Keep actual secrets outside the repository. The ESPHome secrets file and external MQTT password file were not inspected for this documentation.
