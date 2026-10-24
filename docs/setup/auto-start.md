---
icon: lucide/power
---

# Auto-Start & Kiosk Mode

Run OpenFlight as a systemd service so it comes up on boot, and drive the
display in fullscreen kiosk mode.

## Auto-start on boot

The setup script installs and enables a systemd service configured for your
username and install path.

The service runs `scripts/start-kiosk.sh` with no arguments. It finds the
OPS243 on its own: a radar on USB first, then one on the Pi GPIO UART
(`/dev/ttyAMA0`, the J3 wiring in the [UART guide](../build/ops243-uart.md)),
used only when the radar answers its version query there. To name the port
yourself, see [Setting the radar port](#setting-the-radar-port).

If a start fails, systemd retries it every 5 s, at most five times in five
minutes, then stops. The journal shows the launcher's error, then
`Start request repeated too quickly`. Fix the cause, then re-arm the service:

```bash
journalctl -u openflight -n 60 --no-pager        # why it failed
sudo systemctl reset-failed openflight.service
sudo systemctl start openflight.service
```

<details>
<summary>Manual steps and service management</summary>

```bash
# Install, rewriting User= and the paths for this Pi (what setup.sh does)
sed -e "s|^User=.*|User=$USER|" \
    -e "s|/home/coleman/openflight|$HOME/openflight|g" \
    ~/openflight/scripts/setup/openflight.service \
    | sudo tee /etc/systemd/system/openflight.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable openflight
sudo systemctl start openflight
```

Management:

```bash
sudo systemctl status openflight --no-pager   # Check status
journalctl -u openflight -f                   # View logs
sudo systemctl stop openflight                # Stop until the next boot
sudo systemctl restart openflight             # Restart
sudo systemctl disable --now openflight       # Stop now and turn off auto-start
```

To modify the service, use a drop-in, which survives reinstalling the unit:

```bash
sudo systemctl edit openflight
sudo systemctl restart openflight
```

</details>

### Updating a unit installed before October 2026

Units installed before the restart limits were added retry forever, running
`uv sync` and grabbing the hardware on every attempt. `systemctl daemon-reload`
alone does not help, because the installed file is a copy. Reinstall it with
the `sed ... | sudo tee` command above (or rerun `scripts/setup/setup.sh` and
answer yes to auto-start), then:

```bash
sudo systemctl daemon-reload
sudo systemctl reset-failed openflight.service
grep StartLimit /etc/systemd/system/openflight.service   # expect two lines
```

### Setting the radar port

Detection covers the usual wirings. To name the OPS243 port explicitly (it
always wins over detection), set `OPENFLIGHT_OPS_PORT` in a drop-in:

```bash
sudo systemctl edit openflight
```

```ini
[Service]
Environment=OPENFLIGHT_OPS_PORT=/dev/ttyAMA0
```

```bash
sudo systemctl restart openflight
```

On the command line, `./scripts/start-kiosk.sh --radar-port /dev/ttyAMA0` does
the same and beats the environment variable. On a Pi 5 use `/dev/ttyAMA0`, not
`/dev/serial0`, which points at the separate debug-header UART.

### A unit used with the tester

The tester and the kiosk service both want the camera, the radars and the
Python environment, and `start-tester.sh` refuses to start while the service
is running. `stop` only lasts until the next boot, so on a unit used with the
tester turn the service off for good:

```bash
sudo systemctl disable --now openflight.service
```

Turn it back on with `sudo systemctl enable --now openflight.service`.

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

