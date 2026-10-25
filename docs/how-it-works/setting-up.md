---
icon: lucide/crosshair
---

# Setting Up

The kiosk's **Setup** screen tells the unit where the ball is, where you are aiming and
how far away the ball sits. You tap the camera view and press **Confirm**. You type
nothing, and the unit needs no restart.

Open it from **Finish setup** on the Live screen, which shows while a step is still to do,
or from **Setup** in the bottom bar. A left-handed profile shows the camera view
mirrored, so the picture matches what you see on the range.

Right-handed is the default. To change it, tap **Change club** and then **Right-handed** or
**Left-handed** at the top of the club picker. The change applies from the next shot, with
no restart. Each profile keeps its own hand. While a connected simulator says which hand,
its hand wins and the picker shows "Set by the simulator".

Put the unit behind the ball, pointing down your target line, then work through four
steps. **Back** and **Next** move between them.

## The four steps

### 1. Ball in the box

The camera view shows a fixed box. Put one ball anywhere in it and wait for **Ball
found in the box**, then tap **Confirm**. The unit looks for the ball only inside this box.
The aim and the ball range belong to the box, not to the ball's spot in it: move the ball
within the box and neither needs redoing.

| What it says | What to do |
| --- | --- |
| **The ball is outside the box. Move it in.** | Move the ball into the box. |
| **No ball seen. Put a ball in the box.** | Put a ball in the box, or add light. |
| **More than one ball in the box. Keep only one.** | Take the extra balls out. |

### 2. Aim

Tap a point straight down your target line: a mark on the net or screen, or a flag. The
unit draws the line back on the view. Every left and right angle is measured from it.
**Confirm** keeps it. **Skip** leaves left and right measured from where the unit points,
and the screen says so.

Optionally, switch **What a tap marks** to **Floor line** and tap two points where the
floor meets the net. The unit turns the line into a distance to the net. **Clear floor
line** removes it.

!!! note "The net distance is 4.6 m unless you mark the floor line"

    Without a floor line the net distance is the default **4.6 m**. The Ready check
    shows it as **Not measured**. With a floor line it shows the measured distance and
    its spread, **from the floor line**. A floor line the unit cannot use (above the
    horizon, nearer than the ball, too close to the horizon or too uncertain) is not
    used, and the screen says why.

### 3. Ball range

The unit measures the distance to the ball from the camera, with the ball still in the
box. It shows the result with its spread and where it came from, for example **from
the camera; the radar refines it each shot**. The camera range is a starting value; the
radar measures the range again on every shot. **Measure again** repeats it.

### 4. Ready check

One row each for the ball box, aim, ball range, net and lens height, with a badge and
a plain note:

| Badge | Meaning |
| --- | --- |
| **Confirmed** or **Set** | Done, and in effect. |
| **Skipped** | You skipped it; the screen says what that means. |
| **Not measured** | The unit uses a labelled default (the 4.6 m net, the enclosure's lens height). |
| **Not set** | Not done yet. |
| **Needs redoing** | It was done, then something it depended on changed. The note says what. |

A step you have not done does not stop the kiosk. It runs and labels what is missing.

## The ready light

The kiosk's ready light holds back for setup in two ways:

- **Red, NOT READY:** setup is not finished (a step is not set or needs redoing), or a
  step was saved but the unit failed to apply it. The reason names the step.
- **Amber, WAIT:** the box is confirmed but no ball is in it. Put a ball in the box.

## What makes a step go stale

A step is made against the unit as it was. Change one of these and the steps that
depended on it show **Needs redoing**, with the reason:

- the **lens**: the box, aim and range;
- the **enclosure** (rig file): the same;
- the **camera tilt**, by more than the box allows;
- the **box**: the aim and the ball range.

Moving the ball within the box is not a change: a ball anywhere in it is fine.

A stale step is not used. Redo it from the Setup screen.

## Saved and re-applied

Each step is saved to the unit's setup record as soon as you confirm it, and applied
before the next shot. The kiosk reads the record at start and applies it again, so a
restart keeps your setup unless something above changed. Details of the file are in the
[configuration reference](../reference/configuration.md#configopenflightsetupjson), and
[`--setup-file`](../reference/cli.md#unit-setup) names another one.

## A unit without a camera

Setup needs the camera. A unit without one has no setup: the screen says **Setup needs the
camera, and this kiosk has none**, the ready light never asks for setup, and left and right
are measured from where the unit points.
