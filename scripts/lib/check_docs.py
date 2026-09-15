#!/usr/bin/env python3
"""The README's links, and the diagram it opens with.

WHY THIS EXISTS. The README is the one file a newcomer reads first, and the two
ways it breaks are both silent on GitHub: a relative link to a file that moved
renders as a link and 404s when clicked, and an SVG that does not parse renders
as a broken-image icon with no error anywhere. rumdl checks the prose, not
where it points, so this does the pointing.

WHAT IT HOLDS, for each markdown file named on the command line:

    every relative link and image — inline, reference-style or an HTML
    `src`/`srcset` — resolves to a TRACKED file or directory. Tracked, not
    merely present, so a gitignored file in an operator's checkout cannot make
    this pass where CI would fail.

    README.md embeds an SVG diagram, and every SVG any of them embeds
      - parses as XML with an <svg> root and a viewBox
      - carries its words as real <text>, so they stay searchable and sharp
      - loads nothing: no <image>, <script> or <foreignObject>, no href that
        leaves the file, no @import and no url() but a local #fragment
      - has a `prefers-color-scheme` style, so it reads on a dark theme
      - stays small (MAX_SVG_BYTES)

Links inside fenced code blocks and inline code spans are examples, not links,
and are skipped.

Usage: check_docs.py README.md [more.md ...]
"""

from __future__ import annotations

import os
import posixpath
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

MAX_SVG_BYTES = 64 * 1024

FENCE_RE = re.compile(r"^ {0,3}(```|~~~).*?^ {0,3}\1[^\n]*$", re.M | re.S)
CODE_SPAN_RE = re.compile(r"`[^`\n]*`")
INLINE_RE = re.compile(r"(!?)\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
REFDEF_RE = re.compile(r"^ {0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s+\"[^\"]*\")?\s*$", re.M)
HTML_SRC_RE = re.compile(r"\b(src|srcset|href)\s*=\s*\"([^\"]+)\"")
SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def tracked_paths() -> set[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, check=True
    ).stdout.decode()
    files = {p for p in out.split("\0") if p}
    dirs = {os.path.dirname(p) for p in files}
    for d in list(dirs):
        while d:
            d = os.path.dirname(d)
            dirs.add(d)
    return files | dirs


def targets(text: str) -> list[tuple[str, bool]]:
    """(target, is_image) for every link in the prose."""
    prose = CODE_SPAN_RE.sub("", FENCE_RE.sub("", text))
    found = [(m.group(2), m.group(1) == "!") for m in INLINE_RE.finditer(prose)]
    # A reference definition does not say whether it is used as an image, so
    # judge by the extension; an SVG linked to rather than embedded is still
    # worth holding to the same rules.
    for m in REFDEF_RE.finditer(prose):
        found.append((m.group(1), m.group(1).lower().endswith(".svg")))
    for m in HTML_SRC_RE.finditer(prose):
        for candidate in m.group(2).split(","):
            url = candidate.strip().split(" ")[0]
            found.append((url, m.group(1) != "href"))
    return found


def local(url: str) -> str | None:
    if not url or url.startswith("#") or SCHEME_RE.match(url) or url.startswith("//"):
        return None
    return url.split("#", 1)[0].split("?", 1)[0] or None


def check_svg(path: str) -> list[str]:
    problems = []
    size = os.path.getsize(path)
    if size > MAX_SVG_BYTES:
        problems.append(f"{path}: {size} bytes, over the {MAX_SVG_BYTES} limit")
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        return problems + [f"{path}: does not parse as XML: {e}"]

    def name(el: ET.Element) -> str:
        return el.tag.rsplit("}", 1)[-1]

    if name(root) != "svg":
        problems.append(f"{path}: root element is <{name(root)}>, not <svg>")
    if not root.get("viewBox"):
        problems.append(f"{path}: <svg> has no viewBox, so it cannot scale")

    words = 0
    styles = []
    for el in root.iter():
        tag = name(el)
        if tag in ("image", "script", "foreignObject"):
            problems.append(f"{path}: contains <{tag}>; the diagram must load nothing")
        for attr, value in el.attrib.items():
            if attr.rsplit("}", 1)[-1] == "href" and not value.startswith("#"):
                problems.append(f"{path}: <{tag}> links out to {value!r}")
        if tag == "text" and "".join(el.itertext()).strip():
            words += 1
        if tag == "style":
            styles.append(el.text or "")

    if words == 0:
        problems.append(f"{path}: no <text> with words in it; text must stay text")
    css = "\n".join(styles)
    if "@import" in css or re.search(r"url\(\s*['\"]?(?!#)", css):
        problems.append(f"{path}: its style loads an external resource")
    if "prefers-color-scheme" not in css:
        problems.append(f"{path}: no prefers-color-scheme style, so it has no dark palette")
    return problems


def main(argv: list[str]) -> int:
    if not argv:
        sys.stderr.write(__doc__.split("Usage: ", 1)[1])
        return 2
    tracked = tracked_paths()
    problems = []
    for md in argv:
        # Resolved as git names paths, with forward slashes on every OS: an
        # os.path join on Windows gives backslashes, which match no tracked path.
        base = posixpath.dirname(md.replace(os.sep, "/"))
        with open(md, encoding="utf-8") as f:
            text = f.read()
        svgs = []
        for url, is_image in targets(text):
            rel = local(url)
            if rel is None:
                continue
            path = posixpath.normpath(posixpath.join(base, rel))
            if path not in tracked:
                problems.append(f"{md}: links to {url!r}, which is not a tracked file")
                continue
            if is_image and path.lower().endswith(".svg"):
                svgs.append(path)
        if os.path.basename(md) == "README.md" and not svgs:
            problems.append(f"{md}: embeds no SVG diagram; the README opens with one")
        for svg in dict.fromkeys(svgs):
            problems.extend(check_svg(svg))
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
