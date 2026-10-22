# Shared broker ACL deployment

Status: deployed and verified on production on 2026-10-22.
Future production execution still requires separate approval.
See ../mosquitto/README.md for policy, test evidence, and remaining limits.

The broker playbook installs /etc/mosquitto/fridge.acl and adds its reference
to the existing main configuration. It preserves password-file contents,
listener settings, other services, and boot enablement.
A changed configuration reloads only a previously running broker.
A stopped broker stays stopped and loads the policy on its next start.

## Isolated role verification — 2026-10-22

The owner exercised the role in a network-isolated Debian systemd container
using Mosquitto 2.0.21 and generated dummy credentials.

- Initial deployment succeeded; reload preserved broker PID 97, active/running
  state, enabled boot state, and NRestarts=0.
- Authorization and Last Will tests passed against that reloaded broker.
- An unchanged second run reported changed=0 and preserved PID 97.
- With the broker stopped, deliberate ACL permission drift was corrected
  (changed=1); reload was skipped and inactive/enabled states were preserved.
- After starting the test broker as PID 1336, restoring the baseline config
  and removing the ACL file followed by reload preserved PID 1336.
- Rollback verification confirmed authenticated writes were restored while
  anonymous access remained denied.

The test image requires procps because the service's ExecReload invokes
/bin/kill. The version probe accepts help-command exit codes 0 and 3,
then separately asserts the exact version.

These checks do not verify real devices, persistent client connections across
reload, existing fermentation ingestion, or the separate Telegraf role.
Production verification is recorded below.

## Production verification — 2026-10-22

The owner completed preflight, reviewed check mode, and explicitly approved
deployment and reload. The five MQTT accounts were confirmed to be dedicated
to their named applications.

The pre-deployment backup is /var/backups/fridge-mqtt-acl.eHu1pG.
It contains the previous main configuration, the original absent ACL state,
and service-state records. Password contents were not copied.

Deployment reported ok=10, changed=3, failed=0. Mosquitto remained
active/running and enabled with PID 1869495. The controller remained
inactive/disabled with MainPID=0.

The broker processed the reload without an ACL-loading error in the supplied
logs. The owner confirmed both temperatures, Shelly notifications, and
fermentation updates continued afterward. An unchanged second deployment
reported ok=9, changed=0, failed=0 and preserved both service states and PID.

Production actuator commands and controller startup were not tested.
The production rollback was not executed; rollback was verified in isolation.

The logs warned that /etc/mosquitto/passwd and /etc/mosquitto/fridge.acl
are not owned by mosquitto and may be refused by future versions.
Current permissions were accepted by 2.0.21. Ownership changes remain a
separately reviewed follow-up; the role currently sets the ACL to
root:mosquitto 0640 and leaves the password file unchanged.

## Production preflight — review before execution

- Reconfirm broker version, service command, configuration, account inventory,
  and absence of conflicting ACL/plugin/include settings.
- Confirm /bin/kill exists and the service supports the reviewed reload.
- Record broker and controller runtime and boot states; preserve intentional
  controller downtime.
- Confirm actual device publications fit the candidate and identify any other
  clients sharing the five accounts.
- Review historical retained control messages and queued sessions without
  publishing actuator commands or exposing credentials.
- Establish baseline sensor updates, Shelly notifications, and fermentation
  ingestion so post-change behavior can be compared.
- Record whether the destination ACL file already exists. Securely back up
  the main configuration, prior ACL state, and relevant broker data.
- Agree on verification, rollback triggers, and the authorized reload window.

Preflight access, check mode, deployment, and reload remain separately
authorized steps. Do not execute production commands merely because these
checks are listed here.

Before production execution, confirm all client requirements, inspect historical
retained commands and queued sessions, record service state, and take reviewed
backups. Preserve the existing password file outside Git; do not display it.
The role backs up overwritten files but does not automatically roll back.

Run syntax validation from the ansible directory:

```bash
ansible-playbook --syntax-check playbooks/broker.yml
```

After isolated role tests pass, obtain authorization for production check mode
and review its diff. Actual execution and broker reload require separate approval.
A successful reload command alone does not prove the ACL was accepted:
inspect broker logs and verify approved client traffic and ingestion afterward.
Do not start the production controller or issue actuator commands for testing
without separate approval.

Rollback restores the previous main configuration and prior ACL file state,
then reloads a running broker using the separately approved procedure.
Verify original client behavior and preserve stopped/boot-enabled states.
Removing ACL enforcement restores the previous unrestricted authenticated writes.
