# Fridge telemetry deployment runbook

Status: production temperature ingestion is deployed and verified.
Further production changes require separate approval.
The shared broker and monitoring stack serve other applications.

## Scope

The separate playbooks/telemetry.yml playbook installs the tested MQTT input
fragment and a systemd environment-file drop-in. It also manages two rotation
settings in the existing main configuration's file-output block and two database
routing settings in its existing InfluxDB v3 output. It does not
install Telegraf, replace existing inputs or outputs, configure MQTT accounts
or ACLs, provision credentials, or change controller behavior.

Managed paths:

- /etc/telegraf/telegraf.conf (debug-output rotation and database routing settings)
- /etc/telegraf/telegraf.d/fridge.conf
- /etc/systemd/system/telegraf.service.d/fridge.conf

The existing /tmp/telegraf-debug.out output rotates at 10 MB and keeps one
archive: approximately 20 MB in normal operation, with possible write-size
overshoot. It has no line-count limit. Main-configuration diffs are suppressed
to avoid displaying credentials; the rotation task backs up the original file
and validates TOML and placement of the two settings before replacing it.

The InfluxDB output keeps database = "telegraf" as the fallback for fermentation
and uses database_tag = "influxdb_database" to route tagged fridge measurements
to fridge. exclude_database_tag = true removes the routing tag before storage;
the debug file still includes it. The owner selected infinite retention for
telegraf and 30 days for fridge. This role does not create databases.

The role requires an existing Telegraf service and configuration directory.
Changed inputs notify a restart; changed drop-in content first reloads systemd.
Only a previously running Telegraf is restarted. A stopped service stays stopped,
and boot enablement is unchanged. A restart briefly interrupts all Telegraf inputs,
including fermentation ingestion; account for potential gaps during review.

## Prerequisites

1. Review the [monitoring proposal and test results](../monitoring/telegraf/README.md).
   Run the 17 offline parser cases with the deployment's Telegraf version.
2. Verify a dedicated MQTT identity can read the required topics and cannot
   publish telemetry, RPC requests, or responses. Confirm actual denied-message
   delivery, rather than relying only on publisher exit codes. Perform control
   topic tests in an isolated broker. Shared production ACL rollout is separate.
3. Provision /etc/telegraf/fridge.env outside Git as root:root mode 0600.
   Define FRIDGE_MQTT_SERVER, FRIDGE_MQTT_USERNAME, and FRIDGE_MQTT_PASSWORD
   using systemd EnvironmentFile syntax. Use the agreed broker URL and the
   verified read-only identity. Do not print the file or include it in diffs,
   issue reports, or repository backups. Systemd reads it before starting the
   service as user telegraf; that account need not read the file directly.
4. Check for conflicting definitions of these environment variable names in
   existing service configuration, environment files, and command-line options
   without exposing their values. Confirm the effective service still loads
   /etc/telegraf/telegraf.d and uses the expected main configuration.
5. Before installing inputs, create and verify the fridge database with 30-day
   retention through a separately approved procedure. Do not rely on implicit
   database creation, which could use the wrong retention. Confirm the output
   token can write to fridge and preserve telegraf's infinite retention.
   The token file /etc/influxdb3/telegraf_token was reported as root:root 0600;
   keep its contents outside Git and diagnostic output. Core retention is fixed
   at creation. The separate playbooks/database.yml playbook provisions fridge
   idempotently; review and test it before production execution.
6. Review the existing outputs' scope, file destination and rotation, retention,
   capacity, and expected ingestion volume. Controller status can arrive roughly
   once per second. Existing outputs will receive the new measurements.
7. Record current runtime and boot states. Back up any existing managed files
   outside the loaded configuration directory; record if either path is absent.
   Preserve other systemd drop-ins. The role also backs up overwritten files,
   but does not automatically roll back a failed restart.

The role checks environment-file metadata only. It does not validate variable
contents, effective permissions on the broker, or the full merged Telegraf
configuration. Setting fridge_telemetry_mqtt_acl_verified=true is an explicit
operator assertion, not an automated ACL test.

## Local and isolated validation

Run syntax validation from the ansible directory:

```bash
ansible-playbook --syntax-check playbooks/telemetry.yml
```

Before production review, exercise the role in a disposable systemd container
with Telegraf 1.39.3, dummy credentials, and an isolated broker/database. Verify:

- Missing prerequisites or the ACL assertion fail before managed files change.
- Existing shared inputs and outputs continue to work.
- A running service restarts after a relevant change, then ingests test samples.
- A stopped service remains stopped after a change; boot enablement is preserved.
- An unchanged second run reports changed=0 and preserves the running PID.
- A bad environment/configuration produces a visible failure and the reviewed
  rollback procedure restores ingestion.

## Verified role results — 2026-10-22

The tests used a network-isolated Debian systemd container, Telegraf 1.39.3,
the tested broker ACL, generated dummy credentials, and a file output.
The fixture is tests/prepare-telemetry.yml.

- Synthetic fermentation ingestion worked before deployment.
- Deployment restarted the running service from PID 2186 to 2644, preserving
  active/enabled state. The existing main configuration checksum was unchanged.
- Fresh fermentation samples continued alongside both fridge temperatures
  and NORMAL/FAULT controller status. Unknown fields remained omitted.
- An unchanged second run reported changed=0 and preserved PID 2644.
- With Telegraf stopped, permission drift was corrected (changed=1), the restart
  handler was skipped, and inactive/enabled states were preserved.
- Deliberately invalid TOML caused a visible parsing error and exit status 1.
- Rollback moved the added input and environment drop-in outside active
  configuration paths, reloaded systemd, and started the baseline service.
  The unchanged main configuration ingested a fresh fermentation sample.

Rollback files remain in /run/fridge-telemetry-rollback inside the disposable
container; /run is temporary. Dummy credential files were left in place.

These tests did not use the production configuration, InfluxDB output, or real
iSpindel. Missing-prerequisite rejection and credential-error cases were not
separately exercised. A successful restart task alone is not a health check;
production verification must inspect logs and confirm continuing ingestion.

These results predate the debug-output rotation and database-routing extensions.
Those extensions intentionally change four main-configuration settings; the
earlier unchanged checksum result applies to the previous role version.
The subsequent extension checks are recorded below. The original file-only
fixture does not include an InfluxDB output; the routing test added one inside
the disposable container before applying the current full role.

## Two-database routing verification — 2026-10-22

A fresh InfluxDB 3 Core 3.11.2 container shared the test broker's isolated
network namespace, with no published host ports or host bind mounts.
MQTT used the ACL and generated dummy credentials. The test database server
used --without-auth; production InfluxDB token access was not tested.

- Created telegraf with infinite retention and fridge with 30 days.
- The updated role applied rotation, routing, inputs, and the environment
  drop-in successfully (ok=15, changed=5, failed=0).
- A synthetic fermentation sample appeared only in telegraf.
- Both fridge temperatures and NORMAL/FAULT status appeared only in fridge.
- The routing tag was absent from stored measurement columns.
- SQL confirmed unknown actual output and sensor ages were NULL.
- A second role run reported ok=13, changed=0, failed=0; Telegraf retained
  PID 4008, active/enabled state, and NRestarts=0.

Measurement tables used schema iox. Metadata queries must not assume public.
Retention configuration was inspected; elapsed 30-day expiry was not tested.
Rotation settings and idempotence were verified, but a 10 MB rollover was
not forced. Subsequent production results are recorded below.

## Database provisioning

The separate playbooks/database.yml playbook runs
monitoring/influxdb/ensure_fridge_database.py on the target as root. The helper
reads /etc/influxdb3/telegraf_token locally, requires a regular root-owned file
with mode 0600, and supplies the token through the CLI environment. Token
contents are not returned to Ansible or placed in command-line arguments.
The target must have Python 3.11+ and the influxdb3 CLI available.

The helper connects only to http://127.0.0.1:8181. It requires existing telegraf
with infinite retention, creates fridge with 30 days if missing, and rejects
conflicting existing fridge retention without migration or deletion. It does
not alter other databases or restart services.

After isolated provisioning tests and separate approval, review production
check mode from the ansible directory:

```bash
ansible-playbook playbooks/database.yml --check
```

The script task runs during Ansible check mode but receives --check, so it
only reads metadata and reports whether creation is needed. After reviewing
the result and obtaining explicit creation approval:

```bash
ansible-playbook playbooks/database.yml
```

Inspect retention afterward and repeat to verify changed=0. Verify that the
actual Telegraf output credential can write to fridge before enabling inputs;
successful provisioning alone does not establish that access.
Rollback must not delete the database.

## Production verification — 2026-10-22

The owner executed the reviewed production procedures and reported:

- Database provisioning succeeded (ok=1, changed=1); repeat execution
  reported changed=0. The helper verified fridge retention at 30 days
  and preserved telegraf's infinite retention.
- The owner confirmed the token file contains Telegraf's actual output
  credential. A manual write to fridge succeeded using that credential.
  The test measurement is fridge_ingestion_check, with source=manual.
- The external MQTT environment file was provisioned outside Git.
- Telemetry deployment succeeded (ok=15, changed=5, failed=0).
  Telegraf was active/running/enabled, PID 155351, NRestarts=0.
- Recent fermentation ingestion was confirmed through the existing
  Grafana datasource.
- Both fridge temperatures appeared in Grafana Explore through a separate
  datasource using database fridge.
- Repeat telemetry deployment reported ok=13, changed=0, failed=0;
  no restart handler was triggered.

The Grafana fridge datasource uses SQL, http://127.0.0.1:8181,
and Insecure Connection enabled for the local endpoint without TLS.
InfluxDB Enterprise 3.x was selected because the menu lacked a Core option.
Tokens remain outside this documentation.

Controller-status ingestion remains unverified in production because the
controller is intentionally inactive and disabled. No controller startup
or physical actuator test was performed. Production checks have not yet
verified stored field types, routing-tag removal, absence of fridge data
in telegraf, elapsed retention expiry, or debug-file rollover.

### Subsequent Shelly electrical rollout

- All 17 offline parser cases passed with Telegraf 1.39.3.
- An isolated authenticated MQTT-to-InfluxDB test verified electrical values,
  stable tags, Float64 field types, partial-update NULLs, routing-tag removal,
  and no fridge_shelly table in the fermentation database. A lifecycle-only
  notification produced no row.
- The first test queried before the measurement existed and received HTTP 400.
  Subsequent checks verified the samples flushed during temporary-process
  shutdown; this was not a clean uninterrupted test run.
- The reviewed production diff added only the Shelly MQTT input and a comment
  update. Deployment reported ok=14, changed=2, failed=0.
- Telegraf was active/running/enabled, PID 161462, NRestarts=0.
- The owner confirmed plausible electrical readings in Grafana Explore,
  followed by continuing fermentation and both temperature readings.
- Repeat deployment reported ok=13, changed=0, failed=0.

Grafana panels are explicitly deferred to the backlog. Controller-status
verification remains pending while the controller is intentionally inactive.
Energy behavior across actual device resets and reboots remains unverified.

## Production review and execution

Production access, credential provisioning, ACL changes, and deployment each
require explicit authorization. Once prerequisites and isolated role tests pass,
review check-mode predictions from the ansible directory:

```bash
ansible-playbook playbooks/telemetry.yml --check --diff -e fridge_telemetry_mqtt_acl_verified=true
```

Check mode does not validate ingestion or guarantee a successful restart.
After reviewing the diff and receiving separate deployment authorization:

```bash
ansible-playbook playbooks/telemetry.yml --diff -e fridge_telemetry_mqtt_acl_verified=true
```

Verify the service's prior runtime and boot states were preserved, inspect logs
without exposing credentials, and query both new measurements in fridge.
Confirm fermentation remains in telegraf with no fridge measurements leaking
into it, and that the routing tag is absent from stored columns.
Confirm stored types and NULL behavior match the isolated results,
and fermentation ingestion continues. Repeat the playbook to verify changed=0
and an unchanged running PID. Test Grafana queries separately.

## Rollback and credential rotation

With explicit authorization, restore the main configuration from its recorded
backup, including its original file-output and InfluxDB routing settings. Restore the added input
and drop-in from backups, or remove only those newly introduced files if
originally absent.
Keep backups outside telegraf.d so they cannot be loaded as extra inputs.
Reload systemd when restoring/removing the drop-in. If Telegraf was previously
running, restart it and verify the original ingestion; otherwise leave it stopped.
Preserve boot enablement and all unrelated configuration. Keep the externally
provisioned credential file until its separate retirement is reviewed.
Do not delete databases as part of configuration rollback. Disable/remove the
new fridge inputs when reverting routing so they cannot fall back to telegraf.

Credential-content changes are intentionally not detected by this role.
Rotation requires a separately approved restart of a running Telegraf so it
receives the updated environment. Never commit credentials or decryption keys.
