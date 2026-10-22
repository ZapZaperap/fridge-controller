# Fridge telemetry ingestion

Status: deployed for production temperature and Shelly electrical ingestion.
Both temperatures and electrical readings were verified in Grafana Explore;
fermentation ingestion continued.
Controller-status ingestion is configured but remains unverified in production
while the controller is intentionally inactive.

## Discovered stack

The owner reported Telegraf 1.39.3, InfluxDB 3 Core 3.11.2, and Grafana 13.2.0.
At discovery, Telegraf loaded /etc/telegraf/telegraf.conf and an empty
telegraf.d directory. Deployment added telegraf.d/fridge.conf.
Existing fermentation ingestion uses sensors/hydrom/#, float values, table
fermentation, device/metric tags, and an input-local batch tag. It has file and
InfluxDB v3 outputs, with database telegraf and no configured output filters.
Grafana's InfluxDB source uses SQL and http://127.0.0.1:8181.

## Deployed fragment

fridge.conf.example adds four MQTT inputs. It defines no outputs, global
tags, or agent settings. The fermentation input keeps its existing destination.
Each fridge input omits automatic host/topic tags and uses stable identities.

The owner selected a separate fridge database with 30-day retention. The
existing telegraf database intentionally keeps fermentation data indefinitely.
The role adds database_tag = "influxdb_database" and exclude_database_tag = true
to the existing InfluxDB v3 output, preserving database = "telegraf" as its
fallback. Fridge and Shelly inputs carry influxdb_database = "fridge".
It is removed before InfluxDB storage
but remains visible in the debug file and file-based parser tests.

Before production deployment, create and verify the fridge database with 30-day
retention and confirm the existing output token can write to it. Core retention
is selected at database creation and cannot be changed in place. Database
creation and production routing changes were subsequently completed on
2026-10-22. The actual output token passed a write test to fridge.
Two-database routing passed in isolation on 2026-10-22: fermentation stayed
in infinite-retention telegraf, fridge measurements went to 30-day fridge,
the routing tag was removed, and SQL NULL handling was preserved.
The unchanged second role run reported changed=0 and preserved the service PID.
See the telemetry runbook for evidence and limits. The older test results below
predate routing. A separate Grafana datasource for fridge was configured
and used to verify both temperatures.

| Table | Tags | Fields |
|---|---|---|
| fridge_temperature | device=fridge-sensor; metric=keg_wall_temperature or fridge_air_temperature | value: float, degrees Celsius |
| fridge_controller | device=fridge-controller | mode/reason: string; setpoint/ages: float; requested/actual output and safety/connection flags: bool; command failures: int |
| fridge_shelly | device=splug1; channel=0 | power_w: float, W; current_a: float, A; voltage_v: float, V; energy_wh: float, cumulative Wh |

Temperatures come from their original raw topics. Controller fields come only
from fridge-controller/status. Nullable actual output and sensor ages are omitted
from a point rather than converted to OFF or zero. SQL/Grafana must preserve that
unknown state and must not fill it forward automatically. Before the first
non-null value, an omitted field may not yet exist as an InfluxDB column.
Time is receipt/ingestion time, not the original sample time; reconnecting with
retained MQTT messages must not be presented as fresh sensor measurements.

Provision FRIDGE_MQTT_SERVER, FRIDGE_MQTT_USERNAME, and FRIDGE_MQTT_PASSWORD outside
Git only after reviewing the existing environment and MQTT account permissions.
Use a read-only MQTT identity and validate the server URL and transport security.
No credential values are provided here. Default random client IDs avoid clashes
between the four inputs; persistent sessions and replay policy require review.

## Shelly electrical input — deployed and verified

The electrical input subscribes only to splug1/events/rpc and maps
params.switch:0.apower, current, voltage, and aenergy.total to power_w,
current_a, voltage_v, and energy_wh in the fridge_shelly measurement.
Units are watts, amperes, volts, and cumulative watt-hours respectively.
Stable tags are device=splug1 and channel=0; the existing routing tag sends
these measurements to fridge with 30-day retention.

The owner observed electrical updates approximately once per minute.
No additional downsampling is applied. Only events/rpc is consumed,
avoiding duplicate ingestion from component status topics. Missing readings
remain absent; consumers must not carry old values forward as fresh readings.
Receipt time is used consistently with the existing inputs.

Energy is a cumulative counter, not per-message consumption. Calculations
must account for counter decreases instead of reporting negative consumption.
Reboot persistence and actual device reset behavior remain unverified.
No polling or control RPCs are sent. All 17 offline parser cases passed,
and isolated authenticated MQTT-to-InfluxDB checks verified values, field types,
NULLs, and database routing. Production readings were verified in Grafana Explore;
repeat deployment reported changed=0, and existing ingestion continued.
See [the telemetry runbook](../../ansible/telemetry-runbook.md#subsequent-shelly-electrical-rollout)
for the rollout record and test limitations. Grafana panels are deferred.

## Offline parser checks

Requires Python 3.11+ and Telegraf; validate with the installed 1.39.3 binary.
Run on a machine holding a copy of this directory:

```bash
python3 monitoring/telegraf/tests/test_parsers.py
```

The script generates temporary file inputs from the proposed fragment, retaining
parser options, field types, and tags. It replaces MQTT transport with synthetic
files and adds only a stdout output. It explicitly supplies isolated configuration
paths, so it does not load /etc/telegraf, subscribe to MQTT, or write to InfluxDB.
It removes its temporary files on exit and installs no packages.

Checks cover both sensor mappings, invalid numeric input, normal ON, confirmed
OFF, nullable output/ages, unknown extra fields, malformed JSON, and a missing
required field. The line-protocol decoder handles these simple fixtures only.
These are parser tests, not end-to-end MQTT routing, authentication, retained
message, database, or Grafana tests.

## Verified results — 2026-10-16

All nine offline parser cases passed with Telegraf 1.39.3.
The owner also verified MQTT -> Telegraf -> InfluxDB 3 Core 3.11.2
using synthetic messages in disposable rootless Podman containers.

The broker container, fridge-telemetry-test, used --network none.
The database container, fridge-influx-test, shared that network namespace,
listened on 127.0.0.1:8181, and stored data inside its container.
Neither container published host ports or mounted host directories.
The test broker allowed anonymous connections and the database used
--without-auth. These tests do not verify production authentication.

The database fridge_test contained:

- Both temperature metrics with the expected device/metric tags.
- NORMAL and FAULT controller rows with the expected values.
- SQL NULL for unknown actual output and sensor ages.
- No automatic host or topic columns.

Verified SQL types:

| Columns | Type |
|---|---|
| device, metric (where applicable) | Dictionary(Int32, Utf8) |
| mode, reason | Utf8 |
| value, setpoint, keg_sensor_age, air_sensor_age | Float64 |
| output_requested, output_actual, air_safety_active, mqtt_connected | Boolean |
| shelly_command_failures | Int64 |
| time | Timestamp(ns), non-nullable |

All other columns were nullable. Only the listed synthetic samples were
tested; reconnects, retained messages, outages, authentication, Grafana,
and long-term persistence across container recreation remain unverified.

### Repeat the ingestion check

These commands require the prepared, running test containers described
above, with the proposal and fixtures under /tmp/fridge-telemetry.
The database-writing Telegraf process must already be subscribed.
They append synthetic samples to fridge_test only.

```bash
podman exec fridge-telemetry-test mosquitto_pub -h 127.0.0.1 -q 1 -t fridge-sensor/sensor/keg_wall_temperature/state -m '11.4'
podman exec fridge-telemetry-test mosquitto_pub -h 127.0.0.1 -q 1 -t fridge-sensor/sensor/fridge_air_temperature/state -m '8.2'
podman exec fridge-telemetry-test mosquitto_pub -h 127.0.0.1 -q 1 -t fridge-controller/status -f /tmp/fridge-telemetry/tests/fixtures/normal.json
podman exec fridge-telemetry-test mosquitto_pub -h 127.0.0.1 -q 1 -t fridge-controller/status -f /tmp/fridge-telemetry/tests/fixtures/unknown.json
```

Allow several seconds for flushing before querying:

```bash
podman exec fridge-influx-test influxdb3 query --database fridge_test 'SELECT * FROM fridge_temperature ORDER BY time'
podman exec fridge-influx-test influxdb3 query --database fridge_test 'SELECT * FROM fridge_controller ORDER BY time'
podman exec fridge-influx-test influxdb3 query --database fridge_test "SELECT table_name, column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name IN ('fridge_temperature', 'fridge_controller') ORDER BY table_name, ordinal_position"
```

Each repetition adds two temperature rows and two controller rows.
Check their values and types against the results above.

## Ansible role verification — 2026-10-22

Isolated tests verified deployment alongside synthetic fermentation ingestion,
an unchanged main configuration, idempotence, stopped-state preservation,
configuration-failure detection, and rollback recovery.
See [the telemetry runbook](../../ansible/telemetry-runbook.md) for evidence
and limits, including the subsequent production verification record.

## Before any rollout

Review the fragment and parser results first. Confirm read-only account access,
existing environment variable names, file-output destination/rotation, status
ingestion volume, and retention. Test MQTT routing and stored field types in an
isolated environment. Integrate through Ansible with validation and rollback.
Production access, installing the fragment under /etc, environment changes, and
Telegraf restarts require explicit approval. Online health ingestion and
notifications remain separate follow-up work; Grafana panels are deferred
to the backlog.
Broker ACL enforcement is already deployed. The Python controller has no
database dependency.
