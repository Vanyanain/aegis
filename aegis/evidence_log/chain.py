"""Append-only, hash-chained evidence log with a Section 63 BSA certificate.

WHY A HASH CHAIN. A forensic verdict is only worth as much as the record behind it. If the
log can be edited after the fact, nothing in it is evidence. Each entry therefore carries
the SHA-256 of its own canonical content plus the hash of the entry before it, so altering
any historical entry breaks every hash after it and `verify()` says so.

WHY SECTION 63 AND NOT SECTION 65B. The Indian Evidence Act, 1872 was repealed by the
Bharatiya Sakshya Adhiniyam, 2023, in force from 1 July 2024. Electronic-record
admissibility is now Section 63 BSA. Section 63(4) also moved from a single-signatory
certificate to a DUAL-signatory one -- the person responsible for the device, and an expert
where an expert examination was undertaken. A forensic verdict is exactly such an
examination, so the certificate here carries both signature blocks. Citing "Section 65B" in
2026, as the original spec did, would be citing a repealed statute.

The certificate is generated UNSIGNED. AEGIS records what it did and computes the hashes;
the human attestations are captured outside this system by the people who can actually make
them. A system that signed its own expert certificate would be worthless.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

GENESIS = "0" * 64

# Statutory reference, kept as data so a future amendment is an edit here and nowhere else.
STATUTE = {
    "act": "Bharatiya Sakshya Adhiniyam, 2023",
    "section": "63",
    "in_force_from": "2024-07-01",
    "replaces": "Section 65B, Indian Evidence Act, 1872 (repealed)",
    "certificate_subsection": "63(4)",
    "signatories_required": 2,
}


def _canonical(obj: Any) -> str:
    """Stable JSON so the same content always hashes to the same value."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha256_str(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Entry:
    seq: int
    ts: str
    kind: str
    case_id: str
    payload: dict[str, Any]
    prev_hash: str
    content_hash: str = ""
    entry_hash: str = ""

    def finalise(self) -> "Entry":
        self.content_hash = sha256_str(_canonical(
            {"seq": self.seq, "ts": self.ts, "kind": self.kind,
             "case_id": self.case_id, "payload": self.payload}
        ))
        self.entry_hash = sha256_str(self.prev_hash + self.content_hash)
        return self

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq, "ts": self.ts, "kind": self.kind, "case_id": self.case_id,
            "payload": self.payload, "prev_hash": self.prev_hash,
            "content_hash": self.content_hash, "entry_hash": self.entry_hash,
        }


class EvidenceLog:
    """In-memory chain with optional Firestore persistence.

    Firestore is used when AEGIS runs on Cloud Run, so the audit trail survives the
    container being recycled -- an evidence log that resets on cold start would undercut
    the entire admissibility claim. When Firestore is unavailable the chain still works
    in memory and `backend` reports `memory`, so the limitation is visible rather than
    silently assumed away.
    """

    def __init__(self, project: str | None = None, collection: str = "aegis_evidence_log"):
        self._entries: list[Entry] = []
        self._collection = collection
        self._db = None
        self.backend = "memory"
        project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        if project:
            try:
                from google.cloud import firestore  # type: ignore

                self._db = firestore.Client(project=project)
                self.backend = "firestore"
            except Exception:
                self._db = None
                self.backend = "memory"

    @property
    def head(self) -> str:
        return self._entries[-1].entry_hash if self._entries else GENESIS

    def append(self, kind: str, case_id: str, payload: dict[str, Any]) -> Entry:
        e = Entry(
            seq=len(self._entries),
            ts=datetime.now(timezone.utc).isoformat(),
            kind=kind,
            case_id=case_id,
            payload=payload,
            prev_hash=self.head,
        ).finalise()
        self._entries.append(e)
        if self._db is not None:
            try:
                self._db.collection(self._collection).document(
                    f"{case_id}_{e.seq:08d}"
                ).set(e.as_dict())
            except Exception:
                # Persistence failure must not destroy the in-memory chain or the request.
                self.backend = "memory (firestore write failed)"
        return e

    def entries(self, case_id: str | None = None) -> list[dict[str, Any]]:
        return [
            e.as_dict() for e in self._entries
            if case_id is None or e.case_id == case_id
        ]

    def verify(self) -> dict[str, Any]:
        """Recompute the whole chain and report the first entry that fails, if any."""
        prev = GENESIS
        for e in self._entries:
            content = sha256_str(_canonical(
                {"seq": e.seq, "ts": e.ts, "kind": e.kind,
                 "case_id": e.case_id, "payload": e.payload}
            ))
            if content != e.content_hash:
                return {"intact": False, "failed_at": e.seq, "reason": "content altered"}
            if sha256_str(prev + content) != e.entry_hash:
                return {"intact": False, "failed_at": e.seq, "reason": "chain broken"}
            prev = e.entry_hash
        return {"intact": True, "n_entries": len(self._entries), "head": self.head}

    def certificate(self, case_id: str, system_version: str = "AEGIS 1.0") -> dict[str, Any]:
        """A Section 63(4) BSA-shaped certificate for one case.

        Returned UNSIGNED by design: the two attestations required by 63(4) are human acts.
        AEGIS supplies the facts they attest to -- what was examined, by what process, and
        the hashes proving nothing changed since.
        """
        rows = self.entries(case_id)
        return {
            "statute": STATUTE,
            "case_id": case_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "system": {
                "name": system_version,
                "description": (
                    "Automated dispute-evidence analysis system. Operates on transaction "
                    "records and customer-submitted evidence files. Produces qualification "
                    "determinations against published card-network rules and forensic "
                    "authenticity assessments of submitted documents."
                ),
                "regular_use": (
                    "The computer was used regularly to store and process information of "
                    "this kind during the period in question."
                ),
                "operating_properly": (
                    "No malfunction affecting the accuracy of the record is known for the "
                    "period covered by these entries."
                ),
            },
            "entries": rows,
            "n_entries": len(rows),
            "chain": self.verify(),
            "signatures": [
                {
                    "role": "person occupying a responsible official position in relation "
                            "to the operation of the device / management of activities",
                    "basis": "Section 63(4), Bharatiya Sakshya Adhiniyam, 2023",
                    "name": None, "designation": None, "signed_at": None,
                    "status": "UNSIGNED - to be completed by the merchant's authorised officer",
                },
                {
                    "role": "expert (examination of electronic record undertaken)",
                    "basis": "Section 63(4)(c), Bharatiya Sakshya Adhiniyam, 2023",
                    "name": None, "designation": None, "signed_at": None,
                    "status": "UNSIGNED - to be completed by the examining expert",
                },
            ],
            "note": (
                "This certificate is generated unsigned. AEGIS records the facts and the "
                "hash chain; the two attestations required by Section 63(4) must be made "
                "by the responsible officer and the examining expert respectively."
            ),
        }
