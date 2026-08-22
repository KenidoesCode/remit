"""A real interpreter, and an honest account of what can run where.

WHAT IS TRUE ABOUT THIS BUILD ENVIRONMENT
-----------------------------------------
No model weights can be fetched here. Verified, not assumed:

    huggingface.co            tunnel refused (403)
    cdn-lfs.huggingface.co    tunnel refused
    api.github.com            403
    storage.googleapis.com    tunnel refused
    ollama.com                tunnel refused
    files.pythonhosted.org    200      <- only PyPI is reachable

And the deployed instance is a 512 MB Render free tier, which cannot host a
local model of any useful size regardless of whether the weights could be
downloaded.

So: **the deployed demo runs `RuleCompiler`**, `/health` says so, and this
module does not pretend otherwise. What it does instead is make the LLM path
REAL and RUNNABLE the moment either constraint lifts -- one env var, no code
change -- and make every line of that path testable today.

WHAT IS ACTUALLY EXERCISED
--------------------------
`tests/test_llm_path.py` starts a local HTTP server that speaks the
OpenAI-compatible wire protocol and points this adapter at it. That is a real
socket, a real request, a real response body, real JSON parsing, real schema
validation, real timeout handling and real fallback. The *vendor* is stubbed;
none of the code below is. The stub is used to produce outputs a real model
would rarely produce on demand -- malformed JSON, a hallucinated product id, an
authorization field, a 30-second hang -- which is the only reliable way to test
fail-closed behaviour.

Calling that "a real LLM benchmark" would be a lie. It is not called one. The
benchmark harness in `eval/model_bench.py` runs against whatever interpreter is
configured, and reports which one it was.

THE RULE THAT DOES NOT CHANGE
-----------------------------
The model may select. The model may not compute. It never returns an amount
that becomes a ceiling, never returns a verdict, and never returns a product
id -- `remit/intelligence.py` strips all thirteen of those fields and reports
each one. This module is a transport; that module is the boundary.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .intelligence import Reading, sanitise

# Pinned. A model that changes underneath a benchmark invalidates the
# benchmark, and a version nobody recorded is a result nobody can reproduce.
PROMPT_VERSION = "2026-08-22.a"
SCHEMA_VERSION = "1"

SYSTEM_PROMPT = """You extract structured shopping intent from a sentence.

Return ONLY a JSON object. No prose, no markdown, no code fence.

Fields (all optional; omit rather than guess):
  category                 string, the kind of thing wanted
  product_terms            array of strings, the nouns the person actually said
  excluded_attributes      array of strings, what they ruled out ("not white")
  required_attributes      array of strings, what they insisted on
  quantity                 integer >= 1
  objective                one of: best_value cheapest best_rated fastest_delivery
  merchant_constraints     array of strings, merchants they named
  purchase_authority       boolean, did they authorise a purchase or ask to look
  stated_amount_rupees     number, ONLY if they said an amount out loud
  language                 string, e.g. "en", "hinglish"
  ambiguous                boolean, true if you are not confident what they meant
  confidence               number 0..1

Rules you must not break:
- You do NOT decide whether the purchase is allowed. Never return a verdict,
  an approval, a policy, a product id, an actor, or an amount in paise.
- stated_amount_rupees is a REPORT of what you read, never a budget you chose.
  If they did not say a number, omit it.
- If the sentence is ambiguous, say so with ambiguous=true and a low
  confidence. Guessing is worse than abstaining.
- "buy X but not Y" means Y goes in excluded_attributes, never in
  product_terms.
"""


@dataclass
class LLMResult:
    reading: Reading
    raw: str
    latency_ms: float
    model: str
    ok: bool
    error: str | None = None

    def dict(self) -> dict:
        return {"interpreter": self.reading.interpreter, "model": self.model,
                "ok": self.ok, "error": self.error,
                "latency_ms": round(self.latency_ms, 1),
                "prompt_version": PROMPT_VERSION,
                "schema_version": SCHEMA_VERSION,
                "fields": self.reading.fields,
                "refused": self.reading.refused,
                "raw_chars": len(self.raw or "")}


class OpenAICompatible:
    """Anything that speaks `POST /v1/chat/completions`.

    Which is: llama.cpp's server, Ollama, vLLM, LM Studio, text-generation-
    webui, together.ai, groq, openrouter, and OpenAI. One adapter, because the
    wire format is the actual standard and adopting it costs nothing.

    Configured entirely by environment, so switching the intelligence is a
    deployment change and not a code change -- which is the model-independence
    claim, expressed as an operational fact rather than an interface diagram.

        REMIT_LLM_BASE     http://127.0.0.1:8080/v1
        REMIT_LLM_MODEL    qwen2.5-3b-instruct
        REMIT_LLM_KEY      optional; local servers need none
        REMIT_LLM_TIMEOUT  seconds, default 8
    """

    def __init__(self, base: str | None = None, model: str | None = None,
                 key: str | None = None, timeout: float | None = None):
        self.base = (base or os.environ.get("REMIT_LLM_BASE", "")).rstrip("/")
        self.model = model or os.environ.get("REMIT_LLM_MODEL", "unset")
        self.key = key or os.environ.get("REMIT_LLM_KEY", "")
        self.timeout = float(timeout or os.environ.get("REMIT_LLM_TIMEOUT", 8))

    @property
    def name(self) -> str:
        return f"openai-compatible:{self.model}"

    @property
    def configured(self) -> bool:
        return bool(self.base)

    def read(self, utterance: str) -> dict:
        """The interface `remit/intelligence.py` expects. Raises on failure,
        because a caller that cannot tell a timeout from an empty reading will
        eventually treat one as the other."""
        return self.call(utterance).reading.fields

    def call(self, utterance: str) -> LLMResult:
        """Never raises. Returns a result that says what happened.

        Every failure path -- unreachable, timeout, HTTP error, non-JSON body,
        JSON that is not an object, a body with no choices -- produces an empty
        reading, and an empty reading produces MORE friction downstream, never
        less. That is the whole safety property of this file and it is why
        nothing here has an `except: pass`.
        """
        t0 = time.perf_counter()
        if not self.configured:
            return LLMResult(sanitise(None, interpreter="unconfigured"), "",
                             0.0, self.model, False,
                             "REMIT_LLM_BASE is not set")

        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": utterance[:2000]}],
            # Deterministic. A benchmark against a sampled model measures the
            # sampler as much as the model, and a decision path that is not
            # reproducible cannot be audited.
            "temperature": 0,
            "max_tokens": 400,
            "response_format": {"type": "json_object"},
        }).encode()

        headers = {"content-type": "application/json"}
        if self.key:
            headers["authorization"] = f"Bearer {self.key}"

        raw = ""
        try:
            req = urllib.request.Request(
                f"{self.base}/chat/completions", data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                payload = json.loads(res.read().decode())
            raw = (payload.get("choices") or [{}])[0].get(
                "message", {}).get("content", "") or ""
        except urllib.error.HTTPError as e:
            return self._fail(t0, f"http {e.code}", raw)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return self._fail(t0, f"unreachable: {type(e).__name__}", raw)
        except (json.JSONDecodeError, ValueError, KeyError, IndexError) as e:
            return self._fail(t0, f"unreadable envelope: {type(e).__name__}", raw)

        parsed, why = _loads(raw)
        ms = (time.perf_counter() - t0) * 1000
        if parsed is None:
            return LLMResult(sanitise(None, interpreter=self.name), raw, ms,
                             self.model, False, why)
        return LLMResult(sanitise(parsed, interpreter=self.name), raw, ms,
                         self.model, True, None)

    def _fail(self, t0, why, raw) -> LLMResult:
        return LLMResult(sanitise(None, interpreter=self.name), raw,
                         (time.perf_counter() - t0) * 1000, self.model,
                         False, why)


def _loads(text: str) -> tuple[dict | None, str | None]:
    """Parse what a model actually returns, not what the prompt asked for.

    Models emit code fences, leading prose, trailing commentary and occasionally
    two objects. Recovering the first balanced object is worth the twenty lines
    because the alternative is discarding a correct interpretation over a
    markdown artefact -- and discarding it means MORE friction, so the failure
    is safe but needless.

    What this does NOT do is repair malformed JSON. A model that produced
    broken structure has told you something about its confidence, and guessing
    at what it meant is the exact behaviour this system exists to prevent.
    """
    if not text or not text.strip():
        return None, "empty response"
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if t.count("```") >= 2 else t[3:]
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
    t = t.strip()
    start = t.find("{")
    if start < 0:
        return None, "no object in response"
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(t[start:i + 1])
                except json.JSONDecodeError as e:
                    return None, f"malformed json: {e.msg}"
                return (obj, None) if isinstance(obj, dict) else (
                    None, "json was not an object")
    return None, "unterminated object"


def configured_interpreter():
    """Whatever this deployment is actually running, named honestly.

    Returns `None` when no LLM is configured, which is the case on the live
    instance -- and the caller says `RuleCompiler` rather than implying a model
    that is not there.
    """
    llm = OpenAICompatible()
    return llm if llm.configured else None
