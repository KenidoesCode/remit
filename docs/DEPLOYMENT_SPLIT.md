# Split deployment: Vercel front end, Render engine

The page loads from a CDN. The decisions still come from the Python service.

```
   visitor ──► Vercel (static)         ──► instant first paint
                  │
                  └─ fetch ──► Render (FastAPI) ──► the actual engine
```

## Why

Render's free tier spins a service down after ~15 minutes idle, and the next
visitor waits ~50 seconds for it to wake. Serving the page from Vercel means
the opening, the hero and the install command appear immediately.

**This does not remove the wait, it relocates it.** REMIT is data-driven: the
eyebrow, the stats, the executive summary, the arena and every room read from
the API. A cold backend still takes ~50s to answer, and the page will sit on
"connecting…" until it does. The only thing that removes that wait is keeping
the service awake — see [Keeping Render warm](#keeping-render-warm).

## Setting it up

### 1. Vercel

Import the GitHub repository and set **Root Directory** to `web`. There is no
build step: the front end is hand-written HTML, CSS and JS with no bundler.

| Setting | Value |
|---|---|
| Framework preset | Other |
| Root directory | `web` |
| Build command | *(none)* |
| Output directory | `.` |
| Install command | *(none)* |

`web/vercel.json` sets the cache and security headers.

### 2. Tell the API which origin may talk to it

On the Render service, add:

```
REMIT_ALLOWED_ORIGINS = https://<your-project>.vercel.app
```

Comma-separated if there is more than one (a preview domain, a custom domain).

**This is the whole security boundary, so it is an exact list.** No wildcards.
`https://` and `http://` are different origins, and so is a different
subdomain — `tests/test_cross_origin.py` asserts all of that, including that a
look-alike host like `remit.vercel.app.evil.example` is refused.

Without this variable, nothing changes: no CORS middleware is installed at all,
which is the correct default and needed no code.

### 3. Nothing else

The front end works out where the engine lives on its own:

```js
window.REMIT_API_BASE = same origin when the Python service serves the page,
                        https://remit-vvug.onrender.com otherwise
```

## What this changes about security

Honestly, because it is a real trade rather than a free win.

**The session cookie becomes `SameSite=None; Secure`.** It has to: a `Lax`
cookie is *not sent* on a cross-site XHR, so every call would mint a fresh
principal and exposure limits, revocation and audit scoping would all silently
stop working. That failure looks exactly like success — 200s all the way down —
which is what makes it worth spelling out. It is FAILURES #51 one layer up.

**So CSRF protection moves from the cookie policy to CORS.** That holds here
because every state-changing route takes a JSON body, and a cross-origin JSON
`POST` triggers a preflight that an unlisted origin fails.

> **If a form-encoded endpoint is ever added to this API, that reasoning breaks
> and this configuration becomes unsafe.** Simple form posts do not preflight.
> This paragraph is the thing that was wrong, if it ever is.

The cookie stays `httpOnly`. Nothing here lets a script read the session, and
nothing here lets a caller choose a principal — `test_cross_origin.py` asserts
the last one directly.

## Keeping Render warm

A scheduled request every ~10 minutes stops the service idling out. Any pinger
works; the cheapest target is:

```
GET https://remit-vvug.onrender.com/health
```

**Watch the free-tier arithmetic.** Render gives 750 instance-hours per month
across the whole account. Keeping one service awake 24/7 uses about 730 of
them, which will spin your *other* free services down instead. Pinging only
until a deadline, or only during waking hours, is usually the right call.

## Which deployment is which

| Path | What it is |
|---|---|
| `web/` | the front end. Served by Render today, and by Vercel under this setup |
| `deploy/site/` | **superseded.** An older static-only mirror that answered a few endpoints from canned JSON and showed "engine not attached" for the rest. Kept for reference; do not deploy it |

The difference matters: `deploy/site/` is a *screenshot* of REMIT. This setup is
the real engine with a faster front door.
