"""Every link in the documentation must point at something that exists.

A repository is read through its links. A broken one costs nothing to ship and
everything to trust: it is the reader's first evidence that the prose and the
code have drifted apart, and it is invisible to every other test here --
`test_stated_numbers.py` checks that the FIGURES in the prose are true, and
nothing checked that the PATHS were.

This covers relative links only. External URLs are deliberately not fetched:
a test suite that fails because somebody else's server is down is a test suite
people learn to ignore, and the network is not available in every environment
this runs in.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", "node_modules", "__pycache__", "dist", ".pytest_cache",
        ".hypothesis", ".venv", "site-packages"}

MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
HREF = re.compile(r'href="([^"]+)"')


def _files(pattern: str) -> list[Path]:
    return [f for f in ROOT.rglob(pattern)
            if not any(p in SKIP for p in f.parts)]


def _targets(f: Path, pattern: re.Pattern) -> list[str]:
    txt = f.read_text(encoding="utf-8", errors="replace")
    out = []
    for m in pattern.finditer(txt):
        t = m.group(1)
        if t.startswith(("http://", "https://", "mailto:", "#", "data:", "//")):
            continue
        out.append(t)
    return out


@pytest.mark.parametrize("md", _files("*.md"), ids=lambda p: str(p.relative_to(ROOT)))
def test_every_relative_link_in_a_document_resolves(md):
    broken = []
    for t in _targets(md, MD_LINK):
        # Strip the anchor: the file has to exist, and asserting on heading
        # slugs would be a different and much noisier test.
        target = (md.parent / t.split("#")[0]).resolve()
        if not target.exists():
            broken.append(t)
    assert not broken, f"{md.relative_to(ROOT)} links to files that do not exist: {broken}"


def test_the_repository_front_door_points_at_real_files():
    """The README is the one document everybody reads, so it gets its own
    assertion rather than sharing a parametrised one -- if this fails, the
    failure should name the README."""
    readme = ROOT / "README.md"
    broken = [t for t in _targets(readme, MD_LINK)
              if not (readme.parent / t.split("#")[0]).resolve().exists()]
    assert not broken, f"README links to files that do not exist: {broken}"


def test_the_site_asks_for_no_asset_that_is_missing():
    """Relative hrefs in the front end. A missing stylesheet or icon is a
    broken page, and it is the kind of break that survives review because the
    fallback usually looks almost right."""
    broken = []
    for html in _files("*.html"):
        if "deploy" in html.parts:      # superseded static mirrors
            continue
        for t in _targets(html, HREF):
            path = t.split("#")[0].split("?")[0].lstrip("/")
            if not path:
                continue
            # Served from web/, so a root-relative href resolves against it.
            if not ((html.parent / path).exists() or (ROOT / "web" / path).exists()):
                broken.append(f"{html.relative_to(ROOT)}: {t}")
    assert not broken, f"the site references assets that do not exist: {broken}"
