"""The query gate: exclusion as a property of the candidate set.

The tempting implementation of "we respect consent" is to retrieve normally
and drop non-consented records from the results. It is wrong on three
surfaces at once, and each one is a working oracle:

===========  =====================================================
surface      how post-filtering leaks
===========  =====================================================
counts       "1,204 matches, showing 8" — the 1,204 was computed
             over everyone, including the withdrawn. Facet counts
             the same. The number *is* the disclosure.
scores       ranks and any normalisation are computed over the
             full set, so the shape of the surviving scores shifts
             with what was silently removed.
pagination   a short page, or offset arithmetic that skips, marks
             exactly where a record was removed. Position gaps are
             an oracle.
===========  =====================================================

There is a correctness cost too: top-k-then-filter returns fewer than k, and
over-fetching to compensate makes the retrieval budget itself depend on how
many hidden records exist — which is another oracle, built to fix the first
one.

The rule that resolves all of it: **exclusion is a property of the candidate
set, not of the result set.** Every query runs against a virtual corpus
containing only the records consented for the declared purpose. Nothing else
is scored, counted, ranked or paged, because as far as the engine is
concerned nothing else is there.

Two further commitments, both enforced here rather than documented:

**Purpose is mandatory.** Consent is purpose-bound in both source regimes, so
a query naming no purpose has no consented corpus to run against. There is no
default purpose, because a default is a permission the patient never gave.

**Fail closed.** If the store cannot answer, the gate returns an empty result
and an explicit error. It never degrades to unfiltered. Absence of evidence is
not permission.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Protocol

from .consent import (ConsentError, Purpose, PurposeRequired, StoreUnavailable,
                      utcnow)
from .store import ConsentStore

DEFAULT_PAGE_SIZE = 10


@dataclass(frozen=True)
class Hit:
    doc_id: str
    subject: str
    score: float
    text: str
    title: str = ""


@dataclass(frozen=True)
class Response:
    """What the caller sees. Everything in here is computed over the
    consented candidate set, so nothing in here can be differenced against a
    hidden record."""

    hits: tuple[Hit, ...] = ()
    #: matches within the consented corpus. There is no other number in the
    #: system — no "total before filtering" exists to be leaked.
    count: int = 0
    page: int = 0
    page_size: int = DEFAULT_PAGE_SIZE
    has_more: bool = False
    error: str | None = None

    def fingerprint(self) -> str:
        """Canonical bytes of the response.

        The oracle test compares these: a token unique to a non-consented
        record and a token present nowhere must produce the *same* string. If
        anything in the response varies with the hidden record's existence,
        it shows up here as a differing digest.
        """
        payload = {
            "hits": [asdict(h) for h in self.hits], "count": self.count,
            "page": self.page, "page_size": self.page_size,
            "has_more": self.has_more, "error": self.error,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class Index(Protocol):
    """A retrieval engine that can restrict its candidate set.

    The single method that matters takes `subjects` — the consented set — and
    is required to apply it *inside* the scan rather than to its output. The
    SQLite adapter proves it with EXPLAIN QUERY PLAN; a Postgres adapter
    proves the same thing with EXPLAIN over the pgvector scan.
    """

    def search(self, query: str, subjects: set[str] | None, limit: int,
               offset: int) -> tuple[list[Hit], int]:
        """Return (hits for this page, total matches in the candidate set)."""

    def count_by(self, field_name: str, subjects: set[str] | None
                 ) -> dict[str, int]:
        """Aggregate over the candidate set only."""


class QueryGate:
    """Consent enforced below the retriever."""

    mode = "exclusion"

    def __init__(self, store: ConsentStore, index: Index, *,
                 page_size: int = DEFAULT_PAGE_SIZE, log: bool = True):
        self.store = store
        self.index = index
        self.page_size = page_size
        self.log = log

    # ── the consented corpus ────────────────────────────────────────────
    def _candidates(self, purpose, when, scope) -> set[str]:
        if purpose is None:
            raise PurposeRequired(
                "a query must declare exactly one purpose; consent is "
                "purpose-bound, so a query with no purpose has no consented "
                "corpus to run against")
        p = Purpose.parse(purpose)
        try:
            subjects = self.store.consented_subjects(p, when, scope)
        except ConsentError:
            raise
        except Exception as exc:              # noqa: BLE001
            raise StoreUnavailable(
                f"the consent store could not answer ({exc}); returning "
                "nothing rather than everything") from exc
        return subjects

    # ── the public surface ──────────────────────────────────────────────
    def search(self, query: str, *, purpose=None, at: datetime | None = None,
               page: int = 0, scope: str | None = None) -> Response:
        when = at or utcnow()
        try:
            subjects = self._candidates(purpose, when, scope)
        except ConsentError as exc:
            # Empty *and* explicit. A caller that ignores the error still
            # gets nothing, which is the direction a failure must go.
            return Response(error=str(exc), page=page,
                            page_size=self.page_size)

        hits, total = self.index.search(
            query, subjects, limit=self.page_size,
            offset=page * self.page_size)
        return Response(hits=tuple(hits), count=total, page=page,
                        page_size=self.page_size,
                        has_more=(page + 1) * self.page_size < total)

    def count_by(self, field_name: str, *, purpose=None,
                 at: datetime | None = None,
                 scope: str | None = None) -> dict[str, int] | Response:
        when = at or utcnow()
        try:
            subjects = self._candidates(purpose, when, scope)
        except ConsentError as exc:
            return Response(error=str(exc))
        return self.index.count_by(field_name, subjects)

    def stratum_subjects(self, stratum: str, *, purpose=None,
                         at: datetime | None = None,
                         scope: str | None = None) -> set[str]:
        """Who contributes to this stratum's cell.

        The small-cell probe asks this rather than reading a total, because
        a cell of size two discloses both members and the interesting
        question is *which* two — a count of 2 that includes someone who
        withdrew is the leak, and a count alone cannot show it.
        """
        when = at or utcnow()
        try:
            subjects = self._candidates(purpose, when, scope)
        except ConsentError:
            return set()
        return self.index.strata(subjects).get(stratum, set())


class MaskingGate:
    """The baseline being argued against, implemented at its theoretical best.

    It retrieves the whole corpus and masks every direct identifier with
    **100% recall** — possible only because the corpus is synthetic and every
    identifier's offset is known. That perfection is deliberate: it forestalls
    "your masker was just bad". Every leak this configuration produces is one
    masking cannot fix even in principle, because masking hides *who* and
    consent governs *whether*.

    A real NER-based masker also misses identifiers outright, so this exhibit
    understates masking's real-world failure rather than exaggerating it.
    """

    mode = "masking"

    def __init__(self, store: ConsentStore, index: Index, *,
                 page_size: int = DEFAULT_PAGE_SIZE):
        self.store = store
        self.index = index
        self.page_size = page_size

    def search(self, query: str, *, purpose=None, at: datetime | None = None,
               page: int = 0, scope: str | None = None) -> Response:
        # No candidate restriction: this is the whole point of the baseline.
        hits, total = self.index.search(
            query, None, limit=self.page_size, offset=page * self.page_size)
        masked = tuple(
            Hit(h.doc_id, h.subject, h.score, self.index.mask(h.doc_id),
                h.title)
            for h in hits)
        return Response(hits=masked, count=total, page=page,
                        page_size=self.page_size,
                        has_more=(page + 1) * self.page_size < total)

    def count_by(self, field_name: str, *, purpose=None,
                 at: datetime | None = None,
                 scope: str | None = None) -> dict[str, int]:
        return self.index.count_by(field_name, None)

    def stratum_subjects(self, stratum: str, *, purpose=None,
                         at: datetime | None = None,
                         scope: str | None = None) -> set[str]:
        # Masking has no candidate set: everyone is counted, then redacted.
        # This is exactly the leak — redaction happens after the arithmetic.
        return self.index.strata(None).get(stratum, set())
