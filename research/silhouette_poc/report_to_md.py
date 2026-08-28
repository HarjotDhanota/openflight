"""Render the technical report as markdown, so the repo carries it too.

The report is published as a web page with eleven figures. A pull request that
links to it and nothing else leaves the repository dependent on an external
page staying shared and reachable -- and the report is the document that
explains why any of the code exists. This converts the same HTML into markdown
so the substance lives in git, and points at the web version for the figures.

Generated from the published page rather than rewritten by hand, so the two
cannot drift apart on a number.
"""

from __future__ import annotations

import html
import os
import re
from pathlib import Path

SRC = Path(os.environ["OPENFLIGHT_PAGE_OUT"]) / "openflight-clubface-report.html"
OUT = Path("docs/clubface-impact-location-report.md")
WEB = "https://claude.ai/code/artifact/c8817c34-c3ea-4455-9700-cf5a4e238b75"


def strip_tags(fragment: str) -> str:
    """Inline HTML to markdown text."""
    t = fragment
    t = re.sub(r"<br\s*/?>", "\n", t)
    t = re.sub(r"<(b|strong)>(.*?)</\1>", r"**\2**", t, flags=re.S)
    t = re.sub(r"<(i|em)>(.*?)</\1>", r"*\2*", t, flags=re.S)
    t = re.sub(r"<code>(.*?)</code>", r"`\1`", t, flags=re.S)
    t = re.sub(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', r"[\2](\1)", t, flags=re.S)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    t = t.replace(" ", " ")
    return re.sub(r"[ \t]+", " ", t).strip()


def convert_table(block: str) -> str:
    rows = re.findall(r"<tr>(.*?)</tr>", block, re.S)
    if not rows:
        return ""
    out, header_done = [], False
    for row in rows:
        cells = re.findall(r"<(th|td)[^>]*>(.*?)</\1>", row, re.S)
        if not cells:
            continue
        values = [strip_tags(c[1]).replace("\n", " ").replace("|", "\\|") for c in cells]
        out.append("| " + " | ".join(values) + " |")
        if not header_done:
            out.append("|" + "|".join(["---"] * len(values)) + "|")
            header_done = True
    return "\n".join(out)


def main() -> None:
    src = SRC.read_text(encoding="utf-8")
    body = src[src.index('<div class="wrap">') :]

    lines: list[str] = []
    figure_count = 0
    # Walk the top-level blocks in document order.
    pattern = re.compile(
        r"<h1>(?P<h1>.*?)</h1>"
        r"|<h2[^>]*>(?P<h2>.*?)</h2>"
        r"|<h3[^>]*>(?P<h3>.*?)</h3>"
        r"|<p class=\"meta\">(?P<meta>.*?)</p>"
        r"|<p class=\"lede\">(?P<lede>.*?)</p>"
        r"|<p class=\"sub\">(?P<sub>.*?)</p>"
        r"|<p>(?P<p>.*?)</p>"
        r"|<pre><code>(?P<pre>.*?)</code></pre>"
        r"|<div class=\"panel(?P<kind>[^\"]*)\">(?P<panel>.*?)</div>"
        r"|<table>(?P<table>.*?)</table>"
        r"|<figure>(?P<figure>.*?)</figure>",
        re.S,
    )
    for m in pattern.finditer(body):
        if m.group("h1"):
            lines.append(f"# {strip_tags(m.group('h1'))}\n")
        elif m.group("meta"):
            lines.append(f"*{strip_tags(m.group('meta'))}*\n")
        elif m.group("h2"):
            text = strip_tags(m.group("h2"))
            text = re.sub(r"^(\d{2})\s*", r"\1. ", text)
            lines.append(f"\n## {text}\n")
        elif m.group("h3"):
            lines.append(f"\n### {strip_tags(m.group('h3'))}\n")
        elif m.group("lede") or m.group("sub"):
            lines.append(f"{strip_tags(m.group('lede') or m.group('sub'))}\n")
        elif m.group("p"):
            lines.append(f"{strip_tags(m.group('p'))}\n")
        elif m.group("pre"):
            lines.append("```\n" + html.unescape(m.group("pre")).strip() + "\n```\n")
        elif m.group("table") is not None:
            lines.append(convert_table(m.group("table")) + "\n")
        elif m.group("panel") is not None:
            inner = m.group("panel")
            head = re.search(r"<h3>(.*?)</h3>", inner, re.S)
            title = strip_tags(head.group(1)) if head else ""
            kind = (m.group("kind") or "").strip()
            mark = {"flag": "⚠️", "ok": "✅", "warn": "⚡"}.get(kind, "")
            if title:
                lines.append(f"\n> **{mark + ' ' if mark else ''}{title}**  ")
            for para in re.findall(r"<p>(.*?)</p>", inner, re.S):
                text = strip_tags(para).replace("\n", " ")
                lines.append(f"> {text}  ")
            tbl = re.search(r"<table>(.*?)</table>", inner, re.S)
            if tbl:
                lines.append(">")
                for row in convert_table(tbl.group(1)).splitlines():
                    lines.append(f"> {row}")
            lines.append("")
        elif m.group("figure") is not None:
            figure_count += 1
            cap = re.search(r"<figcaption>(.*?)</figcaption>", m.group("figure"), re.S)
            caption = strip_tags(cap.group(1)) if cap else "(no caption)"
            lines.append(
                f"\n> **Figure {figure_count}** — {caption}  \n"
                f"> *(image in the [web version]({WEB}))*\n"
            )

    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    header = (
        "<!-- Generated from the published report by "
        "research/silhouette_poc/report_to_md.py. Do not edit by hand. -->\n\n"
    )
    note = (
        f"> This is the markdown rendering of the [technical report]({WEB}). The web "
        f"version carries {figure_count} figures — real frames with the model's own "
        "projections overlaid — which are not reproduced here.\n\n"
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        header + text.split("\n", 1)[0] + "\n\n" + note + text.split("\n", 1)[1], encoding="utf-8"
    )
    print(f"wrote {OUT} ({len(text.splitlines())} lines, {figure_count} figures referenced)")


if __name__ == "__main__":
    main()
