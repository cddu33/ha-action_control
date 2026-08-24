"""Anti-loop mechanism: track service calls issued by this integration itself.

Every retry, escalation, or post-escalation replay this integration performs
is executed with a freshly created ``Context``. The ``call_service`` event
that Home Assistant fires for that call carries the same context id, so the
event listener can recognize and ignore it before it re-triggers the same
(or any other) rule. This fully replaces the need for an external guard
entity (e.g. a helper switch that must be "on" for N minutes) that the
original hand-written automations relied on.
"""
from __future__ import annotations

import time

from homeassistant.core import Context

from .const import CONTEXT_TTL


class SelfIssuedContexts:
    """Remembers context ids created by this integration, with TTL eviction."""

    def __init__(self, ttl: float = CONTEXT_TTL) -> None:
        self._ttl = ttl
        self._expiry: dict[str, float] = {}

    def new_context(self) -> Context:
        """Create a context, remember it, and return it for use in a service call."""
        ctx = Context()
        self._expiry[ctx.id] = time.monotonic() + self._ttl
        self._prune()
        return ctx

    def is_self_issued(self, context_id: str | None) -> bool:
        """Return True if this context id was created by ``new_context``."""
        if context_id is None:
            return False
        self._prune()
        return context_id in self._expiry

    def _prune(self) -> None:
        now = time.monotonic()
        expired = [cid for cid, exp in self._expiry.items() if exp <= now]
        for cid in expired:
            del self._expiry[cid]
