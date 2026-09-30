"""Log text is written in UTF-8 once, not re-encoded (P6-6).

Outdoors-test-5's kiosk log held ``C3 82 C2 B0`` for every degree sign: the
server's own format strings had been decoded as cp1252 and saved again as
UTF-8, so the log faithfully wrote "Â°".
"""

import re
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src" / "openflight"
# UTF-8 text read as cp1252 and saved again: "Â°", "Â±", "â€”", "â†’", "â†\x90", "Ã©" ...
DOUBLE_ENCODED = re.compile("Â[\u00a0-\u00bf]|â€.|â†.|Ã[\u0080-\u00bf]")


def test_no_source_file_holds_double_encoded_text():
    offenders = {
        str(path.relative_to(SOURCE)): sorted(set(DOUBLE_ENCODED.findall(text)))
        for path in SOURCE.rglob("*.py")
        if DOUBLE_ENCODED.search(text := path.read_text(encoding="utf-8"))
    }
    assert offenders == {}


def test_the_shot_line_writes_a_single_degree_sign():
    text = (SOURCE / "server.py").read_text(encoding="utf-8")
    assert '", Launch: %.1f\u00b0"' in text
