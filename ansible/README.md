# Ansible deployment

The controller role installs Python dependencies, application files,
configuration, and the existing systemd unit. It preserves password contents.

Systemd reloads when the controller unit changes.
The role records whether the controller is running before deployment changes.
Changed dependencies, application files, configuration, or unit notify a restart,
which runs only if the controller was running initially.
Stopped or absent services are not started. Boot enablement is left unchanged.
A changed config.yaml can still be picked up by the controller's live reload.
Restart runs the existing controller shutdown/startup logic, including OFF requests.
Production execution requires separate authorization.

## Scope

Reproduce the existing controller installation while preserving application
behavior, MQTT topics, and production paths. Application files and the
service unit will come from the existing repository directories.

The shared Mosquitto configuration and password file remain unmanaged.
ACL authorization and a possible include_dir are deferred improvements.
ESPHome compilation and flashing are outside this deployment.

## Production baseline

- Host: jpi4; Debian 13 (trixie), ARM64.
- Controller virtual environment: Python 3.13.5.
- Python dependencies: paho-mqtt 2.1.0 and PyYAML 6.0.3.
- Installation: /home/josias/fridge-controller, owned by josias.

## Validation and prerequisites

Run Ansible commands from this directory so ansible.cfg is loaded.
Local syntax validation: ansible-playbook --syntax-check playbooks/controller.yml.

The inventory connects through the SSH hostname jpi4 (192.168.178.29), allowing
the workstation's existing SSH configuration to select the login key.
SSH and sudo access were verified for the initial deployment.
The role preserves the existing controller
password file at /home/josias/fridge-controller/mqtt-password and fail clearly
if it is missing. Its contents will not be read or copied into the repository.

See the [production deployment runbook](../docs/production-deployment.md) for
preflight, backup, change review, deployment, verification, and rollback.
Each production stage requires separate authorization before execution.

## Deployment verification

The initial production deployment succeeded. The controller remained inactive
and disabled; directory permissions were verified as 755 and controller.py,
config.yaml, and mqtt-password as josias:josias 600. The subsequent deployment
reported changed=0 with no failures.

System prerequisites are python3, python3-venv, and python3-packaging.
The first check-mode run required separately bootstrapping python3-packaging:
check mode predicts prerequisite installation without making those packages
available to later tasks.

The production backup remains on the Pi at:
/var/backups/fridge-controller.lOtivPt2/controller.tar.gz.

## Deferred credential management

- Choose encrypted credential storage in Git or a comparable system, including
  provisioning credentials on a replacement Pi.
- Keep plaintext credentials and decryption keys outside the repository.
