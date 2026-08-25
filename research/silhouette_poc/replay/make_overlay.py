"""Render every frame of the real capture with club + ball outlines drawn on it.

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

OUT = pathlib.Path(__file__).parent
SCALE = 2
TEE = (80, 120, 220, 190)

CLUB = (60, 220, 255)  # amber  (BGR)
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
    best, best_d = None, 1e9
    for j in range(1, n):
        x, y, w, h, a = st[j]
        if a < min_area or a > 0.35 * H * W:
            continue
        if w > 0.75 * W:
            continue
        if cent[j][1] < 0.35 * H:  # the club sweeps low, along the mat
            continue
        # Nearest to the ball, NOT largest. The golfer's leg and shoe are bigger
        # than the clubhead and were being outlined instead of it.
        d = (
            0.0
            if tee_xy is None
            else float(np.hypot(cent[j][0] - tee_xy[0], cent[j][1] - tee_xy[1]))
        )
        if tee_xy is not None and d > max_dist:
            continue
        if d < best_d:
            best, best_d = (lab == j).astype(np.uint8), d
    return best


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

    # PASS 1 - club masks. A real club sweep occupies CONSECUTIVE frames; isolated
    # single-frame hits at a fixed 7-frame spacing are a lighting beat, not a club.
    masks = []
    for i, frame in enumerate(F):
        same = abs(float(frame.mean()) - level_pre) < 25.0
        masks.append(
            club_mask(
                frame, bg_pre if same else bg_post, noise_pre if same else noise_post, tee_xy=teed_c
            )
        )
    keep = [False] * len(masks)
    for i in range(len(masks)):
        if masks[i] is None:
            continue
        prev = i > 0 and masks[i - 1] is not None
        nxt = i + 1 < len(masks) and masks[i + 1] is not None
        keep[i] = prev or nxt
    dropped = sum(1 for i, m in enumerate(masks) if m is not None and not keep[i])
    print(
        f"club: {sum(1 for m in masks if m is not None)} raw detections, "
        f"{dropped} dropped as isolated, {sum(keep)} kept"
    )

    # PASS 2 - track the ball AWAY from the tee with a motion gate.
    # Picking the roundest candidate per frame is not tracking: it locks onto fixed
    # scenery. Seed at the teed position, predict, gate, and be willing to LOSE it.
    track_xy: dict[int, tuple[float, float, float]] = {}
    if teed_c is not None:
        last_y, misses = float(teed_c[1]), 0
        for i in range(impact, len(F)):
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
    print(f"ball tracked in flight for {len(track_xy)} frames after impact")

    report = []
    frames_out = []
    for i, frame in enumerate(F):
        same_light = abs(float(frame.mean()) - level_pre) < 25.0

        vis = cv2.cvtColor(
            cv2.resize(frame, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_NEAREST),
            cv2.COLOR_GRAY2BGR,
        )
        row = {"frame": i, "t_ms": round((st[i] - t0) / 1e6, 2), "club": False, "ball": None}

        cm = masks[i] if keep[i] else None
        if cm is not None:
            cs, _ = cv2.findContours(
                cv2.resize(cm, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_NEAREST),
                cv2.RETR_EXTERNAL,
                cv2.CHAIN_APPROX_SIMPLE,
            )
            cv2.drawContours(vis, cs, -1, CLUB, 2, cv2.LINE_AA)
            row["club"] = True
            row["club_px"] = int(cm.sum())

        # ball: the teed fit while it is still on the tee, then the moving blob
        if i < impact and teed_c is not None:
            cv2.circle(
                vis,
                (int(round(teed_c[0] * SCALE)), int(round(teed_c[1] * SCALE))),
                int(round(max(teed_r, 3.0) * SCALE)),
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
                "RADAR TRIGGER",
                (8, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                (30, 30, 30),
                3,
                cv2.LINE_AA,
            )
            cv2.putText(
                vis,
                "RADAR TRIGGER",
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
