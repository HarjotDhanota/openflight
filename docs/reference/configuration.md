---
icon: lucide/file-cog
---

# Configuration Files

Settings that live in files rather than on the command line.

## `config/sim.json`

Simulator connectors. Not tracked in git — copy the example and edit:

```bash
cp config/sim.example.json config/sim.json
```

```json
{
  "connectors": [
    {
      "type": "opengolfsim",
      "enabled": false,
      "host": "127.0.0.1",
      "port": 3111,
      "device_id": "OpenFlight"
    },
    {
      "type": "gspro",
      "enabled": false,
      "host": "127.0.0.1",
      "port": 921,
      "device_id": "OpenFlight",
      "units": "Yards",
      "heartbeat_interval_s": 5
    }
  ]
}
```

| Key | Meaning |
| --- | --- |
| `type` | Connector to use — `opengolfsim`, `gspro`, `partee` |
| `enabled` | Whether this connector is active |
| `host`, `port` | Where the simulator is listening |
| `device_id` | Identifier the simulator displays |
| `units` | `Yards` or `Meters` (GSPro) |
| `heartbeat_interval_s` | Keepalive cadence (GSPro) |

Enable connectors at runtime with `--sim`. See
[simulator connectors](../using/simulator/index.md).

## `~/.config/openflight/cloud.json`

Cloud sync credentials and endpoint. Mode `0600` — it holds a device token.

Created by the device-linking flow rather than by hand; see
[cloud sync](../using/cloud-sync.md#linking-a-device) and the
[wire contract](cloud-uploader-spec.md#6-config-configopenflightcloudjson-mode-0600).

## `~/.config/openflight/setup.json`

The unit's setup record, and the only way a setup reaches the kiosk: the ball box,
the aim (the target line every horizontal angle is measured from), the resting
ball's range, the net range and the scene (the lens height above the hitting
surface). The setup screen writes it through the kiosk's `/api/setup/*` actions, and
the kiosk applies each change between two shots, without a restart;
[`--setup-file`](cli.md#unit-setup) names another file.

Each section keeps its `source` and its uncertainty, and the inputs it was confirmed
against: the lens, the rig file and the camera tilt for the box, the box for the
aim, ball range and scene. At start, a section whose inputs no longer match is
marked stale with the reason and is not applied. A section never set leaves the
kiosk's own start values: no box, the unit-boresight frame, each shot ranged from
its own capture without a prior, the 4.6 m net labelled `default_not_measured`, and
the rig file's lens height. A missing or unreadable file starts with nothing set.

## IWR6843 radar configs

`config/iwr6843_l3dump_*.cfg` — TI RF configuration matching the flashed L3
firmware. Selected with `--iwr6843-config`.

| File | Profile |
| --- | --- |
| `iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg` | Wide, 24 frames × 3 ms, 53 bins, IQ16. **Default.** |
| `iwr6843_l3dump_dense_36f2ms_53bin_iq8.cfg` | Dense, 36 frames × 2 ms, 53 bins, IQ8 |
| `iwr6843_l3dump_dense_36f2ms_53bin_iq8_wide_late.cfg` | Experimental dense IQ8 profile that keeps the wide profile's late-flight window |

All three work with the same flashed image — the profile is selected at runtime, not
at flash time. See
[choosing a profile](../iwr6843/index.md#choose-a-profile).

## `config/iwr6843_calibration_reference.json`

Per-board complex array and range calibration. Selected with `--iwr6843-cal`.

| Key | Meaning |
| --- | --- |
| `range_scale`, `range_offset_m`, `range_bias_const_m` | Range correction |
| `elem_phase_rad` | Per-element phase correction, 8 elements |
| `elem_gain` | Per-element gain correction, 8 elements |
| `convention` | How the correction is applied to the array |

The shipped file is a **validated starting point**, not a guarantee — per-board
calibration may be required. Its `tilt_deg` and `radar_height_m` are not used:
the swing server takes the radar's tilt and height from the enclosure's rig file
(`--rig-geometry`).

## `config/lenses.json`

The lenses the unit can carry (stock 2.8 mm, 6 mm, 8 mm): focal length, aperture,
field of view, provenance, and the exposure rungs each ladder mode allows. A lens's
nominal focal length is the rig file's scaled by `focal_mm / 2.8`; a checkerboard
calibration for its `lens_id` replaces it. The unit's choice and each lens's own
modes, default mode and ladder are kept in `~/.config/openflight/camera-lens.json`.
See [the fixed box](../camera/tester-fixed-box.md#the-lens).

## `config/swing_exposure.json`

The kiosk's automatic swing exposure. It holds the blur budget (4 mm on approach,
1 mm near impact), the measured club-edge speeds (13.6 and 3.3 m/s), each mode's
blur ceiling (294 µs) and signal factor (640×400 is 2.00× 1280×800, measured), and
the derivation of each. A stated ceiling that disagrees with its budget and speeds
is refused. The sensor's shortest exposure is not written here: it is read from the
camera at runtime and recorded in `~/.config/openflight/camera-sensor-limits.json`.
See [automatic exposure](../camera/README.md#automatic-exposure).

## `config/placement_box.json`

The tester's fixed placement box: the placement window (`placement_window_m`,
1.8–2.9 m forward of the lens), its half width, the tilt margins it is drawn with
(uncalibrated 3°, calibrated 1°), the distance windows' pads, the net margin and
the size window's reach past the window. A malformed file is refused, never
defaulted. See [the fixed box](../camera/tester-fixed-box.md).

## `config/alloy.alloy`

Grafana Alloy pipeline for shipping session logs. See
[observability](../using/observability.md).

## `config/credentials.env.example`

Template for Grafana Cloud credentials. Copy to `credentials.env` — which is
gitignored — and fill in.

## Related

- [CLI flags](cli.md)
- [Constants](constants.md)
