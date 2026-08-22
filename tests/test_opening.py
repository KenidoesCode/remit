"""The opening is part of the product, so it gets tests like the rest of it.

These are static assertions on the shipped front end, not a browser harness.
They exist to catch the failure that actually happens to an intro: someone
renames the product, or edits the expansion in one file, and the two drift
apart. The visual behaviour is verified by hand with a browser; this catches
the silent textual regression that no one would notice.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
EXPANSION = "Revocable, Explainable Mandates for Intent-driven Transactions"


def test_the_expansion_is_identical_everywhere_it_appears():
    """One name, one expansion. The CLI demo and the opening must agree."""
    hero = (ROOT / "demo" / "hero.py").read_text(encoding="utf-8")
    assert EXPANSION in hero, "the CLI demo lost the expansion"
    assert EXPANSION in HTML, "the opening lost the expansion"


def test_the_opening_carries_the_whole_attribution():
    for fragment in ("REMIT", EXPANSION, "techuilaguy", "Pranauv Shrinaath"):
        assert fragment in HTML, fragment


def test_the_opening_is_the_first_thing_in_the_body():
    """It must precede the canvas, the glow and the page, or the product
    flashes before the thing that made it."""
    body = HTML.index("<body>")
    assert HTML.index('id="intro"', body) < HTML.index('id="gl"', body)
    assert HTML.index('id="intro"', body) < HTML.index('id="page"', body)


def test_the_product_is_hidden_until_the_opening_says_otherwise():
    assert 'body:not([data-intro="done"]) #page' in CSS
    assert "visibility:hidden" in CSS


def test_the_opening_cannot_strand_the_product():
    """Every exit path ends in a reveal, including a hard timer that does not
    depend on the animation finishing, or on GSAP existing at all."""
    assert "hardStop = setTimeout(done" in APP
    assert 'document.body.dataset.intro = "done"' in APP
    assert "catch (e) {\n    finish();" in APP


def test_reduced_motion_still_gets_the_branding():
    assert "if (REDUCED) {" in APP.split("function opening()")[1].split("function ")[0]


def test_the_opening_introduces_no_new_colour_or_family():
    """It must use the tokens the product already defines. A second red or a
    third font family is a new brand, not an opening."""
    # Bound the slice to the opening's own section rather than "everything
    # after the words 'the opening'". The looser version passed until the file
    # grew a section below it, and then failed on a `#chips` selector that has
    # nothing to do with the opening -- a test that fails for a reason it is
    # not about is worse than no test.
    block = CSS.split("the opening")[1].split("/* \u2500")[0]
    for token in ("var(--bg)", "var(--signal)", "var(--ink)", "var(--m)", "var(--s)"):
        assert token in block, token
    assert "#" not in block.replace("#intro", "").replace("#webshot", "") \
        .replace("#page", "").replace("#gl", "").replace("#glow", ""), \
        "a raw hex colour appeared in the opening"


@pytest.mark.parametrize("path_class", ["w1", "w2", "anchor"])
def test_the_thread_exists_and_is_drawn_not_imported(path_class):
    assert path_class in HTML
    assert "<svg id=\"webshot\"" in HTML
    assert "three.js" not in HTML.lower() and "lottie" not in HTML.lower()


def test_the_teardown_does_not_depend_on_the_animation_clock():
    """GSAP runs on requestAnimationFrame, which a browser throttles to a
    standstill in a background tab. If removal only happened in an onComplete,
    a tab that loaded unfocused would keep a full-screen opaque panel over the
    product. The removal gets a setTimeout of its own. FAILURES #15."""
    body = APP.split("function opening()")[1].split("\nfunction ")[0]
    assert "setTimeout(() => el.remove(), 700)" in body


def test_the_opening_waits_to_be_looked_at():
    """A link someone was sent opens in a background tab. Playing the opening
    there spends it on nobody."""
    body = APP.split("function opening()")[1].split("\nfunction ")[0]
    assert "if (document.hidden) {" in body
    assert "visibilitychange" in body
