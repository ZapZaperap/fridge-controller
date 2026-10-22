# Shared broker ACL proposal

Status: the reviewed ACL was deployed and verified on 2026-10-22.
Password contents and service runtime/boot states were preserved.

## Discovered production baseline

Before ACL deployment, the owner reported Mosquitto running on jpi4 with:

```text
listener 1883
allow_anonymous false
password_file /etc/mosquitto/passwd
```

The service loads /etc/mosquitto/mosquitto.conf without systemd drop-ins.
No active include_dir, ACL, or authentication plugin directive was reported
before deployment. Deployment added acl_file /etc/mosquitto/fridge.acl;
listener and authentication directives remain unchanged.
The password file contains hydrom, telegraf, fridge-sensor, splug, and
fridge-controller. Its contents were not inspected or copied.

hydrom serves the iSpindel publishing under sensors/hydrom/Hydrom_2d699e/.
The Shelly Plug M Gen3 uses firmware 2.0.1 and topic prefix splug1.
Observed splug1/events/rpc messages include power, current, voltage,
frequency, and energy counters. Periodic reporting remains unconfirmed.

## Candidate policy

fridge.acl.example grants broad reads to the five known usernames and limits
writes to their application namespaces. Telegraf has no write grants.
Only fridge-controller can publish splug1/rpc. No account can publish
splug1/command/#. Client-ID changes do not change username permissions.

Shelly may publish online state, RPC notifications, component status, and
responses on the exact fridge-controller/rpc route. Component-status
publishing is allowed for compatibility but has not been observed here.
Other Shelly response destinations and publications remain denied.

The controller's namespace grant also includes fridge-controller/rpc;
Shelly has a narrow response exception within that namespace.
Broad reads include RPC messages. This policy provides write isolation,
not message confidentiality or restrictions on individual RPC methods.

## Isolated test plan

### Verified results — 2026-10-21

The owner ran tests/test_acl.py successfully in a disposable container.
Both production and the test image reported Mosquitto 2.0.21.
The combined test used Telegraf 1.39.3.

Verified with generated dummy credentials and synthetic messages:

- Anonymous and wrong-password connections were rejected.
- All five accounts could read; allowed and denied writes matched the matrix.
- Only fridge-controller could publish splug1/rpc, including retained attempts
  and attempts to impersonate its client ID using another account.
- Simulated request and response publications reached their permitted routes.
- Sensor, controller, and Shelly Last Wills reached existing subscribers and
  were retained for fresh subscribers.
- The actual Telegraf process ingested both temperatures and NORMAL/FAULT
  status through the authenticated broker, preserving field types and
  omitting unknown values from line protocol.

The harness uses stdbuf from coreutils to make subscriber readiness output
visible through pipes. It stops its temporary broker and Telegraf processes
and removes temporary files after execution.

This combined test writes metrics to a temporary file, not InfluxDB.
Earlier separate tests verified sample ingestion into InfluxDB 3.11.2.
Real devices, actual controller execution, fermentation ingestion, and
reconnect behavior remain unverified by this suite.
Separate broker Ansible tests subsequently verified reload enforcement,
idempotence, stopped-state preservation, and rollback; see
[the broker runbook](../ansible/broker-runbook.md).
The owner subsequently verified production deployment and continuing sensor,
Shelly, and fermentation traffic. The unchanged second run reported changed=0.
See the broker runbook for production evidence, backup location, and ownership
warnings. Actual controller startup and actuator commands remain untested.

### Test requirements

Use a fresh disposable broker container with no external network, published
ports, host mounts, production password file, or real Shelly connection.
Match the production Mosquitto version before accepting the results.
Generate dummy credentials inside the container; never copy production ones.

For every publication, use unique payload markers and an authenticated
observer subscribed before publishing. Confirm subscription readiness and
use a successful allowed publication as a positive control. Check actual
delivery with bounded waits; publisher exit status alone is insufficient.

Test:

- Anonymous and incorrect-password connections are rejected.
- Each account can read representative telemetry and status messages.
- hydrom and fridge-sensor can publish within their namespaces but cannot
  publish controller state, Shelly commands, or one another's data.
- fridge-controller can publish status, online/LWT, and splug1/rpc.
- splug can publish each allowed route, including responses, but cannot
  publish splug1/rpc, command topics, or unrelated controller state.
- telegraf cannot publish any representative telemetry or control topic.
- All five accounts are tested against splug1/rpc: only the controller
  succeeds, including QoS 1 and retained-publication attempts.
- Alternative splug1/command/# routes remain unwritable.
- Impersonating the controller client ID with another username does not
  grant control access. Use isolated sequential connections for this test.
- Denied retained publications are absent for a fresh subscriber too.
- A simulated request/response exchange preserves controller RPC routing.
- Sensor/controller availability messages and fermentation ingestion work.

Dummy-client tests do not establish actual device compatibility.
Investigate missing topic requirements before granting additional writes.

## Production review and rollback

First confirm broker version, complete account/client inventory, and any
additional device publications. Inspect existing retained control messages
and queued sessions: ACLs do not prove that historical commands are absent.
Do not issue production actuator commands as negative tests.

After isolated tests pass, prepare a separately reviewed Ansible change.
Keep authentication required and preserve the password file. Add only the
reviewed ACL reference and file. Defer include_dir restructuring so the
initial authorization change remains small.

Before execution, record service state and securely back up the existing
configuration, ACL state, and relevant persistent broker data as applicable.
Do not place credentials or broker data in Git or diagnostic output.
Agree on the apply method, interruption window, and rollback trigger.

Production access and applying or reloading broker configuration require
explicit authorization. Verify device connections, fermentation ingestion,
sensor updates, Shelly notifications, and controller responses afterward.
Preserve the controller's runtime state; do not start it merely to validate
ACLs without separate approval.

Rollback restores the previous broker configuration and prior ACL file
state, then applies it using the separately approved method. Verify original
client behavior. Reverting ACL enforcement temporarily restores the previous
authenticated-but-unrestricted write policy.
