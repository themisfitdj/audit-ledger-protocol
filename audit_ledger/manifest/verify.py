"""Manifest signature + chain + hash verification.

External verifier library — anyone with the GitHub-published files
(manifest.json + manifest.json.sig + public_key.pem) and the `cryptography`
library can:

  1. Verify the manifest's signature is valid (signed by the holder of the
     KMS private key — proves the manifest body has not been tampered with)
  2. Walk a chain of daily manifests and confirm each one's
     prior_manifest_sha256 matches the prior file's canonical-bytes SHA-256
     (chain integrity)
  3. (Optional, auditor-only with bucket read access) Spot-check each
     manifest record's SHA-256 against the actual S3 object body

Independence-of-AWS verification: steps 1 and 2 require ZERO AWS access.
Step 3 is auditor-only.

CLI lives in the adapter (e.g. brk-tasty's scripts/verify_ledger_manifest.py).
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.exceptions import InvalidSignature


@dataclass
class VerificationResult:
    verified: list[str]
    hash_mismatches: list[dict]
    missing_in_s3: list[str]
    orphans_in_s3: list[str]

    def has_failures(self) -> bool:
        return bool(self.hash_mismatches or self.missing_in_s3 or self.orphans_in_s3)


def compute_sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _canonical_bytes(manifest: dict) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_signature(canonical_manifest: bytes, signature_b64: str, public_key_pem: bytes) -> bool:
    """Verify a base64-encoded RSASSA-PSS-SHA-256 signature against the
    canonical manifest bytes using a PEM-encoded public key.

    Returns True on valid, False on invalid (never raises for cryptographic
    failure — only for malformed inputs)."""
    try:
        signature = base64.b64decode(signature_b64)
    except Exception:
        return False

    try:
        public_key = serialization.load_pem_public_key(public_key_pem)
    except Exception:
        return False

    try:
        public_key.verify(
            signature,
            canonical_manifest,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256(),
        )
        return True
    except InvalidSignature:
        return False


def walk_chain(manifests: list[dict]) -> dict:
    """Validate a sequence of manifests forms a continuous hash chain.

    Returns a dict with: valid, broken_links, gaps, genesis_present.

    Chain semantics:
      - First manifest must have prior_manifest_sha256 == None (genesis)
      - Each subsequent manifest's prior_manifest_sha256 must equal the
        sha256 of the prior manifest's canonical bytes
      - gap_days > 0 is permitted (cron outage etc.) but recorded as audit
        signal — it does not invalidate the chain
    """
    if not manifests:
        return {
            "valid": False,
            "broken_links": [],
            "gaps": [],
            "genesis_present": False,
        }

    broken_links: list[dict] = []
    gaps: list[dict] = []

    genesis_present = manifests[0].get("prior_manifest_sha256") is None

    for i in range(1, len(manifests)):
        prior = manifests[i - 1]
        current = manifests[i]
        prior_self = hashlib.sha256(_canonical_bytes(prior)).hexdigest()
        if current.get("prior_manifest_sha256") != prior_self:
            broken_links.append({
                "index": i,
                "expected_prior_sha256": prior_self,
                "claimed_prior_sha256": current.get("prior_manifest_sha256"),
            })
        gap_days = current.get("gap_days", 0)
        if gap_days:
            gaps.append({
                "date": current.get("generated_at"),
                "gap_days": gap_days,
            })

    return {
        "valid": not broken_links and genesis_present,
        "broken_links": broken_links,
        "gaps": gaps,
        "genesis_present": genesis_present,
    }


def _list_s3_keys(s3_client, bucket: str, prefixes: list[str]) -> list[str]:
    keys: list[str] = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for prefix in prefixes:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents") or []:
                keys.append(obj["Key"])
    return keys


def verify_manifest_hashes(s3_client, bucket: str, prefixes: list[str], manifest: dict) -> VerificationResult:
    """Compare each record's sha256 against the actual S3 object body.
    Requires read access to the bucket — auditor-only.

    The bucket interface is duck-typed against boto3's S3 client: needs
    `get_paginator("list_objects_v2")` and `get_object(Bucket, Key)`.
    Non-AWS adapters can implement the same interface.
    """
    manifest_records = manifest.get("records") or []
    manifest_by_key = {r["key"]: r for r in manifest_records}

    s3_keys = set(_list_s3_keys(s3_client, bucket, prefixes))
    manifest_keys = set(manifest_by_key.keys())

    verified: list[str] = []
    mismatches: list[dict] = []
    for key, record in manifest_by_key.items():
        if key not in s3_keys:
            continue
        body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
        actual = compute_sha256(body)
        if actual != record["sha256"]:
            mismatches.append({
                "key": key, "expected": record["sha256"], "actual": actual,
            })
        else:
            verified.append(key)

    missing = sorted(manifest_keys - s3_keys)
    orphans = sorted(s3_keys - manifest_keys)
    return VerificationResult(
        verified=verified,
        hash_mismatches=mismatches,
        missing_in_s3=missing,
        orphans_in_s3=orphans,
    )
