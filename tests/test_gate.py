"""The properties the product is sold on, each with a test that would notice
if it stopped holding."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import sqlite3

from consent_gate.adapters.sqlite_index import SqliteIndex, embed
from consent_gate.cache import CachedGate
from consent_gate.consent import (Consent, Purpose, PurposeRequired,
                                  StoreUnavailable, UnknownPurpose, allow,
                                  pseudonym)
from consent_gate.gate import MaskingGate, QueryGate, Response
from consent_gate.revocation import run_drill
from consent_gate.store import ConsentStore

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)
DAY = timedelta(days=1)


def c(subject, purposes=(Purpose.DIRECT_CARE,), *, frm=-100, until=None,
      revoked=None, scope=(), version=1):
    return Consent(subject, frozenset(purposes), NOW + frm * DAY,
                   None if until is None else NOW + until * DAY,
                   None if revoked is None else NOW + revoked * DAY,
                   frozenset(scope), version)


# ── the decision function, every branch ─────────────────────────────────
@pytest.mark.parametrize("consent,expected", [
    (c("S"), True),
    (c("S", until=-1), False),                       # expired
    (c("S", revoked=-1), False),                     # revoked
    (c("S", frm=+10), False),                        # future-dated
    (c("S", (Purpose.RESEARCH,)), False),            # wrong purpose
])
def test_decision_branches(consent, expected):
    assert allow([consent], Purpose.DIRECT_CARE, NOW) is expected


def test_no_consent_is_a_denial_not_an_absence_of_opinion():
    assert allow([], Purpose.DIRECT_CARE, NOW) is False


def test_scope_narrows_the_grant():
    """A consent for outreach does not unlock research retrieval, and a
    consent scoped to one collection does not cover another."""
    k = c("S", scope=("progress_note",))
    assert allow([k], Purpose.DIRECT_CARE, NOW, "progress_note") is True
    assert allow([k], Purpose.DIRECT_CARE, NOW, "genomics") is False


def test_revocation_takes_effect_at_its_instant_not_before():
    k = c("S", revoked=+1)
    assert allow([k], Purpose.DIRECT_CARE, NOW) is True
    assert allow([k], Purpose.DIRECT_CARE, NOW + 2 * DAY) is False


def test_a_naive_timestamp_is_refused():
    """A window compared across timezones silently grants or withholds
    permission by up to a day."""
    with pytest.raises(ValueError, match="naive"):
        Consent("S", frozenset({Purpose.DIRECT_CARE}),
                datetime(2026, 1, 1))


def test_a_consent_with_no_purpose_is_refused():
    with pytest.raises(ValueError, match="names no purpose"):
        Consent("S", frozenset(), NOW)


def test_a_window_that_ends_before_it_begins_is_refused():
    with pytest.raises(ValueError, match="expires at or before"):
        Consent("S", frozenset({Purpose.DIRECT_CARE}), NOW, NOW - DAY)


def test_purpose_vocabulary_is_closed():
    """A purpose the caller can invent is a text box that always says yes."""
    with pytest.raises(UnknownPurpose):
        Purpose.parse("whatever_we_feel_like")
    assert Purpose.parse("research") is Purpose.RESEARCH


# ── pseudonyms ──────────────────────────────────────────────────────────
def test_pseudonyms_are_keyed_and_stable():
    a = pseudonym("MRN-1", b"k1")
    assert a == pseudonym("MRN-1", b"k1")
    assert a != pseudonym("MRN-1", b"k2")
    assert a != pseudonym("MRN-2", b"k1")


def test_an_unkeyed_pseudonym_is_refused():
    """An unkeyed digest of an MRN is reversible by enumeration."""
    with pytest.raises(ValueError, match="key is required"):
        pseudonym("MRN-1", b"")


# ── the store is append-only, enforced by the database ──────────────────
def test_consent_history_cannot_be_edited():
    with ConsentStore() as store:
        store.record(c("S"))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store._db.execute("UPDATE consent SET purposes = '[]'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store._db.execute("DELETE FROM consent")


def test_the_decision_log_cannot_be_edited():
    with ConsentStore() as store:
        store.log_decision("S", Purpose.DIRECT_CARE, 1, True)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store._db.execute("DELETE FROM decision_log")


def test_revocation_is_an_insert_and_bumps_the_version():
    with ConsentStore() as store:
        store.record(c("S"))
        assert store.versions()["S"] == 1
        v = store.revoke("S", at=NOW)
        assert v == 2
        assert store.versions()["S"] == 2
        assert not allow(store.for_subject("S"), Purpose.DIRECT_CARE,
                         NOW + DAY)


def test_revoking_a_subject_who_never_consented_is_still_a_denial():
    with ConsentStore() as store:
        store.revoke("NEVER", at=NOW)
        assert not allow(store.for_subject("NEVER"), Purpose.DIRECT_CARE, NOW)


def test_a_closed_store_refuses_rather_than_returning_nothing_quietly():
    store = ConsentStore()
    store.close()
    with pytest.raises(StoreUnavailable):
        store.consented_subjects(Purpose.DIRECT_CARE, NOW)


# ── fixtures for the gate ───────────────────────────────────────────────
@pytest.fixture
def world():
    store = ConsentStore()
    index = SqliteIndex()
    store.record(c("PA"))
    store.record(c("PB", revoked=-1))          # withdrew
    # PC never consented at all
    index.add("D1", "PA", "Routine review, blood pressure stable.",
              title="note", region="Orkney", condition="Hypertension")
    index.add("D2", "PB", "ZZORACLE-01-qxth rare finding recorded here.",
              title="note", region="Orkney", condition="Kessler")
    index.add("D3", "PC", "ZZORACLE-02-mfld another rare finding.",
              title="note", region="Orkney", condition="Kessler")
    for i in range(30):
        index.add(f"F{i}", "PA", f"Follow up visit number {i} routine.",
                  title="note", region="Orkney", condition="Hypertension")
    yield store, index, QueryGate(store, index), MaskingGate(store, index)
    store.close()
    index.close()


# ── purpose is mandatory, and failure is closed ─────────────────────────
def test_a_query_without_a_purpose_returns_nothing_and_says_why(world):
    _, _, gate, _ = world
    r = gate.search("review")
    assert r.hits == () and r.count == 0
    assert "purpose" in r.error


def test_an_unknown_purpose_returns_nothing(world):
    _, _, gate, _ = world
    r = gate.search("review", purpose="marketing", at=NOW)
    assert r.hits == () and "not a declared purpose" in r.error


def test_an_unreachable_store_returns_empty_and_an_error_never_everything(world):
    store, index, gate, _ = world
    store.close()
    r = gate.search("review", purpose="direct_care", at=NOW)
    assert r.hits == () and r.count == 0
    assert r.error is not None
    assert "not everything" in r.error, r.error


# ── the oracle property ─────────────────────────────────────────────────
def test_a_withdrawn_record_is_byte_identical_to_one_that_does_not_exist(world):
    """The property the whole library exists for."""
    _, _, gate, _ = world
    hidden = gate.search("ZZORACLE-01-qxth", purpose="direct_care", at=NOW)
    nowhere = gate.search("ZZORACLE-99-none", purpose="direct_care", at=NOW)
    assert hidden.fingerprint() == nowhere.fingerprint()
    assert (hidden.count, hidden.hits, hidden.has_more) == (0, (), False)


def test_a_never_consented_record_is_also_indistinguishable(world):
    _, _, gate, _ = world
    a = gate.search("ZZORACLE-02-mfld", purpose="direct_care", at=NOW)
    b = gate.search("ZZORACLE-98-none", purpose="direct_care", at=NOW)
    assert a.fingerprint() == b.fingerprint()


def test_masking_leaks_the_same_oracle(world):
    """The baseline, at 100% masking recall, still answers the question."""
    _, _, _, masking = world
    hidden = masking.search("ZZORACLE-01-qxth", at=NOW)
    nowhere = masking.search("ZZORACLE-99-none", at=NOW)
    assert hidden.fingerprint() != nowhere.fingerprint()
    assert hidden.count == 1 and nowhere.count == 0


def test_masking_returns_the_withdrawn_narrative_with_identifiers_gone(world):
    _, index, _, masking = world
    index.add("D9", "PB", "Sparrow the lighthouse keeper from Kirkwall.",
              title="note", identifiers=[{"kind": "name", "start": 0,
                                          "end": 7}])
    r = masking.search("lighthouse keeper Kirkwall", at=NOW)
    leaked = [h for h in r.hits if h.subject == "PB"]
    assert leaked, "the baseline must leak, or the exhibit proves nothing"
    assert "[NAME]" in leaked[0].text          # perfectly masked
    assert "lighthouse keeper" in leaked[0].text   # and still identifying


# ── counts and aggregates ───────────────────────────────────────────────
def test_counts_are_computed_over_the_consented_set_only(world):
    _, _, gate, masking = world
    consented = gate.count_by("condition", purpose="direct_care", at=NOW)
    everyone = masking.count_by("condition")
    assert "Kessler" not in consented
    assert everyone["Kessler"] == 2            # both withdrawn records counted


def test_a_small_cell_does_not_count_a_withdrawn_member(world):
    _, _, gate, masking = world
    stratum = "cond=Kessler|region=Orkney"
    assert gate.stratum_subjects(stratum, purpose="direct_care",
                                 at=NOW) == set()
    assert masking.stratum_subjects(stratum) == {"PB", "PC"}


# ── pagination ──────────────────────────────────────────────────────────
def test_pages_are_full_and_the_count_matches_what_can_be_paged(world):
    _, _, gate, _ = world
    first = gate.search("routine", purpose="direct_care", at=NOW, page=0)
    seen, page = 0, 0
    while True:
        r = gate.search("routine", purpose="direct_care", at=NOW, page=page)
        seen += len(r.hits)
        if not r.has_more or page > 20:
            break
        assert len(r.hits) == r.page_size, "a short page marks a removed record"
        page += 1
    assert seen == first.count


# ── caches ──────────────────────────────────────────────────────────────
def test_a_revocation_makes_cached_results_unreadable(world):
    store, _, gate, _ = world
    cached = CachedGate(gate)
    warm = cached.search("routine", purpose="direct_care", at=NOW)
    assert warm.count > 0
    assert cached.results.hits == 0
    cached.search("routine", purpose="direct_care", at=NOW)
    assert cached.results.hits == 1            # served from cache

    store.revoke("PA", at=NOW)
    after = cached.search("routine", purpose="direct_care", at=NOW)
    assert cached.results.stale_rejects >= 1
    assert after.count == 0


def test_the_prompt_context_goes_dark_too(world):
    store, _, gate, _ = world
    cached = CachedGate(gate)
    before = cached.prompt_context("routine", purpose="direct_care", at=NOW)
    assert "Follow up visit" in before
    store.revoke("PA", at=NOW)
    assert cached.prompt_context("routine", purpose="direct_care",
                                 at=NOW) == ""


def test_the_revocation_drill_darkens_every_surface(world):
    store, _, gate, _ = world
    cached = CachedGate(gate)
    result = run_drill(cached, store, "PA", "routine", Purpose.DIRECT_CARE,
                       NOW, stratum="cond=Hypertension|region=Orkney")
    assert result.all_dark, result.as_dict()
    assert result.worst_seconds < 5.0


# ── the predicate is inside the plan, not applied to results ────────────
def test_the_consent_predicate_is_visible_in_the_query_plan(world):
    """"Part of the query, not applied to its results" is a claim a reader
    should be able to check rather than take on trust."""
    _, index, gate, _ = world
    plan = index.explain("routine", {"PA"})
    assert "consented" in plan.lower()
    assert "SEARCH c" in plan or "SCAN c" in plan


def test_the_masking_baseline_has_no_such_predicate(world):
    _, index, _, _ = world
    assert "consented" not in index.explain("routine", None).lower()


# ── response fingerprints ───────────────────────────────────────────────
def test_the_fingerprint_notices_any_observable_difference():
    a = Response(count=0)
    assert a.fingerprint() == Response(count=0).fingerprint()
    for other in (Response(count=1), Response(has_more=True),
                  Response(page=1), Response(error="x")):
        assert a.fingerprint() != other.fingerprint()


def test_embeddings_are_deterministic():
    assert embed("blood pressure stable") == embed("blood pressure stable")
    assert embed("a") != embed("b")
