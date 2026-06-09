"""RealizedTradeSource — abstract interface for the realized-trade stream."""
from __future__ import annotations

import datetime
from typing import Protocol, runtime_checkable

from audit_ledger.schema import ClosedTrade


@runtime_checkable
class RealizedTradeSource(Protocol):
    """Contract every adapter implements to provide the realized stream.

    Implementations: markdown-parser (brk-tasty's pre-B60 path),
    broker-API-anchored (brk-tasty's B60-A reconciliation engine produces
    ClosedTrade entries from broker transactions), SQLite-backed, etc.

    v0 ships with the expectation that this can return an empty list —
    that's the honest-gap framing for platforms that haven't accumulated
    realized matches yet.
    """

    def load_closed(self, start: datetime.date, end: datetime.date) -> list[ClosedTrade]:
        """Return all ClosedTrade entries with closed_date in [start, end]
        inclusive. Empty list is valid (and common at platform inception)."""
        ...
