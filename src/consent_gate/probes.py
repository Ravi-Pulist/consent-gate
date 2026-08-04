"""The leak probes, written before the layer that has to defeat them.

Each probe runs unchanged against both configurations. That is the whole
design of the exhibit: nothing is measured under exclusion that was not
measured under masking first, so every claimed improvement has its baseline
sitting beside it in the same table rather than in a different document.

Three vectors, because there are three surfaces a retrieval system discloses
membership through:

**Narrative.** The prose re-identifies the patient after every direct
identifier is gone. "A 47-year-old lighthouse keeper from Kirkwall, known to
the team as Sparrow" names one person in the country. Masking removes the
name and leaves the sentence.

**Small cells.** A count over a stratum of two patients discloses both. It
does not matter that the record was masked — it was *counted*, and the count
is the disclosure.

**The oracle.** A hit, a count, even an empty result set is information about
who is in the index. Query a token unique to a withdrawn patient's record and
compare against a token that exists nowhere: any observable difference is the
leak, and the difference is usually the entire response.

A probe returns :class:`Leak` objects with the evidence attached, because a
leak count nobody can read is not an argument. The exhibit quotes them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .consent import Purpose


@dataclass(frozen=True)
class Leak:
    vector: str
    probe_id: str
    #: what a reader needs to see to agree this is a leak
    evidence: str
    subject: str = ""
    doc_id: str = ""

    def quote(self, width: int = 220) -> str:
        return self.evidence[:width].replace("\n", " ")


@dataclass
class ProbeResult:
    configuration: str
    leaks: list[Leak] = field(default_factory=list)
    probes_run: int = 0

    def by_vector(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for leak in self.leaks:
            out[leak.vector] = out.get(leak.vector, 0) + 1
        return out

    @property
    def clean(self) -> bool:
        return not self.leaks


# ── vector 1: the narrative ─────────────────────────────────────────────
def probe_narrative(gate, manifest: dict, purpose: Purpose,
                    when: datetime, non_consented: set[str]) -> list[Leak]:
    """Retrieve on the seeded quasi-identifier stories and see who comes back.

    A returned document belonging to a subject with no active consent is a
    leak whether or not its identifiers were masked, because the sentence
    still describes one person.
    """
    leaks: list[Leak] = []
    for trap in manifest.get("narrative_traps", []):
        response = gate.search(trap["query"], purpose=purpose, at=when)
        for hit in response.hits:
            if hit.subject in non_consented:
                leaks.append(Leak(
                    "narrative", trap["doc_id"],
                    evidence=_narrative_body(hit.text), subject=hit.subject,
                    doc_id=hit.doc_id))
    return leaks


#: Documents open with a block of structured fields, all of which the masking
#: baseline redacts perfectly. Quoting that block would show masking working.
#: The argument is about what comes *after* it, so that is what gets quoted.
_HEADER_END = "Record type:"


def _narrative_body(text: str) -> str:
    marker = text.find(_HEADER_END)
    if marker == -1:
        return text.strip()
    newline = text.find("\n", marker)
    return text[newline + 1:].strip() if newline != -1 else text.strip()


# ── vector 2: small-cell aggregates ─────────────────────────────────────
def probe_small_cells(gate, manifest: dict, purpose: Purpose,
                      when: datetime, non_consented: set[str]) -> list[Leak]:
    """Any aggregate that counts a non-consented member is a disclosure.

    Checked on the counts themselves rather than on the returned documents,
    because this is the vector that survives every amount of redaction: the
    record contributes a 1 to a cell of size 2 and the cell names both people.
    """
    leaks: list[Leak] = []
    counts = gate.count_by("condition", purpose=purpose, at=when)
    if not isinstance(counts, dict):          # fail-closed response object
        return leaks

    for cell in manifest.get("small_cells", []):
        hidden = set(cell.get("non_consented", {}).get(purpose.value, []))
        hidden &= non_consented
        if not hidden:
            continue
        observed = gate.stratum_subjects(cell["stratum"], purpose=purpose,
                                         at=when)
        disclosed = observed & hidden
        if disclosed:
            leaks.append(Leak(
                "small_cell", cell["stratum"],
                evidence=(f"stratum {cell['stratum']} has {len(observed)} "
                          f"member(s) in the counted set, of which "
                          f"{len(disclosed)} withdrew consent: "
                          f"{sorted(disclosed)}"),
                subject=sorted(disclosed)[0]))
    return leaks


# ── vector 3: the search oracle ─────────────────────────────────────────
def probe_oracle(gate, manifest: dict, purpose: Purpose,
                 when: datetime, non_consented: set[str]) -> list[Leak]:
    """Paired probes: a token only a hidden record contains, versus a token
    that exists nowhere.

    The assertion is byte-equality of the whole response — status, hits,
    count, pagination. Anything that differs is a channel, and a channel that
    answers "does this person exist in your index?" is the disclosure the
    other two vectors are only special cases of.
    """
    leaks: list[Leak] = []
    for pair in manifest.get("oracle_pairs", []):
        if pair.get("patient_id") and pair["patient_id"] not in non_consented:
            continue
        present = gate.search(pair["present_token"], purpose=purpose, at=when)
        absent = gate.search(pair["absent_token"], purpose=purpose, at=when)
        if present.fingerprint() != absent.fingerprint():
            leaks.append(Leak(
                "oracle", pair["present_token"],
                evidence=(
                    f"querying a token unique to a withdrawn record returned "
                    f"{present.count} match(es) and "
                    f"{len(present.hits)} hit(s); querying a token that "
                    f"exists nowhere returned {absent.count} match(es) and "
                    f"{len(absent.hits)} hit(s). The two are distinguishable, "
                    "so the index answers questions about who is in it."),
                subject=pair.get("patient_id", ""),
                doc_id=pair.get("doc_id", "")))
    return leaks


# ── pagination and counts, which are oracles in their own right ─────────
#: How far the pagination walk goes before giving up. A cap is necessary --
#: a leaking configuration can report tens of thousands of matches -- but a
#: capped walk must never be reported as a count leak, because the shortfall
#: is then the probe's own.
MAX_PAGES = 52


def probe_pagination(gate, purpose: Purpose, when: datetime,
                     queries: list[str]) -> list[Leak]:
    """A page that comes back short marks where a record was removed."""
    leaks: list[Leak] = []
    for q in queries:
        first = gate.search(q, purpose=purpose, at=when, page=0)
        if isinstance(first, dict) or first.error:
            continue
        seen, page, capped = 0, 0, False
        while True:
            r = gate.search(q, purpose=purpose, at=when, page=page)
            seen += len(r.hits)
            if not r.has_more:
                break
            if page >= MAX_PAGES:
                # The walk stopped because *this probe* gave up, not because
                # the result set ran out. Any count mismatch from here on is
                # the probe's own doing, and reporting it as a leak would be
                # a fabricated finding dressed as a measurement.
                capped = True
                break
            if len(r.hits) < r.page_size:
                leaks.append(Leak(
                    "pagination", f"{q}#p{page}",
                    evidence=(f"page {page} of {q!r} returned "
                              f"{len(r.hits)} of {r.page_size} while "
                              "reporting more pages available; the gap marks "
                              "a removed record")))
                break
            page += 1

        if capped:
            continue
        if seen and first.count != seen:
            leaks.append(Leak(
                "count", q,
                evidence=(f"{q!r} reported {first.count} matches but "
                          f"{seen} were reachable by paging; the difference "
                          "is the set that was filtered out of the results "
                          "after being counted")))
    return leaks


def run_all(gate, manifest: dict, purpose: Purpose, when: datetime,
            non_consented: set[str], queries: list[str]) -> ProbeResult:
    result = ProbeResult(configuration=getattr(gate, "mode", "?"))
    result.leaks.extend(probe_narrative(gate, manifest, purpose, when,
                                        non_consented))
    result.leaks.extend(probe_small_cells(gate, manifest, purpose, when,
                                          non_consented))
    result.leaks.extend(probe_oracle(gate, manifest, purpose, when,
                                     non_consented))
    result.leaks.extend(probe_pagination(gate, purpose, when, queries))
    result.probes_run = (len(manifest.get("narrative_traps", []))
                         + len(manifest.get("small_cells", []))
                         + len(manifest.get("oracle_pairs", []))
                         + len(queries))
    return result
