"""LedgerSource — abstract interface for adapter ledger backends."""
from __future__ import annotations

import datetime
from typing import Protocol, runtime_checkable

from audit_ledger.schema import Run


@runtime_checkable
class LedgerSource(Protocol):
    """Contract every adapter implements to feed the framework Runs.

    Implementations: S3-backed (brk-tasty's `load_ledger_runs`), local-file
    (NDJSON or SQLite), API-backed (a hosted ledger service), etc.
    """

    def load_runs(self, start: datetime.date, end: datetime.date) -> list[Run]:
        """Return all Run entries with timestamp in [start, end] inclusive.

        Implementations decide: chronological order, dedup behavior on
        re-runs, handling of pre-cutover archival data.
        """
        ...
