"""The opening, in a real browser, in the state a link is actually opened in.

Three entries in FAILURES.md are browser-behaviour bugs that no amount of unit
testing could have caught -- #15 (rAF is throttled in a background tab, so an
onComplete callback is a hope), #23 (a step-up with nowhere to go), #31 (CSS
tokens that were never defined). They were all found by looking at the page.
"Looking at the page" is not a test.

So this file drives the real thing: a real server, a real Chromium, a page
loaded while `document.hidden` is true -- because that is how a link someone
was sent is opened. The tab loads while they are still reading something else.

It skips rather than fails where Playwright or the browser is not installed:
this is a demanding test to run and it must not be the reason somebody cannot
run the suite.
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

HIDE = """
  window.__hidden = true;
  Object.defineProperty(document, 'hidden',
    { get: () => window.__hidden, configurable: true });
  Object.defineProperty(document, 'visibilityState',
    { get: () => window.__hidden ? 'hidden' : 'visible', configurable: true });
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    port = _free_port()
    env = {
        "REMIT_DB": str(tmp_path_factory.mktemp("open") / "o.sqlite"),
        "RAZORPAY_KEY_ID": "rzp_test_forthetests",
        "PATH": __import__("os").environ.get("PATH", ""),
        "HOME": __import__("os").environ.get("HOME", "/tmp"),
        "PLAYWRIGHT_BROWSERS_PATH":
            __import__("os").environ.get("PLAYWRIGHT_BROWSERS_PATH", ""),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "remit.api:api",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            with socket.create_connection(("127.0.0.1", port), .25):
                break
        except OSError:
            time.sleep(.25)
    else:
        proc.terminate()
        pytest.skip("the server did not come up")
    yield url
    # uvicorn's reload-less server still takes its time over SIGTERM, and a
    # teardown that raises turns a passing test file into an error.
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:                       # no browser binary here
            pytest.skip(f"chromium is not available: {e}")
        yield b
        b.close()


def test_a_hidden_tab_never_paints_a_stacked_opening(site, browser):
    """FAILURES #35.

    The opening defers itself when the tab is hidden, because rAF is throttled
    there and the timeline would crawl. The comment next to that guard said
    'nothing is hidden in the meantime that they can see', and it was wrong:
    until the timeline sets a start state, every line sits at its CSS default,
    so the wordmark, the expansion, the lab line and both sentences render on
    top of each other. A screenshot of the live site caught it.

    A tab preview, a thumbnail, a link unfurl and the frame between becoming
    visible and the handler running all paint that. It reads as a broken page,
    and it is the first thing anyone sees.
    """
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.add_init_script(HIDE)
    pg = ctx.new_page()
    pg.goto(site, wait_until="domcontentloaded")
    pg.wait_for_timeout(2500)

    intro = pg.locator("#intro")
    assert intro.count() == 1, "the opening is not in the page at all"
    assert pg.evaluate(
        "getComputedStyle(document.getElementById('intro')).opacity") == "0", (
        "a hidden tab is painting the opening; every line is at its CSS "
        "default and they are stacked")
    ctx.close()


def test_the_opening_runs_from_the_top_once_the_tab_is_looked_at(site, browser):
    """The other half: deferring is only correct if it recovers.

    Asserts the start state one frame after visibility (everything at zero, not
    at its default), and that the opening finishes and hands the page over --
    `body[data-intro="done"]` is what every ScrollTrigger downstream waits on.
    """
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.add_init_script(HIDE)
    pg = ctx.new_page()
    pg.goto(site, wait_until="domcontentloaded")
    pg.wait_for_timeout(1500)

    pg.evaluate("window.__hidden = false;"
                "document.dispatchEvent(new Event('visibilitychange'))")
    pg.wait_for_timeout(250)
    start = pg.evaluate(
        "[...document.querySelectorAll('.intro-mark,.intro-lab,.intro-said p')]"
        ".map(e => Number(getComputedStyle(e).opacity))")
    assert start and max(start) < 0.5, (
        f"the opening jumped to its end state instead of starting: {start}")

    # The hard stop is 9600ms and the teardown gets its own 700ms clock.
    pg.wait_for_function("document.body.dataset.intro === 'done'", timeout=15000)
    pg.wait_for_timeout(1200)
    assert pg.locator("#intro").count() == 0, "the opening never tore itself down"
    assert pg.locator("#askForm").is_visible(), "the page did not arrive"
    ctx.close()


def test_a_tab_that_is_never_looked_at_still_gets_the_page(site, browser):
    """The failure mode the fix above could have introduced.

    Holding the opening at zero is right only if the wait can end. Some
    contexts report hidden and never stop -- a headless capture, a prerender,
    an embedded view, a tab restored into the background -- and a page that
    waits forever for a visibilitychange that never comes shows those a black
    rectangle. That is worse than the pile-up the guard exists to prevent.
    """
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.add_init_script(HIDE)                # and never cleared
    pg = ctx.new_page()
    pg.goto(site, wait_until="domcontentloaded")
    pg.wait_for_function("document.body.dataset.intro === 'done'", timeout=20000)
    pg.wait_for_timeout(1200)
    assert pg.locator("#intro").count() == 0, (
        "a tab that is never looked at is still waiting behind the opening")
    assert pg.locator("#askForm").count() == 1

    # And the page it arrives at has to have something in it. The hero is the
    # one region that animates INTO existence, so a throttled timeline leaves
    # a black rectangle -- the page arrived and there was nothing in it.
    painted = pg.evaluate(
        "[...document.querySelectorAll('.headline .in, .sub, .cta-row, #nav')]"
        ".map(e => Number(getComputedStyle(e).opacity))")
    assert painted and min(painted) > .9, (
        f"the hero never arrived in a tab nobody looked at: {painted}")
    ctx.close()


def test_the_hero_offers_the_walkthrough(site, browser):
    """G2: a reviewer landing after the opening had eight rooms and no
    suggestion. The approval walk-through is the thing to press first."""
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    pg.goto(site, wait_until="domcontentloaded")
    pg.wait_for_function("document.body.dataset.intro === 'done'", timeout=15000)
    link = pg.locator('.cta-row a[href="#walk"]')
    assert link.count() == 1 and link.is_visible()
    assert pg.locator("#walkOut .wstep").count() == 5
    ctx.close()
