"""A recommendation with no matching broker trade (``recommended_not_taken``)
must carry the recommendation's own ``structure_type``, like the matched branch.

Reported by the brk-tasty adapter (BRK-501): every not-taken row was labelled
``bull_put_spread`` regardless of its structure, so 1,107 of 1,107 not-taken rows
from a premium-BUYING thesis (long calls, call/put debit spreads, long puts) were
recorded as a credit spread on 2026-10-02.
"""
import pytest

from audit_ledger.reconciliation import MatchStatus, Structure, reconcile


def _rec(**extra):
    rec = {"recommendation_id": "r1", "run_id": "run-1", "thesis_id": "t", "symbol": "XYZ",
           "suggested_strikes": {"bought": 100.0, "sold": 105.0, "expiry": "2026-12-18",
                                 "net_debit": 1.5}}
    rec.update(extra)
    return rec


def _not_taken(rec):
    trades = reconcile(recs=[rec], orders=[], transactions=[], open_positions=[]).trades
    assert len(trades) == 1 and trades[0].match_status == MatchStatus.RECOMMENDED_NOT_TAKEN.value
    return trades[0]


@pytest.mark.parametrize("structure", [s.value for s in Structure if s is not Structure.UNKNOWN])
def test_not_taken_row_carries_the_recs_structure(structure):
    assert _not_taken(_rec(structure_type=structure)).structure == structure


def test_legacy_rec_without_structure_type_stays_bull_put_spread():
    """Same default as the matched branch, so pre-structure_type ledgers are unchanged."""
    assert _not_taken(_rec()).structure == Structure.BULL_PUT_SPREAD.value


@pytest.mark.parametrize("bad", ["not_a_structure", "", None, 7])
def test_unrecognised_structure_type_is_unknown_not_a_crash(bad):
    rec = _rec(structure_type=bad)
    expected = Structure.BULL_PUT_SPREAD.value if bad in ("", None) else Structure.UNKNOWN.value
    assert _not_taken(rec).structure == expected
