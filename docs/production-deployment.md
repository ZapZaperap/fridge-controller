# Production deployment runbook

This procedure targets jpi4 at 192.168.178.29. Recording this procedure does
not authorize production access, backups, deployment, or service operations.
Obtain separate authorization before executing each production stage.

## Recorded baseline and local validation

- Production: Debian 13 (trixie), ARM64.
- Controller directory: /home/josias/fridge-controller.
- Service user/group: josias:josias.
- Unit: /etc/systemd/system/fridge-controller.service, with no overrides.
- Service state reported before deployment: inactive and disabled.
- Virtual environment: Python 3.13.5; paho-mqtt 2.1.0; PyYAML 6.0.3.

The native Debian 13 AMD64 container passed installation, service-state,
password-permission, and idempotence checks. Stopped/disabled stayed stopped
after a notified change; running/disabled restarted after a notified change;
an unchanged deployment reported changed=0 without restarting the service.
The stopped/enabled case was deliberately skipped. ARM64 runtime behavior
and physical actuator behavior remain unverified. ARM64 user-mode emulation
exhibited widespread systemd failures; native AMD64 systemd ran successfully.

## 1. Read-only preflight

Obtain authorization before accessing production. Confirm SSH access as
josias and sudo availability without sharing credentials.

Recheck the OS, architecture, unit location, absence of overrides, service
state, existing application files, and virtual environment. Check the MQTT
password file's existence, size, ownership, and permissions without reading
its contents. Record the installation directory's and password file's numeric
ownership and permissions for rollback.

The expected state for this first deployment is inactive and disabled. If it
has changed, review the plan before continuing rather than stopping the
controller automatically. Do not allow concurrent changes during backup
and deployment.

## 2. Backup on the Pi

Obtain separate authorization before creating the backup. Run these commands
on the Pi in the same shell. They create a uniquely named, root-protected
directory and archive only the listed paths. The controller MQTT password
and shared Mosquitto configuration/password file are excluded.

```bash
fridge_backup_dir=$(sudo mktemp -d /var/backups/fridge-controller.XXXXXXXX)
```

```bash
sudo tar -czf "$fridge_backup_dir/controller.tar.gz" -C / \
  home/josias/fridge-controller/controller.py \
  home/josias/fridge-controller/config.yaml \
  home/josias/fridge-controller/.venv \
  etc/systemd/system/fridge-controller.service
```

```bash
sudo gzip -t "$fridge_backup_dir/controller.tar.gz"
```

```bash
printf '%s\n' "$fridge_backup_dir"
```

Record the printed backup path and retain the preflight state and metadata
records. If any backup command fails, stop before deployment. Do not copy
the MQTT password into the backup, repository, or workstation.

## 3. Review predicted changes

Obtain separate authorization before running check mode. Run from the
workstation's ansible directory using the confirmed SSH account:

```bash
ansible-playbook playbooks/controller.yml --limit jpi4 --user josias --ask-become-pass --check --diff
```

Check mode contacts production and predicts changes. It does not replace
the backup or guarantee a successful deployment. Review its output before
authorizing deployment.

## 4. Deploy

After reviewing predicted changes and obtaining explicit deployment
authorization, run from the workstation's ansible directory:

```bash
ansible-playbook playbooks/controller.yml --limit jpi4 --user josias --ask-become-pass
```

Expected effects are installing missing Python prerequisites, ensuring pinned
Python dependency versions, copying controller files and the unit, setting
file ownership and permissions, and reloading systemd if the unit changes.

The role preserves boot enablement and restarts after relevant changes only
when the controller was initially running. This Pi should therefore remain
inactive and disabled. The shared broker remains unmanaged. An active
controller may pick up configuration changes through its existing live reload.

## 5. Verify

Verify file ownership and permissions, installed dependency versions, and
service state. Do not start the controller merely to verify this deployment:
the expected production state is inactive and disabled.

Review the first deployment result before authorizing a second run. Expect
changed=0 on an unchanged deployment. Retain the backup until the deployment
has been accepted.

## 6. Rollback if necessary

Obtain explicit authorization before restoring files or operating services.
Use the recorded backup path and preflight state, not assumptions about
whether the controller should run.

1. Stop the controller if it is running before restoring files.
2. Move the deployed virtual environment aside so restored packages are not
   mixed with files left over from deployment.
3. Extract the trusted, root-protected archive into its original paths,
   preserving ownership and permissions.
4. Restore the recorded installation-directory and password-file ownership
   and permissions if needed, without replacing or reading password contents.
5. Reload systemd and restore the recorded runtime state. Restore boot
   enablement only if it differs from the preflight record.
6. Verify the restored files, dependency versions, and service state.

The backup does not automatically reverse system package changes or restore
the OS-level Python interpreter. Restoring a virtual environment depends on
a compatible system interpreter and libraries. Review system package changes
separately if they contributed to the failure.

## Initial production deployment result

The owner completed the reviewed production deployment and verification.

- SSH uses the previously trusted hostname jpi4 and workstation SSH configuration.
- python3-packaging was installed separately to allow the initial check-mode run.
- Configuration and unit settings were reconciled with production before deployment.
- Deployment completed with changed=1: only the unit file reported a change.
- The controller remained inactive/dead, disabled, and MainPID=0.
- Directory ownership/mode: josias:josias, 755.
- controller.py, config.yaml, and mqtt-password: josias:josias, 600.
- The subsequent production run completed with changed=0 and no failures.
- Backup: /var/backups/fridge-controller.lOtivPt2/controller.tar.gz.

Rollback was not exercised. The stopped/enabled container case was deliberately
skipped. Native AMD64 deployment tests passed; ARM64 production deployment was
verified with the controller stopped, without exercising actuator behavior.
