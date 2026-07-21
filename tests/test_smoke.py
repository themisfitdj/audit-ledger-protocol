"""Smoke tests for the audit-ledger-protocol package.

Confirms imports resolve and the canonical methodology operations produce
expected output. Heavier behavioral coverage lives in adapter test suites
(e.g. brk-tasty/tests/unit/*) — the framework's role is to be small,
stable, and obviously correct.
"""
import datetime
import hashlib
import json
from decimal import Decimal


# ── Imports resolve ─────────────────────────────────────────────────────────

def test_top_level_imports():
    from audit_ledger.schema import Recommendation, RankedRec, Run, SyntheticOutcome, ClosedTrade
    from audit_ledger.outcomes import (
        compute_synthetic_outcome, pending_outcome, unavailable_outcome,
    )
    from audit_ledger.aggregate import aggregate_by, decile_key, ev_pct_decile_key
    from audit_ledger.match import join_realized
    from audit_ledger.strategies.bull_put_spread import spread_pnl_at_expiry
    from audit_ledger.reconciliation import (
        MatchStatus, Origin, Structure,
        ReconciledTrade, ReconciledTradeLeg, RejectedRecommendation,
        PeriodReconciliation, ReconciliationResult, DiscrepancyError,
        reconcile, period_tie_out,
    )
    from audit_ledger.manifest import (
        SCHEMA_VERSION, build_manifest, canonical_bytes, self_sha256,
        VerificationResult, compute_sha256, verify_manifest_hashes,
        verify_signature, walk_chain,
    )
    from audit_ledger.broker.types import (
        BrokerFill, BrokerLeg, BrokerOrder, BrokerTransaction, NlvPoint,
        parse_option_symbol,
    )
    from audit_ledger.adapters import (
        LedgerSource, HistoricalPriceSource, RealizedTradeSource,
    )
    # RankedRec is the back-compat alias for Recommendation
    assert RankedRec is Recommendation


# ── Bull put spread P&L (three-branch math) ─────────────────────────────────

def test_bull_put_spread_full_win():
    """expiry_spot >= short_strike → full_win, pnl = +net_credit * 100."""
    from audit_ledger.strategies.bull_put_spread import spread_pnl_at_expiry
    r = spread_pnl_at_expiry(expiry_spot=80.0, short_strike=75.0, long_strike=70.0, net_credit=1.50)
    assert r["outcome_class"] == "full_win"
    assert r["pnl_dollars"] == 150.0


def test_bull_put_spread_full_loss():
    """expiry_spot <= long_strike → full_loss."""
    from audit_ledger.strategies.bull_put_spread import spread_pnl_at_expiry
    r = spread_pnl_at_expiry(expiry_spot=65.0, short_strike=75.0, long_strike=70.0, net_credit=1.50)
    assert r["outcome_class"] == "full_loss"
    # spread_width = 5; net_credit = 1.50; loss = (5 - 1.50) * 100 = 350
    assert r["pnl_dollars"] == -350.0


def test_bull_put_spread_partial():
    """long_strike < expiry_spot < short_strike → partial."""
    from audit_ledger.strategies.bull_put_spread import spread_pnl_at_expiry
    r = spread_pnl_at_expiry(expiry_spot=73.0, short_strike=75.0, long_strike=70.0, net_credit=1.50)
    assert r["outcome_class"] == "partial"
    # pnl = (1.50 - (75 - 73)) * 100 = (1.50 - 2.0) * 100 = -50
    assert r["pnl_dollars"] == -50.0


# ── Aggregation primitives ──────────────────────────────────────────────────

def test_decile_key_buckets():
    from audit_ledger.aggregate import decile_key
    assert decile_key(0.05) == "0.0-0.1"
    assert decile_key(0.55) == "0.5-0.6"
    assert decile_key(1.0) == "1.0+"
    assert decile_key(-0.1) == "negative"


# ── Recommendation matching ─────────────────────────────────────────────────

def test_join_realized_matches_on_strike_set():
    from audit_ledger.schema import Recommendation, ClosedTrade
    from audit_ledger.match import join_realized

    rec = Recommendation(
        symbol="USO", confidence="medium", risk_flags=(),
        ev_pct=0.10, iv_rank=80.0,
        short_strike=70.0, long_strike=65.0,
        expiry=datetime.date(2026, 6, 18),
        net_credit=1.50, bp_required=350,
    )
    closed = ClosedTrade(
        closed_date=datetime.date(2026, 6, 10),
        symbol="USO",
        expiry=datetime.date(2026, 6, 18),
        short_strike=70.0, long_strike=65.0,
        entry_credit_per_share=1.50, exit_debit_per_share=0.25,
        pnl_dollars=125.0, is_roll=False, notes="",
    )
    match = join_realized(rec, datetime.date(2026, 6, 1), [closed])
    assert match is closed


def test_join_realized_excludes_rolls():
    from audit_ledger.schema import Recommendation, ClosedTrade
    from audit_ledger.match import join_realized

    rec = Recommendation(
        symbol="USO", confidence="medium", risk_flags=(),
        ev_pct=0.10, iv_rank=80.0,
        short_strike=70.0, long_strike=65.0,
        expiry=datetime.date(2026, 6, 18),
        net_credit=1.50, bp_required=350,
    )
    rolled = ClosedTrade(
        closed_date=datetime.date(2026, 6, 10), symbol="USO",
        expiry=datetime.date(2026, 6, 18),
        short_strike=70.0, long_strike=65.0,
        entry_credit_per_share=1.50, exit_debit_per_share=0.25,
        pnl_dollars=125.0, is_roll=True, notes="rolled to July",
    )
    assert join_realized(rec, datetime.date(2026, 6, 1), [rolled]) is None


# ── Manifest builder + signature verification ───────────────────────────────

def test_canonical_bytes_is_sort_keyed_and_compact():
    """The signing operation depends on canonical_bytes being deterministic
    across semantically-equivalent inputs."""
    from audit_ledger.manifest import canonical_bytes
    a = {"x": 1, "y": [{"b": 2, "a": 1}]}
    b = {"y": [{"a": 1, "b": 2}], "x": 1}
    assert canonical_bytes(a) == canonical_bytes(b)
    assert b" " not in canonical_bytes(a)
    assert b"\n" not in canonical_bytes(a)


def test_self_sha256_matches_canonical_hash():
    from audit_ledger.manifest import canonical_bytes, self_sha256
    m = {"schema_version": "1.0", "records": []}
    assert self_sha256(m) == hashlib.sha256(canonical_bytes(m)).hexdigest()


def test_verify_signature_round_trip():
    """Sign with a locally-generated keypair; confirm the published verifier
    accepts a valid signature and rejects a tampered one. This is the load-
    bearing external-verification contract."""
    import base64
    from audit_ledger.manifest import canonical_bytes, verify_signature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    manifest = {"schema_version": "1.0", "records": []}
    cbytes = canonical_bytes(manifest)
    sig = priv.sign(
        cbytes,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )
    sig_b64 = base64.b64encode(sig).decode("ascii")

    assert verify_signature(cbytes, sig_b64, pem) is True

    tampered = canonical_bytes({"schema_version": "1.0", "records": [{"key": "fake"}]})
    assert verify_signature(tampered, sig_b64, pem) is False


def test_walk_chain_validates_two_day_sequence():
    from audit_ledger.manifest import canonical_bytes, walk_chain

    m1 = {
        "schema_version": "1.0",
        "generated_at": "2026-06-08T23:30:00+00:00",
        "prior_manifest_sha256": None,
        "gap_days": 0,
        "records": [],
    }
    m1_self = hashlib.sha256(canonical_bytes(m1)).hexdigest()
    m2 = {
        "schema_version": "1.0",
        "generated_at": "2026-06-09T23:30:00+00:00",
        "prior_manifest_sha256": m1_self,
        "gap_days": 0,
        "records": [],
    }
    result = walk_chain([m1, m2])
    assert result["valid"] is True
    assert result["genesis_present"] is True
    assert result["broken_links"] == []


# ── Reconciliation engine (smoke; comprehensive coverage in brk-tasty) ─────

def test_reconcile_empty_inputs_produces_empty_result():
    from audit_ledger.reconciliation import reconcile
    r = reconcile(recs=[], orders=[], transactions=[], open_positions=[])
    assert r.trades == []
    assert r.rejected == []


def test_reconcile_rec_with_no_orders_is_recommended_not_taken():
    from audit_ledger.reconciliation import reconcile, MatchStatus
    rec = {
        "recommendation_id": "rec-001",
        "run_id": "run-001",
        "thesis_id": "test",
        "thesis_version": "v1",
        "symbol": "USO",
        "suggested_strikes": {
            "short": 70.0, "long": 65.0,
            "expiry": "2026-06-18", "net_credit": 1.5, "bp_required": 350,
        },
    }
    r = reconcile(recs=[rec], orders=[], transactions=[], open_positions=[])
    assert len(r.trades) == 1
    assert r.trades[0].match_status == MatchStatus.RECOMMENDED_NOT_TAKEN.value


# ── Period tie-out math ─────────────────────────────────────────────────────

def test_period_tie_out_with_no_money_movements():
    from audit_ledger.broker.types import NlvPoint
    from audit_ledger.reconciliation import period_tie_out

    nlv = [
        NlvPoint(date=datetime.date(2026, 6, 1),
                 open=Decimal("100"), high=Decimal("100"),
                 low=Decimal("100"), close=Decimal("100")),
        NlvPoint(date=datetime.date(2026, 6, 30),
                 open=Decimal("102"), high=Decimal("102"),
                 low=Decimal("102"), close=Decimal("102")),
    ]
    result = period_tie_out(
        start=datetime.date(2026, 6, 1),
        end=datetime.date(2026, 6, 30),
        reconciled_trades=[], money_movements=[], nlv_points=nlv,
    )
    # $2 unaccounted residual exceeds the $1 floor → discrepancy fires
    assert result.nlv_delta == Decimal("2")
    assert result.residual == Decimal("2")
    assert result.has_discrepancy() is True


# ── Adapter Protocols are runtime-checkable ────────────────────────────────

def test_adapter_protocols_are_runtime_checkable():
    """isinstance(obj, LedgerSource) works at runtime — duck-typed protocols."""
    from audit_ledger.adapters import LedgerSource, HistoricalPriceSource, RealizedTradeSource

    class FakeLedger:
        def load_runs(self, start, end):
            return []

    class FakePrice:
        def close_at(self, symbol, date):
            return None

    class FakeRealized:
        def load_closed(self, start, end):
            return []

    assert isinstance(FakeLedger(), LedgerSource)
    assert isinstance(FakePrice(), HistoricalPriceSource)
    assert isinstance(FakeRealized(), RealizedTradeSource)


# ── v0.2.0 multi-strategy P&L modules ──────────────────────────────────────────

def test_new_strategy_modules_import_and_export():
    """The four non-bull-put structures resolve via the package __init__."""
    from audit_ledger.strategies import (
        long_put_pnl_at_expiry,
        bear_call_spread_pnl_at_expiry,
        put_debit_spread_pnl_at_expiry,
    )
    from audit_ledger.strategies.long_call import long_call_pnl_at_expiry
    from audit_ledger.strategies.call_debit_spread import call_debit_spread_pnl_at_expiry

    assert callable(long_put_pnl_at_expiry)
    assert callable(long_call_pnl_at_expiry)
    assert callable(bear_call_spread_pnl_at_expiry)
    assert callable(put_debit_spread_pnl_at_expiry)
    assert callable(call_debit_spread_pnl_at_expiry)


def _assert_outcome_shape(result):
    assert set(result) == {"pnl_dollars", "pnl_pct_bp", "outcome_class"}
    assert isinstance(result["outcome_class"], str) and result["outcome_class"]


def test_long_call_itm_is_a_win():
    from audit_ledger.strategies.long_call import long_call_pnl_at_expiry
    r = long_call_pnl_at_expiry(expiry_spot=110.0, strike=100.0, premium_paid=5.0)
    _assert_outcome_shape(r)
    assert r["pnl_dollars"] > 0  # 10 intrinsic - 5 premium, net positive


def test_long_put_itm_is_a_win():
    from audit_ledger.strategies.long_put import long_put_pnl_at_expiry
    r = long_put_pnl_at_expiry(expiry_spot=90.0, strike=100.0, premium_paid=5.0)
    _assert_outcome_shape(r)
    assert r["pnl_dollars"] > 0


def test_call_debit_spread_both_itm_is_a_win():
    from audit_ledger.strategies.call_debit_spread import call_debit_spread_pnl_at_expiry
    r = call_debit_spread_pnl_at_expiry(
        expiry_spot=110.0, bought_strike=100.0, sold_strike=105.0, net_debit=2.0
    )
    _assert_outcome_shape(r)
    assert r["pnl_dollars"] > 0  # 5 width - 2 debit


def test_bear_call_spread_below_short_is_a_win():
    from audit_ledger.strategies.bear_call_spread import bear_call_spread_pnl_at_expiry
    r = bear_call_spread_pnl_at_expiry(
        expiry_spot=90.0, short_strike=100.0, long_strike=105.0, net_credit=2.0
    )
    _assert_outcome_shape(r)
    assert r["pnl_dollars"] > 0  # both legs expire worthless, keep the credit


def test_put_debit_spread_below_sold_is_a_win():
    from audit_ledger.strategies.put_debit_spread import put_debit_spread_pnl_at_expiry
    r = put_debit_spread_pnl_at_expiry(
        expiry_spot=90.0, bought_strike=100.0, sold_strike=95.0, net_debit=2.0
    )
    _assert_outcome_shape(r)
    assert r["pnl_dollars"] > 0  # 5 width - 2 debit


def test_strategy_data_unavailable_on_nonpositive_cost():
    """A non-positive premium/debit/credit yields the data_unavailable class."""
    from audit_ledger.strategies.long_call import long_call_pnl_at_expiry
    r = long_call_pnl_at_expiry(expiry_spot=110.0, strike=100.0, premium_paid=0.0)
    assert r["outcome_class"] == "data_unavailable"
    assert r["pnl_dollars"] is None


def test_outcome_for_structure_dispatch_is_in_package():
    """The structure dispatcher lives in the package (no platform dependency)
    and routes each discriminator to the right computer."""
    import pytest

    from audit_ledger.strategies.dispatch import outcome_for_structure

    bull = outcome_for_structure(
        "bull_put_spread", expiry_spot=110.0,
        short_strike=100.0, long_strike=95.0, net_credit=1.5,
    )
    _assert_outcome_shape(bull)
    # empty / None route to the bull-put computer (backward compat).
    assert outcome_for_structure(
        None, expiry_spot=110.0, short_strike=100.0, long_strike=95.0, net_credit=1.5,
    ) == bull
    # a directional structure routes correctly.
    lc = outcome_for_structure("long_call", expiry_spot=110.0, strike=100.0, premium_paid=5.0)
    assert lc["pnl_dollars"] > 0
    # unknown discriminator fails loud.
    with pytest.raises(ValueError):
        outcome_for_structure("iron_condor", expiry_spot=100.0)
