"""Abstract Protocol interfaces every adapter implements.

Adapters plug in the platform-specific bits (S3-vs-SQLite-vs-NDJSON
ledger storage, the broker SDK of choice, the historical-price source).
The audit_ledger framework consumes only the Protocol shape; it doesn't
know or care what's behind it.
"""
from audit_ledger.adapters.ledger_source import LedgerSource  # noqa: F401
from audit_ledger.adapters.price_source import HistoricalPriceSource  # noqa: F401
from audit_ledger.adapters.realized_source import RealizedTradeSource  # noqa: F401
