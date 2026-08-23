"""The red signal must frame the typography, never cross it.

`test_background_collision.py` covers the WebGL field, which is geometry the
renderer can be told to skip. This file covers the two SVG layers, which are
static paths that cannot be told anything — so the only way to know they clear
the text is to sample them and check.

METHOD
------
Walk each path with `getPointAtLength`, convert to viewport coordinates with
`getScreenCTM`, and assert no sample lands inside the bounding box of any hero
or intro text element. Sampling a curve is exact enough at 2px spacing: a
stroke that clips a glyph cannot do it in under 2px without also passing
through a sample.

If a path and a letter ever want the same pixel, the PATH moves.
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
playwright_api = pytest.importorskip("playwright.sync_api",
                                     reason="playwright is not installed")

BREAKPOINTS = [1440, 1280, 1024, 768, 430, 390, 375]

HERO_TEXT = ", ".join([
    ".hero-mark", ".hero-expand", ".hero-thesis", ".hero-by", ".hero-aka",
    ".hero-install", ".hero-line-1", ".hero-line-2", ".hero-btn", "#heroCta",
    "#nav", "#hero .eyebrow",
])

INTRO_TEXT = ", ".join([
    ".intro-mark", ".intro-exp", ".intro-lab", ".intro-by", ".intro-aka",
    ".intro-said p",
])

# Sample every path in an SVG and return viewport-space points.
SAMPLE = """
(sel) => {
  const svg = document.querySelector(sel);
  if (!svg) return null;
  // A hidden layer is not on screen and must not be sampled -- otherwise a
  // router that correctly gave up still fails the collision check.
  const svgCs = getComputedStyle(svg);
  if (svgCs.display === "none" || svgCs.visibility === "hidden") return [];
  if (parseFloat(svgCs.opacity || "1") < 0.02) return [];
  const ctm = svg.getScreenCTM();
  if (!ctm) return null;
  const pts = [];
  svg.querySelectorAll("path").forEach((p) => {
    let len = 0;
    try { len = p.getTotalLength(); } catch (e) { return; }
    if (!len) return;
    // Only sample what is actually being drawn: a path still dashed out to
    // zero length is not on screen and must not fail the check.
    const cs = getComputedStyle(p);
    if (parseFloat(cs.opacity || "1") < 0.02) return;
    const dash = parseFloat(cs.strokeDashoffset || "0");
    const visibleFrom = Math.abs(dash) >= len ? len : 0;
    if (visibleFrom >= len) return;
    const step = 2;
    for (let d = visibleFrom; d <= len; d += step) {
      const q = p.getPointAtLength(d);
      pts.push([q.x * ctm.a + q.y * ctm.c + ctm.e,
                q.x * ctm.b + q.y * ctm.d + ctm.f]);
    }
  });
  svg.querySelectorAll("circle").forEach((c) => {
    const cs = getComputedStyle(c);
    if (parseFloat(cs.opacity || "1") < 0.02) return;
    const cx = parseFloat(c.getAttribute("cx")), cy = parseFloat(c.getAttribute("cy"));
    const r = parseFloat(c.getAttribute("r") || "0");
    for (let a = 0; a < 6.283; a += 0.35) {
      const x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
      pts.push([x * ctm.a + y * ctm.c + ctm.e, x * ctm.b + y * ctm.d + ctm.f]);
    }
    pts.push([cx * ctm.a + cy * ctm.c + ctm.e, cx * ctm.b + cy * ctm.d + ctm.f]);
  });
  return pts;
}
"""

RECTS = """
(sel) => {
  const out = [];
  document.querySelectorAll(sel).forEach((el) => {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return;
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none") return;
    if (parseFloat(cs.opacity || "1") < 0.05) return;
    out.push({
      tag: el.tagName.toLowerCase() + (el.className && typeof el.className === "string"
             ? "." + el.className.split(" ")[0] : ""),
      x: r.left, y: r.top, w: r.width, h: r.height,
      text: (el.textContent || "").trim().slice(0, 36),
    });
  });
  return out;
}
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def server():
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "remit.api:api",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(90):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                break
        except OSError:
            time.sleep(0.2)
    else:
        proc.terminate()
        pytest.skip("server did not start")
    yield f"http://127.0.0.1:{port}"
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as pw:
        args = ["--use-gl=angle", "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader", "--disable-gpu-sandbox"]
        try:
            b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium", args=args)
        except Exception:
            try:
                b = pw.chromium.launch(args=args)
            except Exception as exc:                        # pragma: no cover
                pytest.skip(f"chromium unavailable: {exc}")
        yield b
        b.close()


def _inside(rect: dict, x: float, y: float, inset: float = 1.5) -> bool:
    return (rect["x"] + inset <= x <= rect["x"] + rect["w"] - inset
            and rect["y"] + inset <= y <= rect["y"] + rect["h"] - inset)


def _check(page, svg_sel: str, text_sel: str, width: int) -> list[str]:
    pts = page.evaluate(SAMPLE, svg_sel)
    if not pts:
        return []
    rects = page.evaluate(RECTS, text_sel)
    bad = []
    for rect in rects:
        for x, y in pts:
            if _inside(rect, x, y):
                bad.append(
                    f"w={width}: {svg_sel} passes through {rect['tag']} "
                    f"{rect['text']!r} at ({x:.0f},{y:.0f})")
                break
    return bad


@pytest.mark.parametrize("width", BREAKPOINTS)
def test_the_hero_signal_never_crosses_hero_text(server, browser, width):
    page = browser.new_page(viewport={"width": width, "height": 900})
    try:
        page.goto(server, wait_until="domcontentloaded")
        # Let the opening finish on its own terms rather than ripping it out:
        # the hero animates INTO existence and half of it would be missing.
        page.wait_for_function(
            "() => { const h = document.querySelector('.hero-mark');"
            " return h && getComputedStyle(h).visibility !== 'hidden'"
            " && !document.getElementById('intro'); }",
            timeout=40000)
        page.wait_for_timeout(3500)
        offenders = _check(page, "#heroSignal", HERO_TEXT, width)
        assert not offenders, "the red signal crosses text:\n  " + "\n  ".join(offenders[:8])
    finally:
        page.close()


@pytest.mark.parametrize("width", [1440, 768, 390])
def test_the_intro_threads_never_cross_intro_text(server, browser, width):
    page = browser.new_page(viewport={"width": width, "height": 900})
    try:
        page.goto(server, wait_until="domcontentloaded")
        # Sample the opening WHILE it is playing -- that is the only time these
        # paths are on screen, so checking afterwards would check nothing.
        offenders: list[str] = []
        for _ in range(10):
            if not page.query_selector("#intro"):
                break
            offenders += _check(page, "#webshot", INTRO_TEXT, width)
            page.wait_for_timeout(900)
        assert not offenders, "an intro thread crosses text:\n  " + "\n  ".join(offenders[:8])
    finally:
        page.close()


def test_the_sampler_actually_sees_the_signal(server, browser):
    """Guard against every assertion above passing over an empty sample.

    If the SVG never renders, `pts` is empty and the collision checks are
    vacuous. This is the same trap `test_background_collision.py` fell into on
    its first run, and it is worth one test to not fall into it twice.
    """
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        page.goto(server, wait_until="domcontentloaded")
        page.wait_for_function(
            "() => { const h = document.querySelector('.hero-mark');"
            " return h && !document.getElementById('intro'); }", timeout=40000)
        page.wait_for_timeout(3500)
        pts = page.evaluate(SAMPLE, "#heroSignal")
        assert pts, "the hero signal produced no sample points"
        assert len(pts) > 200, f"only {len(pts)} sample points; the paths are not drawn"
        rects = page.evaluate(RECTS, HERO_TEXT)
        assert rects, "no hero text was measured"
    finally:
        page.close()


@pytest.mark.parametrize("width", BREAKPOINTS)
def test_the_navigation_keeps_all_eleven_destinations(server, browser, width):
    """Every destination stays reachable at every width.

    Not "visible" -- below 1180px they live in a drawer. But they must all be
    IN THE DOM and pointing somewhere real, because hiding a link to make a bar
    fit is deleting a page quietly.
    """
    expected = ["exec", "sdk", "act1", "arena", "act3", "counter",
                "act4", "lab", "audit", "act5", "opensource"]
    page = browser.new_page(viewport={"width": width, "height": 900})
    try:
        page.goto(server, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        got = page.evaluate(
            "() => [...document.querySelectorAll('#navLinks a')].map(a => a.dataset.act)")
        assert got == expected, f"nav at {width}px is {got}"

        # And each one has a section to land on.
        missing = page.evaluate(
            "() => [...document.querySelectorAll('#navLinks a')]"
            "  .filter(a => !document.querySelector(a.getAttribute('href')))"
            "  .map(a => a.getAttribute('href'))")
        assert not missing, f"nav links with no target: {missing}"
    finally:
        page.close()


@pytest.mark.parametrize("width", BREAKPOINTS)
def test_the_hero_has_the_install_command_and_real_links(server, browser, width):
    page = browser.new_page(viewport={"width": width, "height": 900})
    try:
        page.goto(server, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        cmd = page.evaluate(
            "() => { const c = document.querySelector('.hero-cmd code');"
            " return c ? c.textContent.trim() : null; }")
        assert cmd == "npm install remit-sdk", cmd

        hrefs = page.evaluate(
            "() => [...document.querySelectorAll('.hero-actions a')].map(a => a.href)")
        assert any("npmjs.com/package/remit-sdk" in h for h in hrefs), hrefs
        assert any("github.com/KenidoesCode/remit" in h for h in hrefs), hrefs
        # No placeholders anywhere in the hero or nav.
        for h in hrefs:
            assert "example.com" not in h and "localhost" not in h, h
    finally:
        page.close()


def test_the_opening_sentences_stay_long_enough_to_read(server, browser):
    """The two sentences the project came out of must be readable.

    They used to appear and dim 1.7 seconds later, which is about the time it
    takes to NOTICE a sentence, not to read one. A first-time visitor reads
    unfamiliar copy at roughly 3.5 words per second; these are eight and twelve
    words.

    This samples opacity over the whole opening and asserts each line holds at
    full strength for at least 2.8s, and that the pair spans at least 6s. It
    measures the rendered opacity rather than reading the timeline numbers,
    because the timeline is what would be edited by accident.
    """
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        page.goto(server, wait_until="domcontentloaded")
        page.evaluate("""() => {
          window.__samples = [];
          const tick = () => {
            const g = (s) => {
              const el = document.querySelector(s);
              if (!el) return -1;
              return parseFloat(getComputedStyle(el).opacity || "0");
            };
            window.__samples.push([performance.now(), g('.said-1'), g('.said-2')]);
            if (document.getElementById('intro')) requestAnimationFrame(tick);
          };
          tick();
        }""")
        # Watch the whole opening, including its backstop.
        page.wait_for_function("() => !document.getElementById('intro')", timeout=40000)
        samples = page.evaluate("() => window.__samples")
        assert len(samples) > 60, f"only {len(samples)} samples; the opening did not run"

        def held(idx: int) -> float:
            """Milliseconds for which this line was at >=0.9 opacity."""
            first = last = None
            for t, a, b in samples:
                v = (a, b)[idx]
                if v >= 0.9:
                    first = t if first is None else first
                    last = t
            return 0.0 if first is None else last - first

        one, two = held(0), held(1)
        assert one >= 2800, f"'I gave an AI permission…' held only {one:.0f}ms"
        assert two >= 2800, f"'Then I tried to work out…' held only {two:.0f}ms"

        span = samples[-1][0] - samples[0][0]
        assert span >= 6000, f"the whole opening lasted {span:.0f}ms"
    finally:
        page.close()
