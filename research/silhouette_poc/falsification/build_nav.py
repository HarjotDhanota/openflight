"""Turn one 35-section scroll into something a reader can navigate.

The page is a working log and its order carries meaning -- things appear as
they were found and corrected. So the sections are NOT reordered. What is added
is a way in: a sticky rail listing every section, grouped into the phases the
log already falls into, with the current section highlighted as you scroll.

Sections stay expanded rather than collapsed. This is a reference document that
a team will search, and collapsing sections breaks Ctrl+F for the sake of a
scrollbar.

The rail is generated FROM the headings, so it cannot drift out of sync with
the document. Adding a section without updating this file puts it in the
trailing group rather than silently dropping it.

Design notes: no new colour is introduced. The rail borrows --cam (the teal
already used for the camera throughout) for the active marker, and the group
labels reuse the Plex Mono uppercase treatment already established by .secnum
and table headers.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

PAGE = (
    Path(
        os.environ.get("OPENFLIGHT_PAGE_OUT", Path(__file__).resolve().parent / "page")
    )
    / "openflight-impact-location.html"
)

# Contiguous phases the log already falls into. Anything not listed lands in a
# trailing "Also on this page" group rather than disappearing.
GROUPS = [
    ("Start here", ["status", "roadmap"]),
    ("The rig and the ball", ["01", "02", "03", "04"]),
    ("Model, sensors, first fit", ["05", "06", "06b", "07", "07b"]),
    ("The 22-shot session", ["08", "08b", "08c", "08d"]),
    ("Corrections and the radar", ["09", "09b", "09c", "09d"]),
    ("The mesh fit", ["10", "10b", "10c", "10d"]),
    (
        "Pose in three dimensions",
        ["11", "11b", "11c", "11d", "11e", "11f", "11g", "11h", "11i", "11j"],
    ),
    ("What broke it open", ["13", "14", "15", "16"]),
    ("Contribute", ["12"]),
]

CSS = """
/* ---- section rail ---- */
#rail{background:var(--surface);border:1px solid var(--line);border-radius:10px;
      padding:14px 18px;margin:0 0 30px;font-size:14px}
#rail summary{font-family:"IBM Plex Sans Condensed",sans-serif;font-weight:700;
      font-size:16px;cursor:pointer;list-style:none}
#rail summary::-webkit-details-marker{display:none}
#rail summary::after{content:" \\2013 tap to collapse";font-family:"IBM Plex Mono",monospace;
      font-size:11px;color:var(--ink-soft);font-weight:400;letter-spacing:.04em}
#rail[open] summary{margin-bottom:10px}
.rail-group{font-family:"IBM Plex Mono",monospace;font-size:10.5px;letter-spacing:.1em;
      text-transform:uppercase;color:var(--ink-soft);margin:16px 0 6px}
.rail-group:first-of-type{margin-top:4px}
#rail ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:1px}
#rail a{display:block;padding:4px 8px 4px 10px;color:var(--ink-soft);text-decoration:none;
      border-left:2px solid transparent;border-radius:0 4px 4px 0;line-height:1.35;
      font-size:13.4px}
#rail a:hover{color:var(--ink);background:var(--code)}
#rail a:focus-visible{outline:2px solid var(--cam);outline-offset:1px}
#rail a.here{color:var(--ink);border-left-color:var(--cam);background:var(--code);font-weight:600}
.rail-n{font-family:"IBM Plex Mono",monospace;font-size:10.5px;color:var(--ink-soft);
      margin-right:7px;letter-spacing:.04em}
@media (min-width:1240px){
  body{padding-left:262px}
  #rail{position:fixed;top:0;left:0;bottom:0;width:262px;margin:0;border:0;border-radius:0;
        border-right:1px solid var(--line);padding:30px 14px 48px 22px;overflow-y:auto;
        z-index:5}
  #rail summary{display:none}
}
@media (prefers-reduced-motion:no-preference){html{scroll-behavior:smooth}}
h2{scroll-margin-top:22px}
"""

JS = """
(function(){
  var links = Array.prototype.slice.call(document.querySelectorAll('#rail a[href^="#"]'));
  if(!links.length) return;
  var byId = {};
  links.forEach(function(a){ byId[a.getAttribute('href').slice(1)] = a; });
  var targets = Object.keys(byId)
    .map(function(id){ return document.getElementById(id); })
    .filter(Boolean);
  if(!targets.length) return;
  var current = null;
  function mark(id){
    if(id === current) return;
    if(current && byId[current]) byId[current].classList.remove('here');
    current = id;
    var a = byId[id];
    if(!a) return;
    a.classList.add('here');
    var rail = document.getElementById('rail');
    if(rail && window.matchMedia('(min-width:1240px)').matches){
      var top = a.offsetTop, bot = top + a.offsetHeight;
      if(top < rail.scrollTop || bot > rail.scrollTop + rail.clientHeight){
        rail.scrollTop = top - rail.clientHeight / 2;
      }
    }
  }
  // Nearest heading at or above the reading line wins, so short sections and
  // fast scrolling cannot leave the rail pointing at nothing.
  function update(){
    var line = 90, best = targets[0];
    for(var i = 0; i < targets.length; i++){
      if(targets[i].getBoundingClientRect().top <= line) best = targets[i];
    }
    mark(best.id);
  }
  var ticking = false;
  window.addEventListener('scroll', function(){
    if(ticking) return;
    ticking = true;
    window.requestAnimationFrame(function(){ update(); ticking = false; });
  }, {passive:true});
  window.addEventListener('resize', update, {passive:true});
  update();
})();
"""


def main() -> None:
    page = PAGE.read_text(encoding="utf-8")
    if 'id="rail"' in page:
        raise SystemExit("rail already present -- edit it in place instead")

    # 1. Give every numbered h2 a stable id derived from its section number.
    def add_id(match: re.Match) -> str:
        number = match.group(1)
        return f'<h2 id="s{number}"><span class="secnum">{number}</span>'

    page, n_ids = re.subn(
        r'<h2><span class="secnum">([0-9]+[a-z]?)</span>', add_id, page
    )

    # 2. Read the headings back out, so the rail is generated from the document.
    found = re.findall(
        r'<h2 id="(s[0-9]+[a-z]?)"><span class="secnum">([0-9]+[a-z]?)</span>(.*?)</h2>',
        page,
    )
    titles = {number: title for _id, number, title in found}
    assert titles, "no numbered sections found"

    panels = {
        "status": "Where this stands",
        "roadmap": "What has to happen next",
    }
    for key in panels:
        assert f'id="{key}"' in page, f'expected panel id="{key}"'

    listed, items = set(), []
    for label, keys in GROUPS:
        rows = []
        for key in keys:
            if key in panels:
                rows.append(f'<li><a href="#{key}">{panels[key]}</a></li>')
                listed.add(key)
            elif key in titles:
                rows.append(
                    f'<li><a href="#s{key}"><span class="rail-n">{key}</span>{titles[key]}</a></li>'
                )
                listed.add(key)
        if rows:
            items.append(
                f'<div class="rail-group">{label}</div>\n<ul>' + "".join(rows) + "</ul>"
            )

    missing = [k for k in titles if k not in listed]
    if missing:
        rows = "".join(
            f'<li><a href="#s{k}"><span class="rail-n">{k}</span>{titles[k]}</a></li>'
            for k in missing
        )
        items.append(
            '<div class="rail-group">Also on this page</div>\n<ul>' + rows + "</ul>"
        )
        print(f"note: {len(missing)} ungrouped section(s) appended: {missing}")

    rail = (
        '<details id="rail" open>\n<summary>Sections</summary>\n'
        + "\n".join(items)
        + "\n</details>\n"
    )

    # 3. Rail goes first inside .wrap; styles and script alongside the existing ones.
    wrap_open = '<div class="wrap">'
    assert page.count(wrap_open) == 1, "expected exactly one .wrap"
    page = page.replace(wrap_open, wrap_open + "\n" + rail, 1)
    page = page.replace("</style>", CSS + "\n</style>", 1)

    if "</body>" in page:
        page = page.replace("</body>", f"<script>{JS}</script>\n</body>", 1)
    else:
        page = page + f"\n<script>{JS}</script>\n"

    PAGE.write_text(page, encoding="utf-8")
    print(f"ids added: {n_ids}; sections in rail: {len(listed)}; groups: {len(items)}")
    print(f"wrote {PAGE}")


if __name__ == "__main__":
    main()
