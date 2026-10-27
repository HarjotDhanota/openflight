---
icon: lucide/power
---

# Auto-Start & Kiosk Mode

Run OpenFlight as a systemd service so it comes up on boot, and drive the
display in fullscreen kiosk mode.

## Auto-start on boot

The setup script installs and enables a systemd service configured for your
username and install path.

For an OPS243 connected to a specific serial port, run
`sudo systemctl edit openflight.service` and add:

```ini
[Service]
Environment=OPENFLIGHT_OPS_PORT=/dev/ttyAMA0
```

Then restart the service. An explicit `--radar-port` or `--ops-port` argument
overrides this setting. Leave it unset to use automatic port detection.

<details>
<summary>Manual steps and service management</summary>

```bash
# Install (adjust User= and paths in the file if your username isn't the default)
sudo cp ~/openflight/scripts/setup/openflight.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable openflight
sudo systemctl start openflight
```

Management:

```bash
sudo systemctl status openflight --no-pager   # Check status
journalctl -u openflight -f                   # View logs
sudo systemctl stop openflight                # Stop
sudo systemctl restart openflight             # Restart
sudo systemctl disable openflight             # Disable auto-start
```

To modify the service:

```bash
sudo nano /etc/systemd/system/openflight.service
sudo systemctl daemon-reload
sudo systemctl restart openflight
```

</details>

## Kiosk mode (fullscreen, recommended)

```bash
./scripts/start-kiosk.sh        # Default: rolling buffer + sound trigger
./scripts/start-kiosk.sh --mock # Mock mode (no hardware needed)
```

Use the [IWR6843 Operator Guide](../iwr6843/verify.md#start-openflight) or
[Legacy K-LD7 Setup](../legacy/index.md) for angle-radar startup commands.

## Manual start

```bash
openflight-server                # With radar
openflight-server --mock         # No hardware
```

Then open `http://localhost:8080`.

## Running over SSH

```bash
DISPLAY=:0 ./scripts/start-kiosk.sh
```

