"""Supabase persistence for the evidence log, case reviews and uploaded evidence.

WHY THIS REPLACED FIRESTORE.

The Section 63 BSA certificate rests on a hash-chained log. A log that lives in the
container's memory and resets whenever Cloud Run recycles the instance cannot support that
claim -- the chain would restart at genesis with no record that anything preceded it, which
is precisely the property the certificate is supposed to rule out. Firestore fixed the
persistence but is awkward for the rest: analyst review decisions are relational, uploaded
evidence needs object storage, and querying a chain by sequence in a document store is
clumsy.

Supabase gives Postgres, object storage and row-level security in one place, so the log, the
reviews and the files that the verdicts refer to all live together and can be joined.

WHAT IS STORED, AND WHAT IS NOT.

Stored: hash-chained log entries, analyst decisions, and evidence files a user uploads for
analysis. NOT stored: card numbers, cardholder names, or any raw PII. The ledger AEGIS reads
is already tokenised, and the evidence log records SHA-256 digests and verdicts rather than
document contents. If this ever ran on a real merchant's data, the log would still contain
nothing that would hurt anyone if it leaked.

CREDENTIALS. `SUPABASE_URL` plus `SUPABASE_KEY`. Use the anon key with row-level security
enabled, not the service-role key -- service-role bypasses RLS entirely, and a read-mostly
audit surface has no reason to hold that. Without credentials this module reports
`configured: False` and the evidence log falls back to in-memory, which `/api/health`
reports honestly rather than silently pretending to persist.
"""

from __future__ import annotations

import os
from typing import Any

# Tables the integration expects. Created by the SQL in `schema_sql()` below.
T_LOG = "aegis_evidence_log"
T_REVIEW = "aegis_case_reviews"
BUCKET = "aegis-evidence"


def is_configured() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"))


def status() -> dict[str, Any]:
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    return {
        "configured": is_configured(),
        # Show the project ref only -- never the key, and never the full URL with any token.
        "project": url.split("//")[-1].split(".")[0] if url else None,
        "uses_service_role": key.startswith("eyJ") and "service_role" in key,
        "warning": (
            "A service-role key appears to be configured. It bypasses row-level security; "
            "prefer the anon key with RLS enabled."
            if key and "service_role" in key else None
        ),
        "tables": [T_LOG, T_REVIEW],
        "bucket": BUCKET,
    }


class SupabaseStore:
    def __init__(self) -> None:
        self._client = None
        if is_configured():
            try:
                from supabase import create_client

                self._client = create_client(
                    os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"]
                )
            except Exception:
                self._client = None

    @property
    def live(self) -> bool:
        return self._client is not None

    # --- evidence log ---------------------------------------------------------------

    def append_entry(self, entry: dict[str, Any]) -> bool:
        """Persist one hash-chain entry. Returns whether it actually landed.

        Failure is returned rather than raised: losing the remote write must not fail the
        request or destroy the in-memory chain, but it must also not be reported as success,
        because the certificate's integrity claim depends on knowing.
        """
        if not self._client:
            return False
        try:
            self._client.table(T_LOG).insert({
                "case_id": entry["case_id"],
                "seq": entry["seq"],
                "ts": entry["ts"],
                "kind": entry["kind"],
                "payload": entry["payload"],
                "prev_hash": entry["prev_hash"],
                "content_hash": entry["content_hash"],
                "entry_hash": entry["entry_hash"],
            }).execute()
            return True
        except Exception:
            return False

    def entries(self, case_id: str) -> list[dict[str, Any]]:
        if not self._client:
            return []
        try:
            r = (self._client.table(T_LOG).select("*")
                 .eq("case_id", case_id).order("seq").execute())
            return list(r.data or [])
        except Exception:
            return []

    def head(self) -> str | None:
        """The most recent entry hash, so a restarted process can continue the chain
        instead of starting a fresh one and orphaning everything written before."""
        if not self._client:
            return None
        try:
            r = (self._client.table(T_LOG).select("entry_hash,seq")
                 .order("seq", desc=True).limit(1).execute())
            return (r.data or [{}])[0].get("entry_hash")
        except Exception:
            return None

    # --- analyst decisions -----------------------------------------------------------

    def record_review(self, case_id: str, decision: str, analyst: str,
                      note: str = "") -> bool:
        """Record what a human actually decided.

        This is the feedback loop the model card lists as future work: recommendations paired
        with the decision an analyst made and, eventually, the outcome. Without it the system
        can never learn whether its advice was any good.
        """
        if not self._client:
            return False
        try:
            self._client.table(T_REVIEW).insert({
                "case_id": case_id, "decision": decision,
                "analyst": analyst, "note": note[:2000],
            }).execute()
            return True
        except Exception:
            return False

    def reviews(self, case_id: str | None = None) -> list[dict[str, Any]]:
        if not self._client:
            return []
        try:
            q = self._client.table(T_REVIEW).select("*")
            if case_id:
                q = q.eq("case_id", case_id)
            return list((q.order("created_at", desc=True).limit(200).execute()).data or [])
        except Exception:
            return []

    # --- uploaded evidence -----------------------------------------------------------

    def upload_evidence(self, name: str, data: bytes, content_type: str) -> str | None:
        """Store an uploaded file and return its path. Returns None if storage is unavailable."""
        if not self._client:
            return None
        try:
            path = f"uploads/{name}"
            self._client.storage.from_(BUCKET).upload(
                path, data, {"content-type": content_type, "upsert": "true"}
            )
            return path
        except Exception:
            return None


def schema_sql() -> str:
    """The SQL to run once in the Supabase SQL editor.

    Returned from code rather than kept in a loose .sql file so the schema the application
    expects and the schema documented for the operator cannot drift apart.
    """
    return f"""
-- AEGIS schema. Run once in the Supabase SQL editor.

create table if not exists {T_LOG} (
  id          bigserial primary key,
  case_id     text        not null,
  seq         integer     not null,
  ts          timestamptz not null,
  kind        text        not null,
  payload     jsonb       not null,
  prev_hash   text        not null,
  content_hash text       not null,
  entry_hash  text        not null,
  created_at  timestamptz not null default now()
);
create index if not exists {T_LOG}_case_seq on {T_LOG} (case_id, seq);

-- The log is append-only by construction. Without this, an UPDATE could rewrite history
-- while leaving the hash chain internally consistent, which defeats the entire point.
create policy "{T_LOG}_no_update" on {T_LOG} for update using (false);
create policy "{T_LOG}_no_delete" on {T_LOG} for delete using (false);

create table if not exists {T_REVIEW} (
  id         bigserial primary key,
  case_id    text not null,
  decision   text not null,
  analyst    text not null,
  note       text,
  created_at timestamptz not null default now()
);
create index if not exists {T_REVIEW}_case on {T_REVIEW} (case_id);

alter table {T_LOG}    enable row level security;
alter table {T_REVIEW} enable row level security;

-- Storage bucket for uploaded evidence. Keep it PRIVATE: these are customer documents
-- submitted in support of a dispute, and a public bucket would publish them.
insert into storage.buckets (id, name, public)
values ('{BUCKET}', '{BUCKET}', false)
on conflict (id) do nothing;
""".strip()
