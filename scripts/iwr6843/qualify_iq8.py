#!/usr/bin/env python3
"""Compare matched IQ16/IQ8 dumps through the production IWR estimators."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from openflight.iwr6843.driver import BAUD
from openflight.iwr6843.iq8_qualification import (
    build_incomplete_report,
    build_qualification_report,
    write_qualification_report,
)
from openflight.iwr6843.monitor import tx_order_from_config


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Opt-in offline comparison of matched IQ16/IQ8 dumps. This writes evidence for "
            "later Pi qualification and never changes the production profile."
        )
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--cal",
        type=Path,
        default=Path("config/iwr6843_calibration_reference.json"),
    )
    parser.add_argument(
        "--iq16-config",
        type=Path,
        default=Path("config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg"),
    )
    parser.add_argument(
        "--iq8-config",
        type=Path,
        default=Path("config/iwr6843_l3dump_dense_36f2ms_53bin_iq8.cfg"),
    )
    parser.add_argument("--tee-m", required=True, type=float)
    parser.add_argument("--net-m", type=float)
    parser.add_argument("--tilt-deg", type=float)
    parser.add_argument("--radar-height-m", type=float)
    parser.add_argument("--ball-height-m", type=float, default=0.040)
    parser.add_argument("--baud", type=int, default=BAUD)
    parser.add_argument("--azimuth-offset-deg", type=float, default=0.0)
    parser.add_argument("--horizontal-phase-reference-rad", type=float)
    parser.add_argument(
        "--tdm-sign-policy", choices=("positive", "negative", "auto"), default="positive"
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        tx_order = tx_order_from_config(args.iq16_config)
        iq8_tx_order = tx_order_from_config(args.iq8_config)
        if iq8_tx_order != tx_order:
            raise ValueError("IQ16 and IQ8 configs declare different TX order")
        report = build_qualification_report(
            args.manifest,
            calibration_path=args.cal,
            iq16_config_path=args.iq16_config,
            iq8_config_path=args.iq8_config,
            tee_range_m=args.tee_m,
            net_range_m=args.net_m,
            tilt_deg=args.tilt_deg,
            radar_height_m=args.radar_height_m,
            ball_height_m=args.ball_height_m,
            tx_order=tx_order,
            tdm_sign_policy=args.tdm_sign_policy,
            azimuth_offset_deg=args.azimuth_offset_deg,
            horizontal_phase_reference_rad=args.horizontal_phase_reference_rad,
            baud=args.baud,
        )
    except (OSError, ValueError) as error:
        code = "input_unreadable" if isinstance(error, OSError) else "input_contract_rejected"
        report = build_incomplete_report(code, error)
        print(f"IQ8 evidence rejected: {error}", file=sys.stderr)
    try:
        write_qualification_report(report, args.out)
    except FileExistsError:
        print(f"refusing to replace existing output: {args.out}", file=sys.stderr)
        return 3
    if aggregate := report.get("aggregate"):
        print(
            f"wrote {aggregate['compared_pairs']}/{aggregate['declared_pairs']} pair comparisons "
            f"to {args.out}"
        )
    else:
        print(f"wrote incomplete diagnostic evidence to {args.out}")
    print(f"qualification: {report['qualification']['status']}")
    return 0 if report["qualification"]["evidence_status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
