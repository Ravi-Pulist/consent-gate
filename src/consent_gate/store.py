"""The consent store: append-only, enforced by the database, not by manners.

An application-level rule that says "we never update consent rows" is a
comment. A trigger that raises on UPDATE and DELETE is a control. The
difference matters here more than in most audit stores, because the question
this table has to answer is not "what is permitted now" but "what was
permitted at the moment we used the record" — and an overwritten row cannot
answer it at all.

So revocation is an *insert*. The subject's history is the full sequence of
records, and the state at any past instant is reconstructable by replaying it.

**The store is also the most dangerous table in the deployment.** It is, by
construction, a register of who has and has not permitted use of their data —
a membership oracle with a schema. It therefore holds keyed pseudonyms and
there is no linkage table here, in this package, at all.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .consent import (Consent, Purpose, StoreUnavailable, consent_version,
                      utcnow)

SCHEMA = """
CREATE TABLE IF NOT EXISTS consent (
    rowid_    INTEGER PRIMARY KEY AUTOINCREMENT,
    subject   TEXT    NOT NULL,
    purposes  TEXT    NOT NULL,
    scope     TEXT    NOT NULL DEFAULT '[]',
    valid_from  TEXT  NOT NULL,
    valid_until TEXT,
    revoked_at  TEXT,
    version   INTEGER NOT NULL DEFAULT 1,
    recorded_at TEXT  NOT NULL
);
CREATE INDEX IF NOT EXISTS consent_subject ON consent(subject);

-- Append-only, enforced here rather than promised in a code review.
CREATE TRIGGER IF NOT EXISTS consent_no_update
BEFORE UPDATE ON consent BEGIN
    SELECT RAISE(ABORT,
      'consent is append-only: record a revocation, do not edit history');
END;
CREATE TRIGGER IF NOT EXISTS consent_no_delete
BEFORE DELETE ON consent BEGIN
    SELECT RAISE(ABORT,
      'consent is append-only: a deleted consent cannot be audited');
END;

CREATE TABLE IF NOT EXISTS decision_log (
    rowid_     INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    subject    TEXT NOT NULL,
    purpose    TEXT NOT NULL,
    version    INTEGER NOT NULL,
    allowed    INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS decision_no_update
BEFORE UPDATE ON decision_log BEGIN
    SELECT RAISE(ABORT, 'the decision log is append-only');
END;
CREATE TRIGGER IF NOT EXISTS decision_no_delete
BEFORE DELETE ON decision_log BEGIN
    SELECT RAISE(ABORT, 'the decision log is append-only');
END;
"""


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _dt(raw: str | None) -> datetime | None:
    return datetime.fromisoformat(raw) if raw else None


class ConsentStore:
    """SQLite-backed consent register.

    SQLite rather than Postgres for the reference implementation because it
    runs everywhere with no daemon, and the two properties that matter —
    trigger-enforced append-only, and a predicate the query planner can be
    shown to have pushed into the scan — are both demonstrable here. The
    Postgres adapter implements the same interface for deployments that want
    pgvector; see :mod:`consent_gate.adapters`.
    """

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        try:
            self._db = sqlite3.connect(self.path)
        except sqlite3.Error as exc:
            raise StoreUnavailable(f"cannot open consent store: {exc}") from exc
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()
        self._closed = False

    # ── writing ─────────────────────────────────────────────────────────
    def record(self, consent: Consent) -> None:
        self._guard()
        self._db.execute(
            "INSERT INTO consent (subject, purposes, scope, valid_from, "
            "valid_until, revoked_at, version, recorded_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (consent.subject,
             json.dumps(sorted(p.value for p in consent.purposes)),
             json.dumps(sorted(consent.scope)),
             _iso(consent.valid_from), _iso(consent.valid_until),
             _iso(consent.revoked_at), consent.version, _iso(utcnow())))
        self._db.commit()

    def revoke(self, subject: str, at: datetime | None = None) -> int:
        """Record a revocation and return the subject's new consent version.

        An insert, never an update. The returned version is what invalidates
        every cache entry stamped with the old one — which is the mechanism
        that makes revocation take effect in seconds rather than at the next
        index rebuild.
        """
        self._guard()
        when = at or utcnow()
        current = self.for_subject(subject)
        if not current:
            # Revoking a consent that never existed is not an error — it is
            # the correct end state — but it must still bump the version so
            # anything cached against "no opinion" is invalidated.
            version = 1
        else:
            version = consent_version(current) + 1
        for c in current or []:
            self._db.execute(
                "INSERT INTO consent (subject, purposes, scope, valid_from, "
                "valid_until, revoked_at, version, recorded_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (subject, json.dumps(sorted(p.value for p in c.purposes)),
                 json.dumps(sorted(c.scope)), _iso(c.valid_from),
                 _iso(c.valid_until), _iso(when), version, _iso(utcnow())))
        if not current:
            self._db.execute(
                "INSERT INTO consent (subject, purposes, scope, valid_from, "
                "valid_until, revoked_at, version, recorded_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (subject, json.dumps([Purpose.DIRECT_CARE.value]), "[]",
                 _iso(when), None, _iso(when), version, _iso(utcnow())))
        self._db.commit()
        return version

    def log_decision(self, subject: str, purpose: Purpose, version: int,
                     allowed: bool) -> None:
        """Pseudonymous, append-only. The proof of non-use must not itself
        become the disclosure."""
        self._guard()
        self._db.execute(
            "INSERT INTO decision_log (at, subject, purpose, version, allowed)"
            " VALUES (?,?,?,?,?)",
            (_iso(utcnow()), subject, purpose.value, version, int(allowed)))
        self._db.commit()

    # ── reading ─────────────────────────────────────────────────────────
    def _row_to_consent(self, r: sqlite3.Row) -> Consent:
        return Consent(
            subject=r["subject"],
            purposes=frozenset(Purpose(p) for p in json.loads(r["purposes"])),
            scope=frozenset(json.loads(r["scope"])),
            valid_from=_dt(r["valid_from"]),
            valid_until=_dt(r["valid_until"]),
            revoked_at=_dt(r["revoked_at"]),
            version=r["version"])

    def for_subject(self, subject: str) -> list[Consent]:
        """Every consent record for a subject, latest version only.

        Latest-version-only is what makes a revocation take effect: the
        revoked copy carries a higher version than the grant it supersedes,
        so replaying the whole history would resurrect permission that was
        withdrawn.
        """
        self._guard()
        rows = self._db.execute(
            "SELECT * FROM consent WHERE subject = ? "
            "AND version = (SELECT MAX(version) FROM consent WHERE subject = ?)"
            " ORDER BY rowid_", (subject, subject)).fetchall()
        return [self._row_to_consent(r) for r in rows]

    def versions(self) -> dict[str, int]:
        self._guard()
        rows = self._db.execute(
            "SELECT subject, MAX(version) AS v FROM consent GROUP BY subject"
        ).fetchall()
        return {r["subject"]: r["v"] for r in rows}

    def consented_subjects(self, purpose: Purpose, when: datetime,
                           scope: str | None = None) -> set[str]:
        """Every subject permitted for this purpose at this instant.

        This set *is* the corpus the retriever is allowed to see. It is
        computed once and pushed into the query, rather than used to filter
        results afterwards — the difference between the two is the whole
        leak surface described in :mod:`consent_gate.gate`.
        """
        self._guard()
        out = set()
        for subject in {r["subject"] for r in
                        self._db.execute("SELECT DISTINCT subject FROM consent")}:
            records = self.for_subject(subject)
            if any(c.covers(purpose, scope) and c.active_at(when)
                   for c in records):
                out.add(subject)
        return out

    # ── lifecycle ───────────────────────────────────────────────────────
    def _guard(self) -> None:
        if self._closed:
            raise StoreUnavailable(
                "the consent store is closed; refusing to answer. A retrieval "
                "layer that cannot check consent must return nothing, not "
                "everything")

    def close(self) -> None:
        self._closed = True
        self._db.close()

    def __enter__(self) -> "ConsentStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
