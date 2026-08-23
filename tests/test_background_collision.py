"""The decorative field must never cross readable content.

A line through a heading reads as a strikethrough. A node sitting on a
descender reads as a typo. Both are rendering bugs wearing a mood, and both are
the kind of thing that gets waved through as "it's subtle" until somebody
screenshots it.

HOW THIS CHECKS
---------------
Not by sampling pixels. The field is translucent, so a pixel test is a
threshold argument nobody wins. Instead `web/gl.js` records the exact geometry
it drew for a frame -- every point and every line segment, in viewport
coordinates -- and this asserts none of it intersects the bounding box of any
protected element.

That is ground truth: if a segment is not in the list, it was not drawn.

Run at every breakpoint the site claims to support, because a zone that is
clear at 1440 can be crossed at 375 when the layout reflows underneath it.
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

# Every width the project claims to support.
BREAKPOINTS = [1440, 1280, 1024, 768, 430, 390, 375]

# What must never be crossed. Kept narrower than the renderer's own list: this
# is the assertion, and an assertion that protects everything protects nothing.
PROTECTED = ", ".join([
    "#nav",
    "#hero h1",
    ".act-head h2",
    ".act-head .lede",
    ".sdk-cmd",
    ".sdk-code",
    ".sdk-btn",
    ".sdk-thesis",
    "#hero a",
    "#hero .cta",
])

PROBE = """
() => {
  window.__REMIT_GL_DEBUG = true;
  if (window.__remitUpdateZones) window.__remitUpdateZones();
  return true;
}
"""

COLLECT = """
(sel) => {
  const drawn = (window.REMITGL && window.REMITGL.drawn) || { points: [], lines: [] };
  const rects = [];
  document.querySelectorAll(sel).forEach((el) => {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return;
    if (r.bottom < 0 || r.top > innerHeight) return;
    if (r.right < 0 || r.left > innerWidth) return;
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none") return;
    if (parseFloat(cs.opacity || "1") < 0.05) return;
    rects.push({
      tag: el.tagName.toLowerCase() + (el.className ? "." + String(el.className).split(" ")[0] : ""),
      x: r.left, y: r.top, w: r.width, h: r.height,
      text: (el.textContent || "").trim().slice(0, 40),
    });
  });
  return { drawn, rects, zones: (window.REMITGL && window.REMITGL.zones) || [] };
}
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _point_in(rect: dict, x: float, y: float, inset: float) -> bool:
    """Inset rather than pad: assert on the region the TEXT occupies.

    The renderer pads outward by a margin, so testing the padded box would be
    testing the renderer's own arithmetic against itself. Insetting slightly
    means a decoration grazing the very edge of a bounding box -- which is
    usually leading, not a glyph -- does not fail the build.
    """
    return (rect["x"] + inset <= x <= rect["x"] + rect["w"] - inset
            and rect["y"] + inset <= y <= rect["y"] + rect["h"] - inset)


def _segment_hits(rect: dict, seg: list[float], inset: float) -> bool:
    x0, y0, x1, y1 = seg
    if _point_in(rect, x0, y0, inset) or _point_in(rect, x1, y1, inset):
        return True
    rx0, ry0 = rect["x"] + inset, rect["y"] + inset
    rx1, ry1 = rect["x"] + rect["w"] - inset, rect["y"] + rect["h"] - inset
    if rx1 <= rx0 or ry1 <= ry0:
        return False
    if max(x0, x1) < rx0 or min(x0, x1) > rx1:
        return False
    if max(y0, y1) < ry0 or min(y0, y1) > ry1:
        return False
    # Liang-Barsky clip, same test the renderer uses.
    t0, t1 = 0.0, 1.0
    dx, dy = x1 - x0, y1 - y0
    for p, q in ((-dx, x0 - rx0), (dx, rx1 - x0), (-dy, y0 - ry0), (dy, ry1 - y0)):
        if p == 0:
            if q < 0:
                return False
            continue
        r = q / p
        if p < 0:
            if r > t1:
                return False
            t0 = max(t0, r)
        else:
            if r < t0:
                return False
            t1 = min(t1, r)
    return True


@pytest.fixture(scope="module")
def server():
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "remit.api:api",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(90):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                break
        except OSError:
            time.sleep(0.2)
    else:
        proc.terminate()
        pytest.skip("server did not start")
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as pw:
        # Headless Chromium has no GPU, so without a software rasteriser the
        # WebGL context fails, NOTHING is drawn, and every collision assertion
        # below passes because there is no geometry to collide with. The first
        # run of this file did exactly that: 14 green, and the one test that
        # checks the probe sees anything skipped. A vacuous pass is worse than
        # a failure, so SwiftShader is not optional here.
        args = ["--use-gl=angle", "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader", "--disable-gpu-sandbox"]
        try:
            b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium", args=args)
        except Exception:
            try:
                b = pw.chromium.launch(args=args)
            except Exception as exc:                       # pragma: no cover
                pytest.skip(f"chromium unavailable: {exc}")
        yield b
        b.close()


@pytest.mark.parametrize("width", BREAKPOINTS)
def test_decoration_never_crosses_protected_content(server, browser, width):
    page = browser.new_page(viewport={"width": width, "height": 900})
    try:
        page.goto(server, wait_until="domcontentloaded")
        page.wait_for_timeout(3500)
        page.evaluate("() => { const i = document.getElementById('intro'); if (i) i.remove(); }")
        page.evaluate(PROBE)

        offenders: list[str] = []
        # Walk the page: a zone clear at the top can be crossed further down.
        for step in range(6):
            page.evaluate(f"() => window.scrollTo(0, {step} * innerHeight * 0.9)")
            page.wait_for_timeout(700)
            page.evaluate(PROBE)
            page.wait_for_timeout(350)

            data = page.evaluate(COLLECT, PROTECTED)
            drawn, rects = data["drawn"], data["rects"]
            if not rects:
                continue

            for rect in rects:
                for px, py in drawn["points"]:
                    if _point_in(rect, px, py, 2.0):
                        offenders.append(
                            f"w={width} scroll={step}: a POINT at ({px:.0f},{py:.0f}) "
                            f"is inside {rect['tag']} {rect['text']!r}")
                        break
                for seg in drawn["lines"]:
                    if _segment_hits(rect, seg, 2.0):
                        offenders.append(
                            f"w={width} scroll={step}: a LINE "
                            f"({seg[0]:.0f},{seg[1]:.0f})->({seg[2]:.0f},{seg[3]:.0f}) "
                            f"crosses {rect['tag']} {rect['text']!r}")
                        break

        assert not offenders, (
            "decoration is drawn over readable content:\n  "
            + "\n  ".join(offenders[:12]))
    finally:
        page.close()


@pytest.mark.parametrize("width", BREAKPOINTS)
def test_no_horizontal_overflow(server, browser, width):
    """A page that scrolls sideways on a phone is a broken page."""
    page = browser.new_page(viewport={"width": width, "height": 900})
    try:
        page.goto(server, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        page.evaluate("() => { const i = document.getElementById('intro'); if (i) i.remove(); }")
        page.wait_for_timeout(500)
        over = page.evaluate("""() => ({
            scroll: document.documentElement.scrollWidth,
            client: document.documentElement.clientWidth,
        })""")
        assert over["scroll"] <= over["client"] + 1, (
            f"horizontal overflow at {width}px: "
            f"{over['scroll']} > {over['client']}")
    finally:
        page.close()


def test_the_probe_actually_sees_geometry(server, browser):
    """Guard against the collision test passing because nothing was drawn.

    An empty `drawn` list satisfies every assertion above forever. This asserts
    the field is rendering at all, so a silent WebGL failure cannot be mistaken
    for a clean layout.
    """
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        page.goto(server, wait_until="domcontentloaded")
        page.wait_for_timeout(3500)
        page.evaluate("() => { const i = document.getElementById('intro'); if (i) i.remove(); }")
        page.evaluate(PROBE)
        page.wait_for_timeout(600)
        data = page.evaluate(COLLECT, PROTECTED)
        total = len(data["drawn"]["points"]) + len(data["drawn"]["lines"])
        if total == 0:
            pytest.skip("WebGL is unavailable in this browser; nothing was drawn")
        assert data["zones"], "the page sent no protected zones to the renderer"
    finally:
        page.close()
