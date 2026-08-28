"""Cap the section rail on narrow screens.

Open by default it dumped 38 links above the headline on a phone, which is
exactly the long-scroll problem the rail was added to solve. The summary now
sits outside a scrollable body, so the list is a compact index on narrow
screens and fills the fixed rail on wide ones.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

PAGE = Path(os.environ["OPENFLIGHT_PAGE_OUT"]) / "openflight-impact-location.html"
s = PAGE.read_text(encoding="utf-8")


def sub(old: str, new: str) -> None:
    global s
    assert old in s, f"NOT FOUND: {old[:70]!r}"
    assert s.count(old) == 1, f"AMBIGUOUS ({s.count(old)}): {old[:70]!r}"
    s = s.replace(old, new, 1)


# 1. Summary stays put; only the list scrolls.
sub(
    '<summary>Sections</summary>\n<div class="rail-group">',
    '<summary>Sections</summary>\n<div class="rail-body">\n<div class="rail-group">',
)
sub("</ul>\n</details>", "</ul>\n</div>\n</details>")

# 2. Cap on narrow, uncapped inside the fixed rail. Matched by regex because the
#    rule contains a CSS unicode escape that is awkward to quote through a shell.
pattern = re.compile(r'(#rail summary::after\{content:")[^"]*("[^}]*\})', re.S)
assert pattern.search(s), "summary::after rule not found"
s = pattern.sub(
    lambda m: m.group(1)
    + "  tap to collapse"
    + m.group(2).rstrip("}")
    + ";white-space:pre}\n"
    + ".rail-body{max-height:min(46vh,340px);overflow-y:auto;"
    "-webkit-overflow-scrolling:touch}",
    s,
    count=1,
)

sub(
    "  #rail summary{display:none}\n}",
    "  #rail summary{display:none}\n  .rail-body{max-height:none;overflow:visible}\n}",
)

PAGE.write_text(s, encoding="utf-8")
block = s[s.find('<details id="rail"') : s.find("</details>") + len("</details>")]
print("rail-body present:", "rail-body" in s)
print("div balance inside rail:", block.count("<div") - block.count("</div>"))
print("capped on narrow:", "max-height:min(46vh,340px)" in s)
print("uncapped on wide:", ".rail-body{max-height:none" in s)
