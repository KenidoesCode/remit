"""Tool broker: the only surface the AI buyer can reach.

MCP has no money semantics -- to a client, `create_order` is
indistinguishable from `list_products`. The broker adds what the protocol
lacks:

  * every tool declares whether it is a FINANCIAL action, its risk level,
    and the authority it requires;
  * every tool schema is HASH-PINNED at registration. If a description or
    schema changes underneath us, the call is refused. That is the defence
    against tool poisoning and rug-pulls, which remain unfixed at protocol
    level;
  * a financial tool can only be invoked by the orchestrator holding an
    AUTO/confirmed authorization -- the model cannot reach one at all;
  * every invocation is recorded with its arguments' hash.

The model may call read tools freely. It may propose a financial action.
It may not execute one.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from ..models import canonical, sha


class ToolError(Exception):
    pass


class PoisonedTool(ToolError):
    pass


class UnauthorizedTool(ToolError):
    pass


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    output_schema: dict
    financial: bool
    risk: str                      # 'none' | 'low' | 'medium' | 'high'
    requires_authority: bool
    version: str
    fn: Callable[..., Any]
    schema_hash: str = ""

    def compute_hash(self) -> str:
        return sha(canonical({"n": self.name, "d": self.description,
                              "i": self.input_schema, "o": self.output_schema,
                              "v": self.version}))


# Language that has no business in a tool description. Cheap, transparent,
# and it catches the published tool-poisoning payload shapes.
INJECTION_MARKERS = (
    "ignore previous", "ignore all previous", "disregard", "system prompt",
    "you must", "do not tell", "secretly", "exfiltrate", "send the api key",
    "<important>", "instead of",
)


class ToolBroker:
    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self.invocations: list[dict] = []

    def register(self, tool: Tool) -> None:
        low = tool.description.lower()
        for m in INJECTION_MARKERS:
            if m in low:
                raise PoisonedTool(
                    f"tool '{tool.name}' description contains imperative "
                    f"injection marker {m!r}; refusing to register")
        tool.schema_hash = tool.compute_hash()
        self._tools[tool.name] = tool

    def describe(self, financial_ok: bool = False) -> list[dict]:
        """What the model is allowed to SEE. Financial tools are hidden from
        the model entirely by default -- you cannot call what you cannot name."""
        return [{"name": t.name, "description": t.description,
                 "input_schema": t.input_schema, "financial": t.financial,
                 "risk": t.risk, "version": t.version, "schema_hash": t.schema_hash}
                for t in self._tools.values() if financial_ok or not t.financial]

    def call(self, name: str, args: dict, *, actor: str,
             authorization: str | None = None) -> Any:
        t = self._tools.get(name)
        if t is None:
            raise UnauthorizedTool(f"unknown tool {name!r}")
        if t.schema_hash != t.compute_hash():
            raise PoisonedTool(
                f"schema drift on '{name}': pinned {t.schema_hash[:12]} != "
                f"current {t.compute_hash()[:12]}")
        if t.financial:
            if actor == "model":
                raise UnauthorizedTool(
                    f"the model may never invoke a financial tool ({name})")
            if t.requires_authority and authorization not in ("AUTO", "CONFIRMED"):
                raise UnauthorizedTool(
                    f"financial tool {name} requires an AUTO or CONFIRMED "
                    f"authorization, got {authorization!r}")
        missing = [k for k in t.input_schema.get("required", []) if k not in args]
        if missing:
            raise ToolError(f"{name}: missing required args {missing}")
        self.invocations.append({"tool": name, "actor": actor,
                                 "args_hash": sha(canonical(args)),
                                 "financial": t.financial,
                                 "authorization": authorization})
        return t.fn(**args)
