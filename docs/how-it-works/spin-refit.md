---
icon: lucide/rotate-cw
---

# Spin refit (spin-refit-v1)

When the OPS243 gives no spin the kiosk accepts and the collision model has none, the
spin chain's last model route is a fitted kinematic model: **spin-refit-v1**. It is
fitted physics, labelled as a fitted model (D38). Each value says which model and
data built it, which inputs it stands on, and how wrong it is likely to be.

The OPS's own spin reading is not used as an input. Against a TM4 session on 12 Oct
2026 its median error was -17 % with an sd of 157 %, and it railed at 10,986 rpm.

## Where it runs

The spin chain (`server._apply_fallback_spin`, one `source_chain` resolution) takes
the first route that gives a value:

1. an accepted OPS spin (confidence at or above the floor): `measured`;
2. the collision model's spin: that model's label;
3. **the kinematic route, spin-refit-v1** (`spin_estimate.refit_route`): `calculated`
   or `estimated`, as below;
4. the OPS's own reading below the confidence floor: `experimental`.

The route is refused, with its reason kept in `Shot.input_sources["spin"]`, when the
shot has no club and no sensor launch, or when its speed or launch is outside the
fitted range (a 28 mph chip, for example).

## Tiers

Every tier is one power law, `spin = coefficient x speed x sin(angle)^exponent`,
fitted by least squares on `log(spin / speed)`. The route takes the first tier the
shot's inputs allow:

| Tier | Inputs | Model | 68 % range (all clubs) |
|---|---|---|---|
| `club_speed` | the club, a **calculated full** club speed | 101.4 x club speed x sin(nominal loft)^0.79 | x0.69 to x1.48 |
| `club` | the club, the ball speed (full, else OPS radial) | 86.2 x ball speed x sin(nominal loft)^0.93 | x0.70 to x1.57 |
| `launch` | no club: a sensor's vertical launch, the ball speed | 188.2 x ball speed x sin(launch)^1.2 | x0.64 to x1.46 |

- **The club** is the kiosk's club selection. Its nominal loft (`clubs.physics`)
  stands in for the spin loft. The fitted lofts run from 10.5 deg (driver) to 46 deg
  (PW). A club outside them (GW, SW, LW) is extrapolated: the value is `estimated`
  and its note says so.
- **The launch is not used once the club is known.** Fitted with the loft, its
  exponent was 0.10 (0 is no effect), and it lowered no held-out error. Without a
  club, the `launch` tier keeps the first fit's exponent, 1.2, and refits only the
  coefficient. That tier keeps the first fit's form, `170 v sin(LA)^1.2`, fitted to
  a 2026-06-08 session that is not in the repo.
- **Club speed** enters only as a calculated full club speed (`Shot.club_speed_full`).
  In the 6 May 2026 OpenFlight-against-TM comparison (`session_logs/comparison_20260506.csv`),
  the OPS radial club speed read 3-10 % low. That gap costs more than the 5-7 %
  lower held-out MAE that club speed buys. The kiosk does not compute a full club
  speed yet, so this tier does not run today.
- **The range** is the 16th to 84th percentile of the held-out ratio truth /
  prediction. A club tier uses its club family's range: wedge, iron (hybrids
  included) or wood. The `launch` tier uses the all-club range. The value is capped
  at 13,000 rpm.

## Radial and true speeds

The fit uses TM's speeds, which are true speeds. The OPS243 measures radial speed.
The two are never silently mixed:

- a **full ball speed** (`Shot.ball_speed_full_mph`) is used as it is. The value
  takes the full speed's label: `calculated` when the full speed is calculated,
  `estimated` when it rests on an estimated input;
- **without a full speed** the OPS radial ball speed is used as it is. It is never
  corrected by an assumed geometry. The value is `estimated`, and the top of its
  range is widened by the radial reading's largest shortfall, 6.1 %. That figure is
  the 95th percentile of `1 - speed_correction.radial_speed_factor` over the fitted
  shots, for a ball 2 m in front of the OPS and 0.15 m below it. In the 6 May
  comparison the OPS radial ball speed read 1.9-2.5 % below TM's;
- a radial club speed is never used.

## Labels and what the kiosk shows

The record's status is the weakest of the model and its inputs. Its source text
names the model, the data, the tier and the speed it stood on, for example:

    estimated (model: spin refit v1, TM exports; club tier: 86.2 x ball speed x
    sin(loft)^0.93; on the OPS radial ball speed (no full speed))

On an OPS-only unit the spin tile reads **Estimated**. With a calculated full ball
speed it reads **Calculated**. `shot_metrics` then lowers it to the weakest input's
label, as for every model output. On a camera-fallback launch, for example, it
reads **Experimental**.

The 68 % range is kept in `Shot.spin_rpm_range` and in the spin record's `range`.
`Shot.input_sources["spin"]` holds the route, its label, its source, the range's
half-width as `uncertainty`, and every route refused before it. The OPS's own
reading is kept in `spin_rpm_measured` and `spin_method_measured`.

## Data

The fit uses TM exports' measured outputs only: club, ball speed, club speed,
vertical launch and total spin. For the comparators it also uses attack angle,
path and horizontal launch. Provenance, with each file's sha256, is in
`src/openflight/spin_refit_v1.json`.

| Export | Session | Shots | In the fit |
|---|---|---|---|
| `session_logs/OpenFlight-Test.Normalized.csv` (in the repo) | 6 May 2026: 7-iron, driver, PW | 24 | 22 |
| `tm4-public-swingsync-2026-10-12.csv` (beside the checkout) | 12 Oct 2026: PW, 7-iron, 6-iron | 43 | 36 |
| `swingsync-2026-10-04.csv` (test-bench archive) | 4 Oct 2026: PW to driver | 63 | 54 |

- **The fit's domain** is decided from the inputs alone, before any residual is
  seen: a spin TM measured (two 6 May driver spins TM estimated are left out), ball
  speed at least 50 mph, vertical launch at least 5 deg, and no topspin.
- **The held-out error** is counted over every TM-measured shot the tier would run
  on, mishits included.
- **Total spin** on a SwingSync export is the root-sum-square of its back and side
  spin.
- **Duplicate exports** with the same bytes, such as one session saved under two
  names, are read once.

## Held-out error

Cross-validation leaves out one whole session at a time. One export is one session
and one player, so every shot is predicted by a fit that never saw that player.
Leave-one-club-out shows whether the nominal loft carries the model to clubs it
never saw.

The table compares the shots every comparator can predict. The collision model and
Penner 2001 run on TM's attack angle and path, inputs the fallback does not have.
They are a ceiling, not alternatives.

| | all (n=125) MAE / median abs | wedge (46) | iron (59) | wood (20) |
|---|---|---|---|---|
| `club` tier, held out | 1556 rpm / 26.0 % | 1807 / 27.5 % | 1155 / 16.3 % | 2164 / 58.2 % |
| `club_speed` tier, held out | 1478 / 25.9 % | 1698 / 27.4 % | 1069 / 12.9 % | 2178 / 59.2 % |
| first fit, `170 v sin(LA)^1.2` | 1799 / 29.6 % | 2197 / 35.4 % | 1432 / 18.8 % | 1970 / 52.8 % |
| collision model (TM attack, path) | 1283 / 20.8 % | 1360 / 14.6 % | 1218 / 24.0 % | 1296 / 25.7 % |
| Penner 2001 (TM attack, path) | 1266 / 21.3 % | 1521 / 23.9 % | 1060 / 14.9 % | 1286 / 30.1 % |

On the shots where the `launch` tier runs (n=116, launch 5-35 deg), it reaches
1575 rpm / 26.3 % against the first fit's 1632 / 28.4 %.

The median signed error of the `club` tier is -3 % overall, -19 % on wedges and
-25 % on woods. The mean is pulled up by thin strikes whose true spin is a few
hundred rpm. The per-family, leave-one-club-out and comparator numbers are recorded
in the data file.

## Limits

- **Three sessions, at most three players.** The players differ more than the
  clubs do. One player's 6 May PW spun 8,400 rpm; the October players' PW spun
  1,500-7,800 rpm at similar launch. The fitted exponent moves from 0.65 to 1.26
  between folds.
- **Woods are the weakest family.** The data holds 20 wood shots from two players,
  and their 68 % range runs from x0.59 to x2.5. Wedges are under-predicted by about
  20 % at the median.
- **Attack angle is not an input.** On these players it moves wedge spin by a
  factor of two at the same launch, so the shot-to-shot error is large however the
  model is fitted.
- **Chips get no refit spin.** Below 50 mph ball speed the route is refused, with
  its reason kept. Lofts beyond the fitted 46 deg are extrapolated, and the value
  says so.

## Refitting

    uv run python scripts/analysis/fit_spin_refit.py            # report
    uv run python scripts/analysis/fit_spin_refit.py --explore  # the launch's weight with the club
    uv run python scripts/analysis/fit_spin_refit.py --write    # rewrite spin_refit_v1.json

A new export is passed on the command line. Exports beside the checkout are read
from there and never copied into the repo. A refit that changes the coefficients is
a new version, `spin-refit-v2`, with its own data file.
