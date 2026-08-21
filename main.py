"""Vercel entry point.

Vercel detects FastAPI from requirements.txt, looks for an ASGI app called
`app` in a supported entrypoint (main.py at the root is one), and routes every
request to it with the path intact. That last part matters: an earlier version
of this repo used a catch-all rewrite in vercel.json, which overwrote the path
with the destination before the app saw it, so every request arrived as
/api/index and nothing matched. The framework preset is the correct mechanism
and needs no vercel.json at all.

REMIT is one FastAPI application, so this file is just an import: the same
object serves the CLI demo, the tests, the evaluation harness and the site.
"""
from __future__ import annotations

from remit.api import api as app          # noqa: F401
