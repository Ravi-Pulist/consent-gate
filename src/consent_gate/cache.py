"""Caches stamped with the subject's consent version.

A cache keyed only on the query is a way for a withdrawn record to keep
answering questions after it went dark. The embedding cache still holds its
vector, the result cache still holds it in a hit list, and the prompt-context
cache still hands it to the model — all of them serving a record the patient
withdrew, all of them technically correct about the query they were keyed on.

So every entry carries the consent version of every subject it depends on.
Revocation is an insert that bumps the version, and every entry stamped with
the old one becomes unreadable on the next lookup. Not evicted on a timer,
not invalidated by a background job that might be behind — *unreadable*,
because the key no longer matches.

That is what makes the propagation number in the exhibit a measurement rather
than a promise: there is no window during which a stale entry is still valid,
only the time it takes the next lookup to miss.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class VersionedCache:
    """Keyed on (query key, consent versions of the subjects involved)."""

    name: str
    _data: dict[tuple, Any] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0
    stale_rejects: int = 0

    @staticmethod
    def _stamp(subjects: dict[str, int]) -> tuple:
        return tuple(sorted(subjects.items()))

    def get(self, key: str, subjects: dict[str, int]) -> Any | None:
        entry = self._data.get(key)
        if entry is None:
            self.misses += 1
            return None
        stamp, value = entry
        if stamp != self._stamp(subjects):
            # The entry exists and is about the right query. It is refused
            # anyway, because the consent state it was computed under is no
            # longer the consent state that holds.
            self.stale_rejects += 1
            self.misses += 1
            return None
        self.hits += 1
        return value

    def put(self, key: str, subjects: dict[str, int], value: Any) -> None:
        self._data[key] = (self._stamp(subjects), value)

    def stats(self) -> dict:
        return {"cache": self.name, "hits": self.hits, "misses": self.misses,
                "stale_rejects": self.stale_rejects, "entries": len(self._data)}


class CachedGate:
    """Wraps a gate with the three caches a real deployment would have.

    Included in the probe surface deliberately: a consent layer that is
    correct at the database and wrong at the cache has not excluded anything,
    it has merely delayed the disclosure by one request.
    """

    def __init__(self, gate):
        self.gate = gate
        self.mode = getattr(gate, "mode", "?")
        self.embeddings = VersionedCache("embeddings")
        self.results = VersionedCache("results")
        self.contexts = VersionedCache("prompt_contexts")

    def _versions(self) -> dict[str, int]:
        try:
            return self.gate.store.versions()
        except Exception:                     # noqa: BLE001
            return {}

    def search(self, query: str, **kw):
        versions = self._versions()
        key = f"{query}|{kw.get('purpose')}|{kw.get('page', 0)}"
        cached = self.results.get(key, versions)
        if cached is not None:
            return cached
        response = self.gate.search(query, **kw)
        self.results.put(key, versions, response)
        return response

    def prompt_context(self, query: str, **kw) -> str:
        """The text that would actually reach the model.

        The last surface, and the one that matters most: everything upstream
        can be correct and this can still be handing a withdrawn record to a
        prompt.
        """
        versions = self._versions()
        key = f"ctx|{query}|{kw.get('purpose')}"
        cached = self.contexts.get(key, versions)
        if cached is not None:
            return cached
        response = self.search(query, **kw)
        context = "\n\n".join(h.text for h in getattr(response, "hits", ()))
        self.contexts.put(key, versions, context)
        return context

    def count_by(self, *a, **kw):
        return self.gate.count_by(*a, **kw)

    def stratum_subjects(self, *a, **kw):
        return self.gate.stratum_subjects(*a, **kw)

    def stats(self) -> list[dict]:
        return [c.stats() for c in (self.embeddings, self.results,
                                    self.contexts)]
