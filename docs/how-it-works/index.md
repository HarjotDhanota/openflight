---
icon: lucide/brain
---

# How It Works

The measurement chain, from a strike to a carry number, and the honest limits of
each stage.

<div class="grid cards" markdown>

- :material-sitemap-outline: **[Measurement pipeline](pipeline.md)**

    Components, the shared trigger edge, and how the two radars are correlated.

- :material-waveform: **[Rolling buffer & spin](rolling-buffer.md)**

    What is inside a capture, how ball and club speed are separated, and why
    spin is still experimental.

- :material-speedometer: **[Full ball speed](full-ball-speed.md)**

    The OPS radial reading turned into the ball's full speed with the shot's
    own measured geometry, and when it is refused.

- :material-angle-acute: **[Launch angle](launch-angle.md)**

    LCMF-v1 over the IWR6843 raw radar cube, and the July 2026 TrackMan
    baseline.

- :material-chart-bell-curve: **[Ballistics & carry](ballistics.md)**

    Drag, Magnus, and the RK4 integration that produces carry distance.

- :material-golf: **[Club delivery arc](club-arc.md)**

    Club path, attack angle, swing plane, swing direction and low point from a
    circular arc, labelled calculated or model geometry.

- :material-vector-triangle: **[Collision model](collision-model.md)**

    Face angle, dynamic loft, spin loft, a calculated spin and axis, and the
    spin and smash indices from published physics (Penner 2001, Cross & Nathan
    2007) with constants fitted by the maintainer, labelled calculated.

- :material-map-marker-radius-outline: **[Radar positioning](positioning.md)**

    Where each radar goes and why the geometry matters.

</div>

## In one paragraph

A sound detector hears the strike and sends one electrical edge to two places at
once: the OPS243's `HOST_INT` pin, which freezes its rolling buffer of raw I/Q
samples, and the Pi's BCM17, which asks the IWR6843 to dump its rolling frame
ring. OpenFlight then does its own signal processing on both captures — an FFT
over short windows for speed, and LCMF-v1 over the radar cube for angle — merges
them on the OPS impact timestamp, and runs the result through a ballistic
simulation to get carry.

## What is trusted

Not every number the system produces is used for physics.

| Measurement | Status |
| --- | --- |
| Ball speed, club speed | Trusted |
| Launch angle, launch direction | Trusted within the estimator's stated limits |
| Club path | Reported, experimental |
| Spin rate | **Experimental** — recorded, not used for carry by default |

Carry uses trusted measurements and fills anything missing with documented
fallbacks rather than guessing silently. When spin is absent, the simulator uses
a kinematic estimate derived from ball speed and launch angle; `--calculated-spin`
makes that substitution explicit even when a measured value exists.
