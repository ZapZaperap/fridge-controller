# Isolated deployment test

Run the commands below from the repository root on the workstation.
The image contains only controller.py, config.yaml, the service unit, and
the ansible directory. ESPHome secrets and production password files are
not copied. No host directories or sockets are mounted and no ports are
published. The controller uses a container-local broker and generated
dummy credentials.

## Build and start

The build installs Debian test tools and downloads packages. Podman stores
the resulting image and container locally. Rootless Podman is required.
The ARM64 commands also require registered ARM64 QEMU emulation.

```bash
podman build --platform linux/arm64 -t localhost/fridge-controller-test -f ansible/tests/Containerfile .
podman run -d --name fridge-controller-test --platform linux/arm64 --systemd=always localhost/fridge-controller-test
```

Podman runs systemd inside the container. No privileged mode is requested.

### Native AMD64 alternative

The ARM64-emulated container exhibited widespread systemd failures while a
native AMD64 container started successfully. On x86_64 workstations, use
the native alternative for deployment and service tests:

```bash
podman build --platform linux/amd64 -t localhost/fridge-controller-test-amd64 -f ansible/tests/Containerfile .
podman run -d --name fridge-controller-test-amd64 --platform linux/amd64 --systemd=always localhost/fridge-controller-test-amd64
```

For subsequent commands, substitute fridge-controller-test-amd64 for
fridge-controller-test. The test inventory permits both architectures;
production defaults remain ARM64-only. Native tests do not verify ARM64 binaries.

## Prepare and deploy

Prepare fixtures once per fresh container. This creates a dummy account and
password and restarts only the container's MQTT broker. It intentionally
changes password ownership so the deployment role can correct it.

```bash
podman exec fridge-controller-test ansible-playbook -i tests/inventory.yml tests/prepare.yml
podman exec fridge-controller-test ansible-playbook -i tests/inventory.yml playbooks/controller.yml -e @inventory/host_vars/jpi4.yml
```

Do not run the local-connection test inventory directly on the workstation
or production host. Run it through podman exec as shown.

## Verify and repeat

```bash
podman exec fridge-controller-test systemctl is-active fridge-controller.service
podman exec fridge-controller-test systemctl is-enabled fridge-controller.service
podman exec fridge-controller-test journalctl --no-pager -u fridge-controller.service -n 30
podman exec fridge-controller-test stat -c '%U:%G %a' /home/josias/fridge-controller/mqtt-password
podman exec fridge-controller-test systemctl show fridge-controller.service -p MainPID -p NRestarts
podman exec fridge-controller-test ansible-playbook -i tests/inventory.yml playbooks/controller.yml -e @inventory/host_vars/jpi4.yml
podman exec fridge-controller-test systemctl show fridge-controller.service -p MainPID -p NRestarts
```

Expect the initial running/stopped and enabled/disabled states to be preserved,
and password metadata to be josias:josias 600. A fresh container has no controller
service initially; deployment installs it but leaves it inactive and disabled.
For an initially running service, expect a log confirming MQTT connection.
The second deployment must report changed=0 and leave MainPID and NRestarts
unchanged. A successful systemctl query alone does not prove ongoing health.

Test both running and stopped services with a change that triggers notification,
and verify that only the initially running service restarts. Check boot
enablement independently in both cases. Repeat without changes to verify
idempotence. These scenarios require explicit service-state changes only
inside the disposable test container.

These tests cover deployment and basic service handling, not Shelly replies,
sensor behavior, or physical compressor safety. QEMU containers share the
workstation kernel and do not reproduce the Raspberry Pi kernel or hardware.
An image rebuild is needed after changing repository sources.

## Cleanup

This stops the test controller and broker and removes their container data.
The test image remains cached. Run only when the results are no longer needed.

```bash
podman stop fridge-controller-test
podman rm fridge-controller-test
```
