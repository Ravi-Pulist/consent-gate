"""The exhibit: one corpus, one probe suite, two configurations, diffed.

Nothing is measured under exclusion that was not measured under masking
first, so every claimed improvement has its baseline in the same table rather
than in a different document. The report quotes the leaking passages, because
a leak count nobody can read is not an argument — watching a perfectly masked
narrative name its patient is.

The masking side is not a straw man. It masks every direct identifier at 100%
recall, which is possible only because the corpus is synthetic and every
identifier's offset is known. A real NER-based masker misses identifiers
outright, so this exhibit *understates* masking's real-world failure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .cache import CachedGate
from .consent import Consent, Purpose
from .adapters.sqlite_index import SqliteIndex
from .gate import MaskingGate, QueryGate
from .probes import ProbeResult, run_all
from .revocation import run_drill
from .store import ConsentStore


@dataclass
class Corpus:
    patients: list[dict]
    documents: list[dict]
    consents: list[dict]
    manifest: dict


def load_corpus(data_dir: Path) -> Corpus:
    def j(name):
        return json.loads((data_dir / name).read_text(encoding="utf-8"))
    return Corpus(j("patients.json"), j("documents.json"), j("consents.json"),
                  j("manifest.json"))


def build(corpus: Corpus) -> tuple[ConsentStore, SqliteIndex]:
    store = ConsentStore()
    index = SqliteIndex()
    for record in corpus.consents:
        store.record(Consent(
            subject=record["subject"],
            purposes=frozenset(Purpose(p) for p in record["purposes"]),
            valid_from=datetime.fromisoformat(record["valid_from"]),
            valid_until=(datetime.fromisoformat(record["valid_until"])
                         if record.get("valid_until") else None),
            revoked_at=(datetime.fromisoformat(record["revoked_at"])
                        if record.get("revoked_at") else None),
            scope=frozenset(record.get("scope", ())),
            version=record.get("version", 1)))
    by_patient = {p["patient_id"]: p for p in corpus.patients}
    for doc in corpus.documents:
        p = by_patient.get(doc["patient_id"], {})
        index.add(doc["doc_id"], doc["patient_id"], doc["text"],
                  title=doc.get("title", ""),
                  doc_type=doc.get("doc_type", ""),
                  region=p.get("region", ""), condition=p.get("condition", ""),
                  identifiers=doc.get("identifiers"))
    return store, index


def narrative_queries(corpus: Corpus) -> list[dict]:
    """Turn each seeded narrative trap into the query that surfaces it.

    The query is drawn from the trap span itself — the quasi-identifier
    story, not the masked name — because that is precisely the text masking
    leaves behind.
    """
    by_id = {d["doc_id"]: d for d in corpus.documents}
    out = []
    for trap in corpus.manifest.get("narrative_traps", []):
        doc = by_id.get(trap["doc_id"])
        if not doc:
            continue
        span = trap.get("span") or [0, 120]
        phrase = doc["text"][span[0]:span[1]]
        # strip any identifier surfaces so the probe is a story query, not a
        # name query — masking must be given its best case
        for ident in doc.get("identifiers", []):
            phrase = phrase.replace(doc["text"][ident["start"]:ident["end"]],
                                    " ")
        out.append({**trap, "query": " ".join(phrase.split())[:120]})
    return out


def non_consented_for(store: ConsentStore, corpus: Corpus, purpose: Purpose,
                      when: datetime) -> set[str]:
    everyone = {p["patient_id"] for p in corpus.patients}
    return everyone - store.consented_subjects(purpose, when)


@dataclass
class Exhibit:
    masking: ProbeResult
    exclusion: ProbeResult
    drill: dict
    plan_exclusion: str
    plan_masking: str
    purpose: str
    when: str
    counts: dict


def run(data_dir: Path, purpose: Purpose = Purpose.DIRECT_CARE) -> Exhibit:
    corpus = load_corpus(data_dir)
    when = datetime.fromisoformat(corpus.manifest["now"])
    store, index = build(corpus)

    manifest = dict(corpus.manifest)
    manifest["narrative_traps"] = narrative_queries(corpus)
    hidden = non_consented_for(store, corpus, purpose, when)

    page_queries = ["review", "routine", "follow up"]
    exclusion = QueryGate(store, index)
    masking = MaskingGate(store, index)

    result_masking = run_all(masking, manifest, purpose, when, hidden,
                             page_queries)
    result_exclusion = run_all(exclusion, manifest, purpose, when, hidden,
                               page_queries)

    # The consented set is snapshotted *before* the drill, which revokes a
    # subject. Reading it afterwards reported 79 consented against 120 not,
    # summing to 199 of 200 patients — the drill's own revocation leaking
    # into the corpus description.
    consented = store.consented_subjects(purpose, when)

    subject = _drill_subject(store, corpus, purpose, when)
    drill = run_drill(CachedGate(QueryGate(store, index)), store, subject,
                      _drill_query(corpus, subject), purpose, when,
                      stratum=_drill_stratum(corpus, subject))

    return Exhibit(
        masking=result_masking, exclusion=result_exclusion,
        drill=drill.as_dict(),
        plan_exclusion=index.explain("review", consented),
        plan_masking=index.explain("review", None),
        purpose=purpose.value, when=when.isoformat(),
        counts={"patients": len(corpus.patients),
                "documents": len(corpus.documents),
                "consented_subjects": len(consented),
                "non_consented_subjects": len(hidden)})


def _drill_subject(store, corpus, purpose, when) -> str:
    consented = store.consented_subjects(purpose, when)
    for p in corpus.patients:
        if p["patient_id"] in consented:
            return p["patient_id"]
    return corpus.patients[0]["patient_id"]


def _drill_stratum(corpus, subject: str) -> str | None:
    """The stratum the drill subject belongs to.

    Passing this matters: without it the drill's `aggregates` surface never
    consults the gate at all, so it reports "dark" without having checked
    anything. A surface that measures nothing must not sit in a table of
    measurements looking like a pass.
    """
    for p in corpus.patients:
        if p["patient_id"] == subject:
            return f"cond={p.get('condition', '')}|region={p.get('region', '')}"
    return None


def _drill_query(corpus, subject) -> str:
    for d in corpus.documents:
        if d["patient_id"] == subject:
            words = [w for w in d["text"].split() if w.isalpha()][:4]
            return " ".join(words) or "review"
    return "review"


# ── the report ──────────────────────────────────────────────────────────
def markdown(ex: Exhibit) -> str:
    m, e = ex.masking, ex.exclusion
    L: list[str] = []
    L.append("# Consent as exclusion, not masking\n")
    L.append("Same corpus. Same queries. Two configurations. The only "
             "difference is where consent is applied: masking redacts what it "
             "retrieved, exclusion never retrieves it.\n")

    L.append("| | masking | exclusion |")
    L.append("|---|---|---|")
    L.append(f"| probes run | {m.probes_run} | {e.probes_run} |")
    L.append(f"| **total leaks** | **{len(m.leaks)}** | **{len(e.leaks)}** |")
    vectors = sorted(set(m.by_vector()) | set(e.by_vector()))
    for v in vectors:
        L.append(f"| {v} leaks | {m.by_vector().get(v, 0)} | "
                 f"{e.by_vector().get(v, 0)} |")
    L.append("")
    L.append(f"Corpus: {ex.counts['patients']} patients, "
             f"{ex.counts['documents']} documents. For purpose "
             f"`{ex.purpose}` at {ex.when}, "
             f"{ex.counts['consented_subjects']} subjects are consented and "
             f"{ex.counts['non_consented_subjects']} are not.\n")

    L.append("> The masking configuration masks every direct identifier at "
             "**100% recall**, which is only possible because the corpus is "
             "synthetic and every identifier's offset is known. Every leak "
             "below survives a perfect masker. A real NER masker also misses "
             "identifiers outright, so this understates the failure.\n")

    # ── the leaks, quoted ───────────────────────────────────────────────
    for vector, title, note in [
        ("narrative", "The narrative leak",
         "The name is gone. The sentence still describes one person."),
        ("small_cell", "The small-cell leak",
         "It does not matter that the record was masked. It was counted, and "
         "the count is the disclosure."),
        ("oracle", "The search-oracle leak",
         "A token unique to a withdrawn record, against a token that exists "
         "nowhere. Any observable difference answers 'is this person in your "
         "index?'"),
    ]:
        found = [x for x in m.leaks if x.vector == vector]
        L.append(f"## {title}\n")
        L.append(f"{note}\n")
        L.append(f"**Masking: {len(found)} leaks. Exclusion: "
                 f"{len([x for x in e.leaks if x.vector == vector])}.**\n")
        for leak in found[:3]:
            L.append(f"> {leak.quote()}\n")
            where = leak.doc_id or leak.probe_id
            if leak.subject:
                L.append(f"`subject {leak.subject}` · `{where}` · surfaced by "
                         f"probe `{leak.probe_id}`\n")
        if len(found) > 3:
            L.append(f"_{len(found) - 3} further instances in the JSON "
                     "output._\n")

    # ── the query plan ──────────────────────────────────────────────────
    L.append("## The predicate is inside the query plan\n")
    L.append("Not applied to results afterwards. This is the engine's own "
             "plan for the query that ran:\n")
    L.append("**Exclusion**\n```\n" + ex.plan_exclusion + "\n```\n")
    L.append("**Masking** (no consent predicate exists to push down)\n```\n"
             + ex.plan_masking + "\n```\n")

    # ── revocation ──────────────────────────────────────────────────────
    d = ex.drill
    L.append("## Revocation, measured\n")
    L.append(f"Mid-session revocation of `{d['subject']}` with caches warm. "
             f"Every surface dark: **{d['all_dark']}**. Worst surface: "
             f"**{d['worst_seconds']:.4f} s**.\n")
    L.append("| surface | visible before | visible after | seconds to dark |")
    L.append("|---|---|---|---|")
    for s in d["surfaces"]:
        secs = "-" if s["seconds_to_dark"] is None else f"{s['seconds_to_dark']:.4f}"
        L.append(f"| {s['surface']} | {s['visible_before']} | "
                 f"{s['visible_after']} | {secs} |")
    L.append("")
    L.append("> A surface that was never visible before revocation proves "
             "nothing about revocation, so it is not counted as a pass.\n")

    L.append("## What this does not close\n")
    L.append("- **Timing.** Exclusion closes the content channel. Response "
             "time is a separate channel and is measured separately; it is "
             "not claimed closed here.")
    L.append("- **Corpus totals.** If overall corpus size is published, "
             "consented-count changes leak consent *events* by differencing. "
             "The gate exposes no totals; a deployment that does reopens "
             "this.")
    L.append("- **Retrieval quality** is not what this exhibit tests. The "
             "embeddings are a deterministic hashed bag of words so a sceptic "
             "can reproduce the run without downloading a model.\n")
    return "\n".join(L)


def as_json(ex: Exhibit) -> dict:
    def leaks(r):
        return [{"vector": x.vector, "probe_id": x.probe_id,
                 "subject": x.subject, "doc_id": x.doc_id,
                 "evidence": x.evidence} for x in r.leaks]
    return {
        "purpose": ex.purpose, "when": ex.when, "counts": ex.counts,
        "masking": {"probes_run": ex.masking.probes_run,
                    "total_leaks": len(ex.masking.leaks),
                    "by_vector": ex.masking.by_vector(),
                    "leaks": leaks(ex.masking)},
        "exclusion": {"probes_run": ex.exclusion.probes_run,
                      "total_leaks": len(ex.exclusion.leaks),
                      "by_vector": ex.exclusion.by_vector(),
                      "leaks": leaks(ex.exclusion)},
        "revocation_drill": ex.drill,
        "query_plan": {"exclusion": ex.plan_exclusion,
                       "masking": ex.plan_masking},
    }
