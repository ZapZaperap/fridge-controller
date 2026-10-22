# TODO roadmap

These are planned tasks, not authorization to access production, change /etc,
deploy, restart services, or operate the fridge. First inspect repository
configuration; inspect the running system only after separate approval.
Review proposed changes and validation/rollback procedures before deployment.
Keep passwords, tokens, and private keys outside Git.

## Inspection baseline

- Initial inspection found no monitoring configuration in the repository.
  Subsequent owner-provided discovery confirmed running Telegraf 1.39.3,
  InfluxDB 3 Core 3.11.2, and Grafana 13.2.0, with working fermentation
  ingestion and a SQL Grafana datasource using database telegraf.
- The repository now contains the deployed fridge ingestion fragment.
  Nine offline parser cases and isolated MQTT-to-InfluxDB sample ingestion
  passed. Field types and preservation of unknown values as SQL NULL were
  verified. See ../monitoring/telegraf/README.md for evidence and limitations.
  Production temperature ingestion was deployed and verified on 2026-10-22.
  Both temperatures appeared in Grafana Explore, fermentation ingestion
  continued, and repeat deployment reported changed=0. Controller-status
  ingestion remains unverified while the controller is intentionally inactive.
- Mosquitto 2.0.21 configuration and five account identities were reported
  by the owner. A repository ACL candidate passed isolated authorization,
  retained-message, client-ID impersonation, Last Will, and authenticated
  Telegraf ingestion checks. See ../mosquitto/README.md for limits.
- Broker Ansible tests passed for reload enforcement, idempotence,
  stopped-state preservation, and rollback in a disposable systemd container.
  See ../ansible/broker-runbook.md.
- Telegraf role checks passed in isolation for deployment alongside synthetic
  fermentation ingestion, idempotence, stopped-state preservation,
  configuration-failure detection, and rollback recovery.
  See ../ansible/telemetry-runbook.md for limits.
- Retention decision: preserve fermentation in telegraf indefinitely; use a
  separate fridge database with 30-day retention for fridge and Shelly
  telemetry. Two-database routing, tag removal, NULL handling, retention
  configuration, and role idempotence passed in isolation on 2026-10-22.
  Production database provisioning, actual output-token write access, and
  routing deployment were subsequently verified. Database provisioning was
  idempotent; production routing-tag removal and cross-database isolation
  checks remain pending.
- Production telemetry credentials were provisioned outside Git. ntfy
  discovery, storage-capacity review, remaining device topic requirements,
  and full controller-status verification remain pending.
- Shelly power, current, voltage, and cumulative energy ingestion is deployed
  from splug1/events/rpc into fridge_shelly. All 17 parser cases passed;
  isolated authenticated ingestion verified values, types, NULLs, and routing.
  The owner confirmed production readings and continuing fermentation and
  temperature ingestion. Repeat deployment reported changed=0. Actual energy
  reset/reboot behavior remains unverified; Grafana panels are deferred.
- Production ACL deployment was completed on 2026-10-22. Existing telemetry
  continued, the second run reported changed=0, and broker/controller service
  states were preserved. Controller startup and actuator testing remain pending.
- ansible/tests/prepare.yml configures only a disposable test broker.
  It is not evidence of the shared production broker's effective configuration.
- Production Mosquitto retains listener 1883, allow_anonymous false, and
  password_file /etc/mosquitto/passwd. Deployment added
  acl_file /etc/mosquitto/fridge.acl without include_dir restructuring.
- The production controller was left inactive and disabled after deployment.
  Preserve intentional downtime when designing health alerts.
- The controller publishes retained fridge-controller/status JSON and an
  online topic with a retained false last will. It publishes scalar state
  topics too. Status contains no temperatures or electrical measurements.
- Requests go to splug1/rpc with src fridge-controller. Responses are consumed
  from fridge-controller/rpc. GetStatus currently extracts switch:0.output;
  it does not republish power, current, voltage, or energy.

## Implementation order

1. Discover the existing monitoring stack, complete broker/client configuration,
   Shelly MQTT capabilities, and externally provisioned credentials.
2. Agree on the data model and authenticated-client/topic permission matrix.
   Test ACLs and client compatibility in an isolated broker before enforcement.
3. Introduce the reviewed ACL policy without locking out shared-broker clients.
   Account for future read-only Telegraf and monitoring identities.
4. Configure Telegraf ingestion into the existing InfluxDB layout and verify
   stored data. Grafana panels are explicitly deferred to the backlog.
5. Add loosely coupled notifications after confirming available health signals.
   Database storage is not a prerequisite for MQTT-based notifications.

Each production rollout remains a separate review/approval step. Keep changes
incremental and reproducible through Ansible without changing control behavior.

## 1. Telegraf / InfluxDB / Grafana integration

### Rationale and intended approach

- [ ] Persist useful history and controller state for diagnosis and Grafana.
  Telegraf subscribes directly to MQTT and writes to InfluxDB. The Python
  controller must neither write to nor query InfluxDB.
- [ ] Inspect existing Telegraf inputs/processors/outputs, InfluxDB version,
  database or bucket organization, retention, timestamp conventions, and
  Grafana data sources before choosing measurement names, tags, or fields.
  Obtain approved, redacted configuration; do not read credential contents.
- [x] Use the raw numeric topics as the original temperature sources:
  fridge-sensor/sensor/keg_wall_temperature/state and
  fridge-sensor/sensor/fridge_air_temperature/state.
- [ ] Prefer fridge-controller/status JSON for mode, reason, setpoint,
  output_requested, and output_actual where the installed Telegraf parser
  makes this cleaner than subscribing to duplicate scalar topics. Preserve
  booleans, numeric fields, and strings deliberately; actual output can be null.
- [ ] Assess sensor ages, air_safety_active, shelly_command_failures, and
  fridge-controller/online as additional useful diagnostic signals.
- [ ] Discover Shelly Plug M Gen3 firmware, MQTT settings, actual telemetry
  topics, sample payloads, units, reporting rates, and energy counter/reset
  semantics. Ingest power, current, voltage, and energy where available.
  Do not invent topics or require Telegraf to send control RPCs.
  The four electrical fields are deployed and verified; actual device counter
  behavior across resets and reboots remains to be established.
- [x] Use bounded, stable device/sensor identity tags as appropriate to the
  existing schema. Prefer fields for reason, mode, changing state, readings,
  counters, and timestamps. Do not turn arbitrary payloads, RPC IDs, or
  per-message values into tags.

### Acceptance criteria

- [ ] Authenticated read-only MQTT ingestion persists both temperatures,
  controller mode/reason/setpoint, and requested/actual output. Available
  electrical telemetry is stored; unavailable values are explicitly documented.
- [ ] Verify sample messages through parsing, stored field types, and queries.
  Unknown actual output remains distinguishable from OFF; invalid samples do
  not become plausible temperatures. Check units and avoid duplicate ingestion.
- [ ] Reconnects, retained messages, timestamps, and ingestion gaps are tested.
  Receipt of retained data is not proof of a newly measured sample. Determine
  buffering/retention and volume requirements; status can publish about once
  per second because sensor ages change, despite its configured 10 s interval.
- [ ] Grafana can display both temperatures and setpoint over time,
  reported relay ON/OFF state, requested versus actual output, modes/faults/
  degraded periods, power consumption, and available energy history.
  Reported relay state must not be presented as independent compressor proof.
- [ ] InfluxDB, Telegraf, or Grafana failure does not interfere with control.

### Deferred backlog: Grafana panels

- [ ] Build Grafana panels when the owner resumes this work.
  Show both temperatures, power, and cumulative energy first.
  Add setpoint, requested/actual output, and mode/fault/degraded views after
  production controller-status verification. Preserve unknown states and
  handle energy counter resets. Relay state is not independent compressor proof.
  Agree on time range and refresh rate before implementation.
  This work is deliberately deferred and does not block notifications.

### Dependencies and discovery

Stack versions and MQTT identities have been discovered. Retention is infinite
for existing fermentation and an agreed 30 days for the separate fridge database.
Remaining production routing checks, storage capacity,
actual Shelly counter reset/reboot behavior, and the intended dashboard time
range/refresh rate remain to be established.
Use separate scoped MQTT and InfluxDB credentials without committing them.

## 2. Mosquitto ACLs

### Rationale and intended approach

- [ ] Keep authentication required. Authenticated reads may be broad;
  restrict writes by least privilege, especially Shelly control.
- [ ] With approval, inspect the broker service invocation, effective config,
  password-file references, include paths, ACL/plugin state, and all existing
  shared-broker clients. Do not read or copy password-file contents.
- [ ] Map the conceptual identities below to actual authenticated accounts.
  Separate shared credentials where necessary. MQTT client IDs alone are
  not an authentication boundary; changing a client ID must not grant writes.
- [ ] Evaluate include_dir for modular configuration without changing listener,
  authentication, or unrelated applications' behavior. It is optional, not
  a prerequisite for ACLs.

| Identity | Intended publish permissions | Required reads |
|---|---|---|
| fridge-sensor | Own fridge-sensor/# only; no splug1/rpc or fridge-controller/# | Discover firmware needs; broader authenticated reads acceptable |
| fridge-controller | fridge-controller/# and splug1/rpc | Both sensor topics, fridge-controller/rpc, and any additional proven-required Shelly/status topics |
| splug | Only discovered Shelly publications plus the exact fridge-controller/rpc response topic where required | splug1/rpc and any other topics proven necessary for its MQTT/RPC behavior |
| Telegraf | None unless a requirement is demonstrated and separately reviewed | Ingested sensor, controller, and discovered Shelly telemetry topics |
| Unrelated clients | Preserve their required application writes; deny Shelly control and unrelated fridge writes | Broad authenticated telemetry/status reads acceptable |

Shelly's narrowly scoped response permission is an exception within the
controller namespace, not general write access to fridge-controller/#.
Confirm actual account names and RPC routing before generating ACL rules.
The same control-write restriction applies to future monitoring clients.

### Acceptance criteria

- [ ] Anonymous connections are denied; known authenticated clients retain
  their required reads and legitimate writes.
- [ ] In an isolated broker, verify allowed publications are delivered and
  denied publications are not delivered, including retained-message attempts.
  Do not rely solely on a publisher's exit status. Avoid real actuator topics
  on production during negative tests.
- [ ] Only the authenticated fridge-controller identity can publish splug1/rpc.
  Sensor, splug, Telegraf, unrelated users, and client-ID impersonation attempts
  cannot obtain that permission.
- [ ] Sensors cannot write controller state; splug cannot write unrelated
  namespaces except its exact required RPC response route; Telegraf cannot
  publish telemetry, responses, or control.
- [ ] Controller RPC request/response flow, Shelly telemetry and availability,
  sensor availability/log/state publications, and unrelated applications still
  work. Discover alternate Shelly control topics and restrict them too.
- [ ] Review effective rules for wildcard overlaps and inherited broad writes.
  Validate with the installed broker version before rollout.
- [ ] Record validation, rollback, and remote-access recovery procedures.
  Preserve working authentication and unrelated client access. Any handling
  of credential material requires a separately approved approach.

### Dependencies and discovery

Known usernames are hydrom, telegraf, fridge-sensor, splug, and
fridge-controller; the owner confirmed dedicated use by their applications.
The broker requires authentication and now references the deployed ACL.
No include directory or authentication plugin was introduced.
Shelly Plug M Gen3 firmware 2.0.1 uses splug1; controller replies use
fridge-controller/rpc. Revisit topic requirements when adding clients or
changing firmware. Recheck retained commands, queued sessions, and rollback
readiness before future policy changes.

- [ ] Review the ownership warnings for /etc/mosquitto/passwd and
  /etc/mosquitto/fridge.acl before upgrading Mosquitto. Any ownership change
  must preserve credential contents and be reflected in the relevant
  automation after review.

## 3. Failure notifications, preferably ntfy

### Rationale and intended approach

- [ ] Notify Android users of actionable failures and useful recoveries.
  Prefer ntfy, self-hosted on the Pi if it fits the existing services and
  resources, with the Android ntfy app as receiver. Choose another solution
  only with a documented strong reason.
- [ ] Inspect existing monitoring/notification services, Pi resources, network
  reachability, endpoint authentication/TLS, and Android background-delivery
  requirements before selecting deployment and delivery details.
- [ ] Decide between controller-originated events and an external monitoring
  component using the actual architecture. Prefer an independent MQTT consumer
  of status/online signals where practical. Delivery failures, retries, and
  timeouts must never block or change core temperature-control decisions.
- [ ] Define a transition-based state machine with startup grace, sustained
  condition thresholds, deduplication, debounce, rate limits, bounded retries,
  and restart/reconnect behavior. Avoid one alert per loop or retained replay.
- [ ] Cover FAULT/both sensors stale, sustained DEGRADED_AIR or
  DEGRADED_KEG_ONLY, repeated Shelly command failures, persistent requested/
  actual mismatch, detectable controller/MQTT health loss, and useful recovery
  to NORMAL. Treat unknown output separately from a confirmed mismatch.
- [ ] Account for current signal limits: the command-failure count resets after
  a successful Switch.Set and is not a lifetime counter; output_actual can
  remain remembered without fresh confirmation; status has no source timestamp.
  A disconnected controller cannot publish its own disconnected status.
  Use last-will/online, observed freshness, and independent checks as appropriate.
- [ ] Define expected-running state and maintenance suppression so an
  intentionally stopped/disabled controller is not treated as an outage.
  A monitor and ntfy on the same Pi cannot reliably report total Pi/network
  failure without an independent observer/delivery path.

### Acceptance criteria

- [ ] Isolated tests produce one alert per qualified failure transition and
  an appropriate recovery notification, without repeated loop-level spam.
- [ ] Test threshold crossings, brief flaps, retained startup messages,
  reconnects, monitor restarts, counter resets, and intentional downtime.
- [ ] Test repeated RPC failures and sustained output mismatches with simulated
  devices; no real fridge operation is needed for notification testing.
- [ ] Demonstrate Android receipt and useful content, and test ntfy/network
  unavailability without any controller slowdown, failure, or output change.
- [ ] Authenticate notification publishing/reading appropriately. Keep topic
  credentials, tokens, and endpoint secrets outside Git and sensitive logs.
- [ ] Document supported health coverage, chosen alert durations/rate limits,
  recovery semantics, maintenance controls, and remaining blind spots.

### Dependencies and discovery

An approved ntfy deployment location, Android delivery/network requirements,
external-monitor placement, alert thresholds and recipients, expected service
uptime, MQTT permissions, and external secret provisioning remain undecided.
Monitoring and notification components must not receive Shelly control writes.
