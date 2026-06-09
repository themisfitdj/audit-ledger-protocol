"""HistoricalPriceSource — abstract interface for historical close prices."""
from __future__ import annotations

import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class HistoricalPriceSource(Protocol):
    """Contract every adapter implements to provide expiry-day closes.

    Implementations: Polygon (Massive), Yahoo, Alpha Vantage, broker-native,
    Bloomberg, etc. Returning None signals data unavailable — the
    framework surfaces this as `outcome_class = data_unavailable` rather
    than silently dropping the recommendation.
    """

    def close_at(self, symbol: str, date: datetime.date) -> float | None:
        """Return the historical close price for `symbol` on `date`, or
        None if the data source can't provide it."""
        ...
