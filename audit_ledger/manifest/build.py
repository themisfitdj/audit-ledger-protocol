"""B55 — Ledger manifest builder.

Lists every object under the provided prefixes in the ledger bucket, computes
the SHA-256 of each, and assembles a canonical JSON manifest with hash-chain
metadata. The manifest is then signed via KMS (see clients/kms_signer) and
published to GitHub (see clients/github_publisher). The chain enables an
external observer to verify, with only the GitHub-published files and no AWS
access, that the publisher's signed claim about ledger contents at time T is
the same one anchored in yesterday's manifest.

Pure-Python module — no IO except the s3_client passed in. The Lambda handler
in scripts/manifest_lambda.py wires this up to KMS + GitHub.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Iterable


SCHEMA_VERSION = "1.0"

# The manifest must only ingest date-prefixed daily entries. Non-date
# prefixes (e.g. an ad-hoc test artifact at a ledger/<label>/... key) get
# filtered out so the externally-published audit trail does not include
# synthetic test data. Object Lock prevents cleanup of existing test
# artifacts; the filter enforces the convention.
_DATE_SUBPATH = re.compile(r"\d{4}-\d{2}-\d{2}/.+")


def _is_date_prefixed(key: str, prefix: str) -> bool:
    """True iff the key sits under <prefix><YYYY-MM-DD>/<file>."""
    if not key.startswith(prefix):
        return False
    remainder = key[len(prefix):]
    return bool(_DATE_SUBPATH.match(remainder))


def canonical_bytes(manifest: dict) -> bytes:
    """Deterministic canonical encoding: sort keys, compact separators, no
    insignificant whitespace. Identical semantic content → identical bytes
    → identical KMS signature → identical verification."""
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")


def self_sha256(manifest: dict) -> str:
    """SHA-256 of the manifest's canonical bytes. Used as the value the KMS
    sign operation actually signs, and as the chain anchor referenced by the
    NEXT day's manifest in prior_manifest_sha256."""
    return hashlib.sha256(canonical_bytes(manifest)).hexdigest()


def _list_prefix(s3_client, bucket: str, prefix: str) -> list[dict]:
    """Paginate every object under a prefix. Returns the raw S3 metadata
    list — does not fetch bodies yet. Filters out non-date-prefixed keys
    (e.g. ad-hoc test artifacts) so the audit trail stays clean."""
    paginator = s3_client.get_paginator("list_objects_v2")
    out = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents") or []:
            if _is_date_prefixed(obj["Key"], prefix):
                out.append(obj)
    return out


def _hash_object(s3_client, bucket: str, key: str) -> str:
    body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
    return hashlib.sha256(body).hexdigest()


def _retention(s3_client, bucket: str, key: str) -> tuple[str | None, str | None]:
    """Per-object retention metadata. None/None if Object Lock not set on
    the object."""
    try:
        resp = s3_client.get_object_retention(Bucket=bucket, Key=key)
        ret = resp.get("Retention") or {}
        mode = ret.get("Mode")
        until = ret.get("RetainUntilDate")
        until_iso = until.isoformat() if hasattr(until, "isoformat") else (str(until) if until else None)
        return mode, until_iso
    except Exception:
        # Object without retention (or permission missing). Don't fail the
        # whole manifest — record None and let the verifier surface the gap.
        return None, None


def build_manifest(
    s3_client,
    bucket: str,
    prefixes: Iterable[str],
    generated_at: datetime,
    prior_manifest_sha256: str | None,
    gap_days: int = 0,
) -> dict:
    """Assemble the manifest dict. Does NOT sign — that's the caller's
    responsibility via clients/kms_signer.

    prior_manifest_sha256: SHA-256 of yesterday's signed manifest's canonical
        bytes. None on genesis day.
    gap_days: number of days between the prior manifest and this one. Zero on
        normal sequential days; nonzero when the chain skipped (cron outage,
        infrastructure migration, etc.). Verifiers SHOULD flag nonzero
        gap_days as an audit signal.
    """
    records: list[dict] = []
    for prefix in prefixes:
        for obj in _list_prefix(s3_client, bucket, prefix):
            key = obj["Key"]
            size = obj["Size"]
            last_modified = obj["LastModified"]
            sha256 = _hash_object(s3_client, bucket, key)
            mode, until_iso = _retention(s3_client, bucket, key)
            records.append({
                "key": key,
                "sha256": sha256,
                "size": size,
                "last_modified": last_modified.isoformat() if hasattr(last_modified, "isoformat") else str(last_modified),
                "object_lock_mode": mode,
                "retain_until": until_iso,
            })

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "bucket": bucket,
        "prefixes": sorted(set(prefixes)),
        "prior_manifest_sha256": prior_manifest_sha256,
        "gap_days": gap_days,
        "records": records,
    }
