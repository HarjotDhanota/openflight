"""Render every frame of the real capture with club + ball outlines drawn on it.

The capture archive is CAMERA-ONLY: no I/Q, no IWR frames, no range, no radar of
any kind. `pre_trigger_count` and `trigger_host_timestamp_ns` come from the
SEN-14262 ACOUSTIC trigger on BCM17.

That trigger LAGS true impact. At the trigger frame the ball is already ~48 px
off the tee, which back-extrapolates to impact at least 4.7 frames (>=10 ms)
earlier. Sound needs 4.2 ms to cross 1.43 m and the rest is host-side latency.
Do not treat the trigger frame as the impact frame.

Honest overlay: what the detectors actually output, frame by frame, including the
frames where they fail. Nothing is hand-drawn and nothing is hidden.
"""

from __future__ import annotations

import json
import pathlib
import sys

import cv2
import numpy as np

sys.path.insert(0, r"C:\Users\harjo\Desktop\Coding\OpenFlight\openflight\research")
from silhouette_poc.fusion.ball_detect import find_teed_ball  # noqa: E402
from silhouette_poc.fusion.solver import CAMERA_CENTER_WORLD, _ray_world  # noqa: E402
from silhouette_poc.generator.mesh_truth import TriangleMesh  # noqa: E402
from silhouette_poc.replay.fit_real import (  # noqa: E402
    fit_frame_6dof,
    measured_camera,
    render_mask_6dof,
)

OUT = pathlib.Path(__file__).parent
SCALE = 2
TEE = (80, 120, 220, 190)

CLUB = (150, 120, 255)  # projected 3D mesh (BGR)
OBSERVED = (60, 220, 255)  # the observed silhouette the fit consumes


def _mesh_path() -> pathlib.Path:
    """Locate the mesh whether run from the repo or a scratch copy."""
    here = pathlib.Path(__file__).resolve()
    for base in (here.parents[1], *(p for p in here.parents)):
        cand = base / "meshes" / "assets" / "poc_7iron.npz"
        if cand.exists():
            return cand
    import silhouette_poc

    return pathlib.Path(silhouette_poc.__file__).parent / "meshes" / "assets" / "poc_7iron.npz"


MESH_PATH = _mesh_path()
BALL = (200, 235, 120)  # teal
GHOST = (120, 120, 120)


def deflicker(frame, background):
    """Undo the 120 Hz mains ripple before differencing.

    The room lights flicker (measured: a 4-frame / 8.6 ms period). On the clipped
    mat that is invisible, but on the unclipped wall it produces frame-to-frame
    swings that a plain background subtraction reports as large moving regions.
    Left uncorrected it painted "club" outlines on empty frames.
    """
    f = frame.astype(np.float32)
    ref = background[:100, :]
    cur = f[:100, :]
    m = (ref < 250) & (cur < 250)  # unclipped wall only
    if m.sum() < 500:
        return f
    gain = float(np.median(ref[m]) / max(np.median(cur[m]), 1e-3))
    return f * float(np.clip(gain, 0.5, 2.0))


def club_mask(frame, background, noise, tee_xy=None, min_area=120, max_dist=130.0):
    """Moving region that is plausibly the club: dark against the lit mat.

    `min_area` is deliberately low. As the clubhead nears impact its face turns
    toward the light and saturation eats into it: the non-saturated part of the
    impact zone falls from 413 px at F70 to 53 px at F76 on the reference capture.
    A 350 px gate rejected genuine 250-300 px clubheads in exactly the six frames
    closest to impact - the ones that matter most. 120 px recovers all of them;
    below ~100 px noise starts winning instead.
    """
    d = background.astype(np.float32) - deflicker(frame, background)
    thr = max(4.0 * noise, 30.0)
    m = (np.abs(d) > thr).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, lab, st, cent = cv2.connectedComponentsWithStats(m, 8)
    H, W = frame.shape
    out = []
    for j in range(1, n):
        x, y, w, h, a = st[j]
        if a < min_area or a > 0.35 * H * W:
            continue
        if w > 0.75 * W:
            continue
        if cent[j][1] < 0.35 * H:  # the club sweeps low, along the mat
            continue
        out.append(((lab == j).astype(np.uint8), np.array(cent[j], dtype=float), int(a)))
    return out


def track_club(frames, backgrounds, noises, level_pre, tee_xy, gate_px=34.0, ball_track=None):
    """Follow ONE clubhead once it is acquired, instead of re-choosing each frame.

    Re-selecting the component nearest the tee every frame let the outline jump
    onto the ball: after impact the ball is the thing closest to the tee, so a
    proximity rule hands the club's identity straight to it. A real clubhead has
    continuity - it arrives, sweeps through and leaves - so the track is seeded
    near the tee and then propagated by motion, and it is allowed to end.
    """
    # Veto anything sitting on the AIRBORNE ball. Once struck, the ball is a moving
    # blob of similar size to the shrinking clubhead, and it sits closer to the
    # tracker's prediction - so the club outline transferred onto it for several
    # frames and then jumped back. The two trackers must be mutually exclusive.
    ball_track = ball_track or {}

    per_frame = []
    for i, frame in enumerate(frames):
        same = abs(float(frame.mean()) - level_pre) < 25.0
        cands = club_mask(frame, backgrounds[same], noises[same], tee_xy=tee_xy)
        # Only veto once the ball has SEPARATED from the club. At impact the two
        # occupy the same place, so an unconditional veto deletes the clubhead too
        # and the track dies at F69.
        if i in ball_track and tee_xy is not None and ball_track[i][1] < tee_xy[1] - 25.0:
            bx, by, br = ball_track[i]
            veto_px = max(2.5 * br, 12.0)
            cands = [c for c in cands if float(np.hypot(c[1][0] - bx, c[1][1] - by)) > veto_px]
        per_frame.append(cands)

    # Seed where the clubhead is CLEAREST, not at the first frame that happens to
    # contain a blob - acquiring on the first hit locked onto early sensor noise.
    seed, seed_area = None, 0
    for i, cands in enumerate(per_frame):
        for mask, cen, area in cands:
            if np.linalg.norm(cen - tee_xy) <= 130.0 and area > seed_area:
                seed, seed_area = i, area
    if seed is None:
        return {}

    tracked: dict[int, np.ndarray] = {}

    def walk(order):
        pos = vel = None
        misses = 0
        for i in order:
            cands = per_frame[i]
            if pos is None:
                near = [c for c in cands if np.linalg.norm(c[1] - tee_xy) <= 130.0]
                if not near:
                    return
                mask, cen, _ = max(near, key=lambda c: c[2])
                tracked[i], pos, vel = mask, cen, np.zeros(2)
                continue
            if not cands:
                misses += 1
                if misses >= 3:
                    return
                continue
            pred = pos + vel
            pool = [c for c in cands if np.linalg.norm(c[1] - pred) <= gate_px + 12.0 * misses]
            if not pool:
                misses += 1
                if misses >= 3:
                    return
                continue
            mask, cen, _ = min(pool, key=lambda c: float(np.linalg.norm(c[1] - pred)))
            # FAIL CLOSED when the clubhead has no contrast left to track. As the face
            # turns toward the light its brightness converges on the saturated mat: on
            # the reference capture the impact region falls from 550 px of >30 DN
            # contrast at F68 to 19-80 px by F73. Past that there is nothing to track,
            # and the tracker was latching onto mat-edge artifacts and reporting them
            # as the club. Better to stop than to draw an outline on the wrong object.
            cy, cx = int(round(cen[1])), int(round(cen[0]))
            y0, y1 = max(0, cy - 18), min(frames[i].shape[0], cy + 18)
            x0, x1 = max(0, cx - 35), min(frames[i].shape[1], cx + 35)
            region = np.abs(
                frames[i][y0:y1, x0:x1].astype(np.float32) - backgrounds[True][y0:y1, x0:x1]
            )
            if int((region > 30.0).sum()) < 170:
                return
            if misses == 0:
                vel = cen - pos
            tracked[i], pos, misses = mask, cen, 0

    walk(range(seed, len(per_frame)))  # forward through impact
    walk(range(seed, -1, -1))  # backward into the downswing
    return tracked


def main():
    # every member is a plain numeric array, so no pickle deserialisation is needed
    z = np.load(r"C:\Users\harjo\Downloads\frames.npz")
    F = z["frames"]
    st = z["sensor_timestamp_ns"].astype(float)
    impact = int(z["pre_trigger_count"])
    t0 = st[impact]

    # two background models: the illuminator goes out at frame 79
    bg_pre = np.median(F[10:60].astype(np.float32), axis=0)
    noise_pre = float(F[10:60].astype(np.float32).std(axis=0).mean())
    bg_post = np.median(F[84:99].astype(np.float32), axis=0)
    noise_post = float(F[84:99].astype(np.float32).std(axis=0).mean())
    level_pre = float(F[10:60].mean())

    try:
        teed = find_teed_ball(F, impact, TEE)
        teed_c, teed_r = teed.center, teed.radius_px
    except Exception as exc:  # noqa: BLE001
        teed, teed_c, teed_r = None, None, None
        print("teed ball not found:", exc)

    # PASS 1 - track the ball AWAY from the tee with a motion gate.
    # Picking the roundest candidate per frame is not tracking: it locks onto fixed
    # scenery. Seed at the teed position, predict, gate, and be willing to LOSE it.
    departure = impact

    track_xy: dict[int, tuple[float, float, float]] = {}
    ball_back: dict[int, tuple[float, float, float]] = {}
    if teed_c is not None:
        last_y, misses = float(teed_c[1]), 0
        for i in range(departure, len(F)):
            if abs(float(F[i].mean()) - level_pre) >= 25.0:
                print(
                    f"ball tracking stops at F{i}: the tee light went out, so the "
                    f"pre-impact background model no longer applies"
                )
                break
            d = F[i].astype(np.float32) - bg_pre  # airborne ball is BRIGHTER
            m = (d > 45.0).astype(np.uint8)
            m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
            n, lab, cstats, cent = cv2.connectedComponentsWithStats(m, 8)
            pool = []
            for j in range(1, n):
                x, y, w, h, a = cstats[j]
                if not 12 <= a <= 400 or max(w, h) / max(min(w, h), 1) > 2.5:
                    continue
                cx, cy = cent[j]
                if abs(cx - teed_c[0]) > 30.0 or cy > last_y + 6.0 or cy < 10.0:
                    continue
                pool.append((a, cx, cy, 0.5 * (w + h) / 2.0))
            if not pool:
                misses += 1
                if misses >= 2:
                    break
                continue
            a, cx, cy, r = max(pool)
            track_xy[i] = (float(cx), float(cy), float(max(r, 2.0)))
            last_y, misses = float(cy), 0
    # The acoustic trigger lags impact by ~5 frames, so the measured flight track
    # starts late and the club tracker was left unprotected exactly when the ball was
    # climbing past it - it transferred onto the ball at F73 and stayed there.
    #
    # Absence-based and brightness-based departure detection both failed here (F65,
    # four frames early - the club occluding the ball, and its own specular flash).
    # The measured flight track is unambiguous, so extrapolate THAT backwards along
    # its own fitted line to cover the frames the trigger missed.
    if len(track_xy) >= 3:
        idx = np.array(sorted(track_xy), dtype=float)
        xy = np.array([track_xy[int(i)] for i in idx])
        fy = np.polyfit(idx, xy[:, 1], 1)
        fx = np.polyfit(idx, xy[:, 0], 1)
        fr = np.polyfit(idx, xy[:, 2], 1)
        first = int(idx[0])
        for j in range(first - 1, max(first - 8, 0), -1):
            y = float(np.polyval(fy, j))
            if y >= teed_c[1] - 4.0:  # back at the tee: before departure
                break
            ball_back[j] = (float(np.polyval(fx, j)), y, max(float(np.polyval(fr, j)), 2.0))
        if ball_back:
            print(
                f"ball back-extrapolated over F{min(ball_back)}-F{max(ball_back)} "
                f"to cover the trigger lag"
            )

    print(f"ball tracked in flight for {len(track_xy)} frames after impact")

    # PASS 2 - follow ONE clubhead by motion continuity. Selecting the component
    # nearest the tee every frame handed the club's identity to the ball as soon
    # as the ball became the closest thing to the tee.
    masks = track_club(
        F,
        {True: bg_pre, False: bg_post},
        {True: noise_pre, False: noise_post},
        level_pre,
        teed_c if teed_c is not None else np.array([160.0, 150.0]),
        ball_track={**ball_back, **track_xy},
    )
    print(f"club tracked for {len(masks)} frames: {sorted(masks)}")

    # Fit the actual 3D CAD mesh to each tracked silhouette. What gets DRAWN is the
    # projected mesh at the fitted pose - the model's own output - not the contour of
    # a background-subtraction blob. The blob is the observation the fit consumes; it
    # is not the system's answer, and drawing it as if it were overstates the result.
    mesh_contours: dict[int, np.ndarray] = {}
    mesh_iou: dict[int, float] = {}
    if MESH_PATH.exists():
        d = np.load(MESH_PATH)
        mesh = TriangleMesh(d["vertices_local_mm"], d["faces"], "poc_7iron", "x" * 64)
        cam = measured_camera(F.shape[2], F.shape[1])
        for i, m in sorted(masks.items()):
            fit = fit_frame_6dof(mesh, m, cam)
            if not fit["ok"]:
                continue
            ys, xs = np.nonzero(m.astype(bool))
            ray = _ray_world(np.array([xs.mean(), ys.mean()], dtype=float), cam)
            rendered = render_mask_6dof(
                mesh,
                CAMERA_CENTER_WORLD + ray * fit["range_mm"],
                fit["yaw_deg"],
                fit["pitch_deg"],
                fit["roll_deg"],
                cam,
            )
            if rendered is None:
                continue
            mesh_contours[i] = rendered
            mesh_iou[i] = fit["iou"]
        print(
            f"mesh fitted on {len(mesh_contours)}/{len(masks)} tracked frames; "
            f"median IoU {np.median(list(mesh_iou.values())):.3f}"
            if mesh_iou
            else "no mesh fits"
        )

    report = []
    frames_out = []
    for i, frame in enumerate(F):
        same_light = abs(float(frame.mean()) - level_pre) < 25.0

        vis = cv2.cvtColor(
            cv2.resize(frame, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_NEAREST),
            cv2.COLOR_GRAY2BGR,
        )
        row = {"frame": i, "t_ms": round((st[i] - t0) / 1e6, 2), "club": False, "ball": None}

        cm = masks.get(i)
        if cm is not None:
            cs, _ = cv2.findContours(
                cv2.resize(cm, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_NEAREST),
                cv2.RETR_EXTERNAL,
                cv2.CHAIN_APPROX_SIMPLE,
            )
            cv2.drawContours(vis, cs, -1, OBSERVED, 1, cv2.LINE_AA)
            row["club"] = True
            row["club_px"] = int(cm.sum())
            if i in mesh_contours:
                mc = cv2.resize(
                    mesh_contours[i].astype(np.uint8),
                    None,
                    fx=SCALE,
                    fy=SCALE,
                    interpolation=cv2.INTER_NEAREST,
                )
                mcs, _ = cv2.findContours(mc, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                cv2.drawContours(vis, mcs, -1, CLUB, 2, cv2.LINE_AA)
                row["mesh_iou"] = round(float(mesh_iou[i]), 3)

        # ball: the teed fit while it is still on the tee, then the moving blob
        if i < departure and teed_c is not None:
            cv2.circle(
                vis,
                (int(round(teed_c[0] * SCALE)), int(round(teed_c[1] * SCALE))),
                int(round(teed_r * SCALE)),
                BALL,
                2,
                cv2.LINE_AA,
            )
            row["ball"] = "teed"
        elif track_xy.get(i) is not None:
            bx, by, br = track_xy[i]
            cv2.circle(
                vis,
                (int(round(bx * SCALE)), int(round(by * SCALE))),
                int(round(br * SCALE)),
                BALL,
                2,
                cv2.LINE_AA,
            )
            row["ball"] = "flight"
            row["ball_r_px"] = round(br, 2)
            row["ball_xy"] = [round(bx, 1), round(by, 1)]
        else:
            row["ball"] = "lost"

        tag = f"F{i:02d}  {row['t_ms']:+7.2f} ms"
        cv2.putText(vis, tag, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (30, 30, 30), 3, cv2.LINE_AA)
        cv2.putText(
            vis, tag, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (245, 245, 245), 1, cv2.LINE_AA
        )
        if i == impact:
            cv2.putText(
                vis,
                "ACOUSTIC TRIGGER - not impact",
                (8, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                (30, 30, 30),
                3,
                cv2.LINE_AA,
            )
            cv2.putText(
                vis,
                "ACOUSTIC TRIGGER - not impact",
                (8, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                (74, 160, 224),
                1,
                cv2.LINE_AA,
            )
        if not same_light:
            cv2.putText(
                vis,
                "tee light OFF",
                (8, vis.shape[0] - 12),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                (30, 30, 30),
                3,
                cv2.LINE_AA,
            )
            cv2.putText(
                vis,
                "tee light OFF",
                (8, vis.shape[0] - 12),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                (110, 110, 235),
                1,
                cv2.LINE_AA,
            )

        frames_out.append(vis)
        report.append(row)

    sheet_cols = 10
    rows = int(np.ceil(len(frames_out) / sheet_cols))
    h, w = frames_out[0].shape[:2]
    sheet = np.zeros((rows * h, sheet_cols * w, 3), np.uint8)
    for k, v in enumerate(frames_out):
        r, c = divmod(k, sheet_cols)
        sheet[r * h : (r + 1) * h, c * w : (c + 1) * w] = v
    cv2.imwrite(str(OUT / "overlay_sheet.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 90])

    meta = {
        "n": len(frames_out),
        "w": w,
        "h": h,
        "cols": sheet_cols,
        "impact": impact,
        "rows": report,
    }
    (OUT / "overlay_meta.json").write_text(json.dumps(meta), encoding="utf-8")

    kb = (OUT / "overlay_sheet.jpg").stat().st_size / 1024
    club_n = sum(1 for r in report if r["club"])
    ball_n = sum(1 for r in report if r["ball"] in ("teed", "flight"))
    print(
        f"{len(frames_out)} frames, tile {w}x{h}, sheet {sheet.shape[1]}x{sheet.shape[0]}, {kb:.0f} KB"
    )
    print(f"club outlined in {club_n}/{len(report)} frames; ball in {ball_n}/{len(report)}")
    if teed is not None:
        print(f"teed ball: centre ({teed_c[0]:.2f},{teed_c[1]:.2f}) r={teed_r:.2f} px")


if __name__ == "__main__":
    main()
