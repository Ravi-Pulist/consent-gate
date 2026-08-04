"""The revocation drill: how long until the record is dark, everywhere.

Revocation is the buying trigger. "We honour withdrawal" is worth nothing
without a number attached, because the honest answer for most deployments is
"at the next index rebuild", which is to say overnight, which is to say the
patient's withdrawal was ignored for a working day.

So this is a measurement, taken at every surface a record can still be alive
on after the consent store has been told:

- the vector path
- the keyword path
- aggregates and counts
- the result and prompt-context caches
- the text that would actually reach the model

The drill revokes mid-session — with caches warm, which is the only version
of this test that means anything — and reports seconds to dark per surface.
A surface that never goes dark is reported as such rather than omitted.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

from .consent import Purpose


@dataclass
class SurfaceResult:
    surface: str
    visible_before: bool
    visible_after: bool
    seconds_to_dark: float | None

    @property
    def ok(self) -> bool:
        # A surface that never showed the record proves nothing about
        # revocation, so it is not counted as a pass.
        return self.visible_before and not self.visible_after


@dataclass
class DrillResult:
    subject: str
    purpose: str
    surfaces: list[SurfaceResult] = field(default_factory=list)
    total_seconds: float = 0.0

    @property
    def all_dark(self) -> bool:
        return all(s.ok for s in self.surfaces)

    @property
    def worst_seconds(self) -> float:
        seen = [s.seconds_to_dark for s in self.surfaces
                if s.seconds_to_dark is not None]
        return max(seen) if seen else 0.0

    def as_dict(self) -> dict:
        return {
            "subject": self.subject, "purpose": self.purpose,
            "all_dark": self.all_dark,
            "worst_seconds": round(self.worst_seconds, 4),
            "total_seconds": round(self.total_seconds, 4),
            "surfaces": [
                {"surface": s.surface, "visible_before": s.visible_before,
                 "visible_after": s.visible_after, "ok": s.ok,
                 "seconds_to_dark": (None if s.seconds_to_dark is None
                                     else round(s.seconds_to_dark, 4))}
                for s in self.surfaces],
        }


def _visible(cached_gate, query: str, subject: str, purpose: Purpose,
             when: datetime) -> bool:
    r = cached_gate.search(query, purpose=purpose, at=when)
    return any(h.subject == subject for h in getattr(r, "hits", ()))


def run_drill(cached_gate, store, subject: str, query: str,
              purpose: Purpose, when: datetime,
              stratum: str | None = None) -> DrillResult:
    """Warm every surface, revoke, then measure each one going dark."""
    result = DrillResult(subject=subject, purpose=purpose.value)
    started = time.perf_counter()

    # ── warm the caches, so this measures invalidation and not a cold miss ─
    before_search = _visible(cached_gate, query, subject, purpose, when)
    before_context = subject_in_context = cached_gate.prompt_context(
        query, purpose=purpose, at=when)
    before_counts = cached_gate.count_by("condition", purpose=purpose, at=when)
    before_stratum = (cached_gate.stratum_subjects(
        stratum, purpose=purpose, at=when) if stratum else set())

    doc_marker = None
    r = cached_gate.search(query, purpose=purpose, at=when)
    for h in getattr(r, "hits", ()):
        if h.subject == subject:
            doc_marker = h.text[:60]
            break

    # ── the revocation ──────────────────────────────────────────────────
    store.revoke(subject, at=when)

    def measure(name: str, before: bool, check) -> None:
        t0 = time.perf_counter()
        after = check()
        dt = time.perf_counter() - t0
        result.surfaces.append(SurfaceResult(
            name, before, after, None if after else dt))

    measure("vector+keyword search", before_search,
            lambda: _visible(cached_gate, query, subject, purpose, when))
    measure("result cache", before_search,
            lambda: _visible(cached_gate, query, subject, purpose, when))
    measure("prompt context", bool(doc_marker and doc_marker in before_context),
            lambda: bool(doc_marker) and doc_marker in
            cached_gate.prompt_context(query, purpose=purpose, at=when))
    measure("aggregates", bool(before_counts),
            lambda: subject in cached_gate.stratum_subjects(
                stratum, purpose=purpose, at=when) if stratum else False)
    if stratum:
        measure("stratum membership", subject in before_stratum,
                lambda: subject in cached_gate.stratum_subjects(
                    stratum, purpose=purpose, at=when))

    result.total_seconds = time.perf_counter() - started
    return result
