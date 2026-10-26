"""The firmware's binary wire-format registry (firmware/iwr6843/WIRE_FORMATS.md) matches its headers."""

from __future__ import annotations

import re
from pathlib import Path

FIRMWARE_DIR = Path(__file__).parents[1] / "firmware" / "iwr6843"
REGISTRY = FIRMWARE_DIR / "WIRE_FORMATS.md"
TWO_ROUND = FIRMWARE_DIR / "two_round_format.h"


def _rows() -> dict[str, dict[str, str]]:
    rows = {}
    for line in REGISTRY.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 5 or not cells[0].startswith("`"):
            continue
        for magic in re.findall(r"`([A-Z0-9]{4})`", cells[0]):
            rows[magic] = {"versions": cells[2], "status": cells[3], "defined_in": cells[4]}
    return rows


def _headers() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8").replace("\r\n", "\n")
        for path in FIRMWARE_DIR.glob("*.h")
    }


def test_the_registry_lists_the_five_agreed_payloads():
    assert set(_rows()) == {"ILD1", "ILE1", "ILT1", "OFC2", "ILO1", "ILC1"}


def test_each_magic_is_written_in_the_header_the_registry_names():
    headers = _headers()

    for magic, row in _rows().items():
        named = re.findall(r"`([a-z_0-9]+\.h)`", row["defined_in"])
        assert named, magic
        assert any(f'"{magic}"' in headers[name] for name in named), magic


def test_each_magic_is_defined_in_exactly_one_header():
    headers = _headers()

    for magic in _rows():
        owners = [name for name, text in headers.items() if f'"{magic}"' in text]
        assert len(owners) == 1, (magic, owners)


def test_the_stage_two_payloads_are_reserved_at_version_one_with_no_layout_yet():
    rows = _rows()
    text = TWO_ROUND.read_text(encoding="utf-8")

    for magic, prefix in (("ILO1", "OVERVIEW"), ("ILC1", "CELLS")):
        assert rows[magic]["versions"] == "1"
        assert rows[magic]["status"].startswith("Reserved")
        assert re.search(rf'#define L3_TWO_ROUND_{prefix}_MAGIC\s+"{magic}"', text)
        assert re.search(rf"#define L3_TWO_ROUND_{prefix}_VERSION\s+1U", text)
    assert "PLACEHOLDER" in text
    assert "typedef struct" not in text  # the layout comes from the host module in F2


def test_l3_dump_includes_the_stage_two_header_only_under_its_flag():
    source = (FIRMWARE_DIR / "l3_dump.c").read_text(encoding="utf-8").replace("\r\n", "\n")

    assert '#ifdef L3_TWO_ROUND\n#include "two_round_format.h"\n#endif\n' in source
    assert source.count('#include "two_round_format.h"') == 1
