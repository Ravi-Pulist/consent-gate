"""The Postgres + pgvector reference path.

Skipped loudly when no Postgres is reachable, rather than passing silently:
a green suite that never exercised the reference adapter is a false report,
and this adapter exists precisely so the pushdown property can be checked on
the engine the design nominates.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from consent_gate.consent import Consent, Purpose
from consent_gate.gate import MaskingGate, QueryGate
from consent_gate.store import ConsentStore

pgv = pytest.importorskip("consent_gate.adapters.pgvector_index")

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def index():
    if not pgv.PgVectorIndex.available():
        pytest.skip("no Postgres+pgvector reachable at "
                    f"{pgv.DEFAULT_DSN}; start one with "
                    "`docker run -p 55432:5432 pgvector/pgvector:pg16`")
    ix = pgv.PgVectorIndex()
    ix.add("D1", "PA", "Routine review, blood pressure stable.",
           title="note", region="Orkney", condition="Hypertension")
    ix.add("D2", "PB", "ZZORACLE-01-qxth rare finding recorded here.",
           title="note", region="Orkney", condition="Kessler")
    ix.add("D3", "PC", "ZZORACLE-02-mfld another rare finding.",
           title="note", region="Orkney", condition="Kessler")
    for i in range(30):
        ix.add(f"F{i}", "PA", f"Follow up visit number {i} routine.",
               title="note", region="Orkney", condition="Hypertension")
    yield ix
    ix.close()


@pytest.fixture
def store():
    s = ConsentStore()
    s.record(Consent("PA", frozenset({Purpose.DIRECT_CARE}),
                     NOW - timedelta(days=100)))
    s.record(Consent("PB", frozenset({Purpose.DIRECT_CARE}),
                     NOW - timedelta(days=100), revoked_at=NOW - timedelta(1)))
    yield s
    s.close()


def test_a_withdrawn_record_is_indistinguishable_on_pgvector(store, index):
    """The property the library sells, on the reference engine."""
    gate = QueryGate(store, index)
    hidden = gate.search("ZZORACLE-01-qxth", purpose="direct_care", at=NOW)
    nowhere = gate.search("ZZORACLE-99-none", purpose="direct_care", at=NOW)
    assert hidden.fingerprint() == nowhere.fingerprint()
    assert (hidden.count, hidden.hits) == (0, ())


def test_a_never_consented_record_is_also_indistinguishable(store, index):
    gate = QueryGate(store, index)
    a = gate.search("ZZORACLE-02-mfld", purpose="direct_care", at=NOW)
    b = gate.search("ZZORACLE-98-none", purpose="direct_care", at=NOW)
    assert a.fingerprint() == b.fingerprint()


def test_masking_leaks_the_same_oracle_on_pgvector(store, index):
    masking = MaskingGate(store, index)
    hidden = masking.search("ZZORACLE-01-qxth", at=NOW)
    nowhere = masking.search("ZZORACLE-99-none", at=NOW)
    assert hidden.fingerprint() != nowhere.fingerprint()
    assert hidden.count == 1


def test_counts_are_over_the_consented_set_only(store, index):
    gate, masking = QueryGate(store, index), MaskingGate(store, index)
    assert "Kessler" not in gate.count_by("condition", purpose="direct_care",
                                          at=NOW)
    assert masking.count_by("condition")["Kessler"] == 2


def test_the_consent_predicate_is_inside_the_postgres_plan(store, index):
    """"Part of the query, not applied to its results", checked on the
    engine the design nominates as reference rather than asserted."""
    plan = index.explain("routine", {"PA"})
    assert "consented" in plan.lower()
    # The join must be resolved by the planner as part of the scan, not
    # applied to its output. Any of these is a genuine join strategy; what
    # would fail is the consent test appearing only in a Filter above the
    # documents scan.
    assert any(m in plan for m in ("Index Cond: (subject = c.subject)",
                                   "Hash Cond", "Nested Loop")), plan
    assert "'[...1024 dims...]'::vector" in plan, "vector literal abbreviated"


def test_the_masking_plan_carries_no_consent_predicate(store, index):
    assert "consented" not in index.explain("routine", None).lower()


def test_pagination_is_stable_on_pgvector(store, index):
    gate = QueryGate(store, index)
    first = gate.search("routine", purpose="direct_care", at=NOW, page=0)
    seen, page = 0, 0
    while True:
        r = gate.search("routine", purpose="direct_care", at=NOW, page=page)
        seen += len(r.hits)
        if not r.has_more or page > 20:
            break
        assert len(r.hits) == r.page_size, "a short page marks a removal"
        page += 1
    assert seen == first.count


def test_both_adapters_agree_on_the_oracle_property(store, index):
    """The SQLite and Postgres paths must reach the same verdict, or one of
    them is not implementing the thing being sold."""
    from consent_gate.adapters.sqlite_index import SqliteIndex

    lite = SqliteIndex()
    lite.add("D1", "PA", "Routine review, blood pressure stable.",
             title="note", region="Orkney", condition="Hypertension")
    lite.add("D2", "PB", "ZZORACLE-01-qxth rare finding recorded here.",
             title="note", region="Orkney", condition="Kessler")

    a = QueryGate(store, lite).search("ZZORACLE-01-qxth",
                                      purpose="direct_care", at=NOW)
    b = QueryGate(store, index).search("ZZORACLE-01-qxth",
                                       purpose="direct_care", at=NOW)
    assert a.fingerprint() == b.fingerprint()
    lite.close()


def test_the_observable_property_holds_regardless_of_join_strategy(store,
                                                                   index):
    """The guarantee is about the candidate set, not about the CPU.

    Postgres picks a nested loop on a tiny table and a hash join on a large
    one. Under the hash join it evaluates the similarity filter on rows it is
    about to discard, so "never scored" is a claim about the plan rather than
    about the library. What must hold either way is that nothing
    non-consented is returned, counted, ranked or paged — and that is what
    this asserts.
    """
    gate = QueryGate(store, index)
    r = gate.search("routine", purpose="direct_care", at=NOW)

    # nothing from a subject without consent, at any position
    assert all(h.subject == "PA" for h in r.hits), [h.subject for h in r.hits]
    # the count is over the joined set, so it cannot be differenced
    reachable, page = 0, 0
    while True:
        pg = gate.search("routine", purpose="direct_care", at=NOW, page=page)
        reachable += len(pg.hits)
        if not pg.has_more or page > 20:
            break
        page += 1
    assert r.count == reachable


def test_the_plan_is_recorded_so_a_deployment_can_check_its_own(index):
    """Physical non-access is plan-dependent, so the plan is published rather
    than a claim about it."""
    plan = index.explain("routine", {"PA"})
    assert "Seq Scan on consented" in plan or "consented" in plan
    assert "cost=" in plan, "the real planner output, not a paraphrase"
