"""Sabotage the layer and require the suite to notice.

Green tests prove the tests ran. They do not prove the tests would catch a
regression, and a consent layer whose tests pass under sabotage is a consent
layer with no tests at all.

So each case here breaks the enforcement in a way a tired engineer plausibly
would — ignore the expiry, honour the grant but not the revocation, filter
after ranking instead of before — and asserts that something goes red. A
sabotage that survives is reported as a hole in the suite, not as a pass.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from consent_gate.adapters.sqlite_index import SqliteIndex
from consent_gate.cache import CachedGate
from consent_gate.consent import Consent, Purpose, allow
from consent_gate.gate import QueryGate
from consent_gate.store import ConsentStore

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)
DAY = timedelta(days=1)


def _world():
    store = ConsentStore()
    index = SqliteIndex()
    store.record(Consent("PA", frozenset({Purpose.DIRECT_CARE}),
                         NOW - 100 * DAY))
    store.record(Consent("PB", frozenset({Purpose.DIRECT_CARE}),
                         NOW - 100 * DAY, revoked_at=NOW - DAY))
    store.record(Consent("PC", frozenset({Purpose.DIRECT_CARE}),
                         NOW - 100 * DAY, valid_until=NOW - DAY))
    store.record(Consent("PD", frozenset({Purpose.RESEARCH}),
                         NOW - 100 * DAY))
    index.add("D1", "PA", "ZZ-PA routine review", condition="H", region="R")
    index.add("D2", "PB", "ZZ-PB rare finding", condition="K", region="R")
    index.add("D3", "PC", "ZZ-PC rare finding", condition="K", region="R")
    index.add("D4", "PD", "ZZ-PD rare finding", condition="K", region="R")
    return store, index, QueryGate(store, index)


def _hidden_is_reachable(gate, token: str) -> bool:
    """Would the oracle probe fire? The property the exhibit sells."""
    hit = gate.search(token, purpose="direct_care", at=NOW)
    miss = gate.search("ZZ-NOTHING-AT-ALL", purpose="direct_care", at=NOW)
    return hit.fingerprint() != miss.fingerprint()


def test_the_suite_is_green_before_any_sabotage():
    _, _, gate = _world()
    for token in ("ZZ-PB", "ZZ-PC", "ZZ-PD"):
        assert not _hidden_is_reachable(gate, token), token


# ── sabotage 1: honour the grant, ignore the revocation ─────────────────
def test_ignoring_revocation_is_caught(monkeypatch):
    monkeypatch.setattr(Consent, "active_at",
                        lambda self, when: when >= self.valid_from
                        and (self.valid_until is None
                             or when < self.valid_until))
    _, _, gate = _world()
    assert _hidden_is_reachable(gate, "ZZ-PB"), \
        "a revoked record became reachable and nothing noticed"


# ── sabotage 2: widen the validity window ───────────────────────────────
def test_ignoring_expiry_is_caught(monkeypatch):
    monkeypatch.setattr(Consent, "active_at",
                        lambda self, when: self.revoked_at is None
                        or when < self.revoked_at)
    _, _, gate = _world()
    assert _hidden_is_reachable(gate, "ZZ-PC"), \
        "an expired consent still granted access and nothing noticed"


# ── sabotage 3: drop the purpose check ──────────────────────────────────
def test_ignoring_purpose_is_caught(monkeypatch):
    monkeypatch.setattr(Consent, "covers",
                        lambda self, purpose, scope=None: True)
    _, _, gate = _world()
    assert _hidden_is_reachable(gate, "ZZ-PD"), \
        "a research-only consent unlocked direct care and nothing noticed"


# ── sabotage 4: post-filter instead of restricting the candidate set ────
def test_post_filtering_instead_of_exclusion_is_caught():
    """The central design claim. Filtering after ranking leaks the count and
    the pagination even when no forbidden document is returned."""
    store, index, _ = _world()

    class PostFilteringGate(QueryGate):
        def search(self, query, *, purpose=None, at=None, page=0, scope=None):
            when = at or NOW
            allowed = self.store.consented_subjects(
                Purpose.parse(purpose), when, scope)
            # retrieve over everyone, then drop — the tempting implementation
            hits, total = self.index.search(query, None,
                                            limit=self.page_size,
                                            offset=page * self.page_size)
            kept = tuple(h for h in hits if h.subject in allowed)
            from consent_gate.gate import Response
            return Response(hits=kept, count=total, page=page,
                            page_size=self.page_size,
                            has_more=(page + 1) * self.page_size < total)

    gate = PostFilteringGate(store, index)
    assert _hidden_is_reachable(gate, "ZZ-PB"), \
        "post-filtering returned no forbidden document but still leaked; if " \
        "this assertion fails the oracle probe is not actually testing the " \
        "count and pagination channels"


# ── sabotage 5: forget to stamp the cache ───────────────────────────────
def test_an_unstamped_cache_keeps_serving_a_revoked_record(monkeypatch):
    store, index, gate = _world()
    cached = CachedGate(gate)
    monkeypatch.setattr(cached, "_versions", lambda: {})   # no stamp at all

    before = cached.search("routine", purpose="direct_care", at=NOW)
    assert before.count == 1
    store.revoke("PA", at=NOW)
    after = cached.search("routine", purpose="direct_care", at=NOW)
    assert after.count == 1, \
        "the cache correctly went cold without a stamp, which means the " \
        "stamping test is not proving what it claims"


def test_the_stamped_cache_does_not_serve_a_revoked_record():
    """The same case with stamping on. This is the pair that makes the
    previous test meaningful rather than alarming."""
    store, index, gate = _world()
    cached = CachedGate(gate)
    assert cached.search("routine", purpose="direct_care", at=NOW).count == 1
    store.revoke("PA", at=NOW)
    assert cached.search("routine", purpose="direct_care", at=NOW).count == 0


# ── sabotage 6: fail open instead of closed ─────────────────────────────
def test_failing_open_is_caught(monkeypatch):
    store, index, gate = _world()

    def fail_open(self, purpose, when, scope):
        return None                    # None means "no restriction" downstream

    monkeypatch.setattr(QueryGate, "_candidates", fail_open)
    assert _hidden_is_reachable(gate, "ZZ-PB"), \
        "the gate degraded to unfiltered retrieval and nothing noticed"


def test_the_decision_function_denies_by_default():
    """The one-line property the other five sabotages all attack."""
    assert allow([], Purpose.DIRECT_CARE, NOW) is False
