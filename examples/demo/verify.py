#!/usr/bin/env python3
"""Re-derive every figure printed in index.html and README.md beside it.

The demo makes a claim that is cheap to make and expensive to keep: no number
on the page was typed from memory. This script is how a reader checks it. Each
figure the page prints is recomputed here from one of four sources, and the
source is named in the output:

    exhibit    examples/demo/exhibits/exhibit.json (a copy of exhibits/)
    manifest   corpus/data/manifest.json
    live       a replay of the query against the real gate, built from
               corpus/data and src/consent_gate
    repo       counted out of the tests directory

The live tier needs src/ and corpus/data/ next to this checkout. If they are
missing, those checks report SKIP rather than PASS, and the tally says so.

    python examples/demo/verify.py
    python examples/demo/verify.py --no-live
    python examples/demo/verify.py --exhibits path/to/exhibits

Exit code 0 if every check that ran passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

PASSED: list[str] = []
FAILED: list[str] = []
SKIPPED: list[str] = []


def check(source: str, label: str, actual, expected) -> bool:
    """Compare one printed figure against its recomputed value."""
    ok = actual == expected
    (PASSED if ok else FAILED).append(label)
    shown = actual if not isinstance(actual, str) else _clip(actual)
    print(f"  {'PASS' if ok else 'FAIL'}  [{source:8}] {label:<54} {shown!r}")
    if not ok:
        print(f"        page says {_clip(str(expected))!r}")
    return ok


def skip(source: str, label: str, why: str) -> None:
    SKIPPED.append(label)
    print(f"  SKIP  [{source:8}] {label:<54} {why}")


def _clip(text: str, width: int = 68) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: width - 3] + "..."


def section(title: str) -> None:
    print(f"\n{title}")


# ---------------------------------------------------------------- expected
# Everything below is what the demo prints. Nothing here is computed; these
# are the literals a reader can find on the page.

NARRATIVE_QUOTE = (
    "This contact was a scheduled review of Harrowfield anaemia. Beacon, as "
    "the ward has called them since childhood, is 42 and still working as a "
    "lock keeper on the Cinderfell shorefront. Transport support was offered "
    "and declined for the next appointment. Bloods were repeated and the "
    "trend since the last review is unchanged. Plan: repeat bloods before "
    "the next appointment, otherwise unchanged."
)
NARRATIVE_QUOTE_SHORT = (
    "This contact was a scheduled review of Harrowfield anaemia. Beacon, as "
    "the ward has called them since childhood, is 42 and still working as a "
    "lock keeper on the Cinderfell shorefront."
)
NARRATIVE_QUOTE_2 = (
    "The encounter covered ongoing management of Sable-tract colitis and "
    "nothing further. Wick, as the ward has called them since childhood, is "
    "32 and still working as a cartwright on the Bramleigh shorefront."
)
ORACLE_QUOTE = (
    "querying a token unique to a withdrawn record returned 1 match(es) and "
    "1 hit(s); querying a token that exists nowhere returned 0 match(es) and "
    "0 hit(s). The two are distinguishable, so the index answers questions "
    "about who is in it."
)
SMALL_CELL_QUOTE = (
    "stratum cond=Ashcombe myopathy|region=Vellum Bay has 2 member(s) in the "
    "counted set, of which 1 withdrew consent: ['P184']"
)
COUNT_QUOTES = [
    "'review' reported 847 matches but 520 were reachable by paging; the "
    "difference is the set that was filtered out of the results after being "
    "counted",
    "'follow up' reported 625 matches but 520 were reachable by paging; the "
    "difference is the set that was filtered out of the results after being "
    "counted",
]
PLAN_EXCLUSION = (
    "SCAN d\n"
    "SEARCH c USING COVERING INDEX sqlite_autoindex_consented_1 (subject=?)\n"
    "USE TEMP B-TREE FOR ORDER BY"
)
PLAN_MASKING = "SCAN d\nUSE TEMP B-TREE FOR ORDER BY"
DRILL_ROWS = [
    ("vector+keyword search", True, False, 0.0799),
    ("result cache", True, False, 0.0001),
    ("prompt context", True, False, 0.0002),
    ("aggregates", True, False, 0.0),
]

QUERY = ("Larkspur, as the ward has called them since childhood, is 48 and "
         "still working as a salt panner on the Aldermoor shorefr")

MASKING_PAGE = [
    ("D0003", "P001", 0.405), ("D0004", "P001", 0.3988),
    ("D0155", "P030", 0.2624), ("D0997", "P200", 0.2548),
    ("D0683", "P138", 0.2537), ("D0637", "P127", 0.2508),
    ("D0895", "P179", 0.2494), ("D0543", "P110", 0.245),
    ("D0930", "P185", 0.2393), ("D0248", "P050", 0.237),
]
EXCLUSION_PAGE = [
    ("D0003", "P001", 0.405), ("D0004", "P001", 0.3988),
    ("D0155", "P030", 0.2624), ("D0997", "P200", 0.2548),
    ("D0895", "P179", 0.2494), ("D0930", "P185", 0.2393),
    ("D0113", "P022", 0.2347), ("D0771", "P155", 0.2319),
    ("D0617", "P124", 0.2263), ("D0550", "P111", 0.2182),
]
MASKED_D0683 = """Laboratory report
Patient: [NAME]
MRN: [MRN]
Phone: [PHONE]
Email: [EMAIL]
Address: [ADDRESS]
Encounter date: 2024-11-02
Record type: lab_report

This contact was a scheduled review of Harrowfield anaemia. Beacon, as the \
ward has called them since childhood, is 42 and still working as a lock \
keeper on the Cinderfell shorefront. Transport support was offered and \
declined for the next appointment. Bloods were repeated and the trend since \
the last review is unchanged. Plan: repeat bloods before the next \
appointment, otherwise unchanged.
"""

FP_SAME = "009ffefc0e4a378bebb8a7e0ad3b3397d082f70f349e469be012da717e9c4ff8"
FP_PRESENT_MASKING = (
    "0a5abcd39ea90bcc46033d6323413ae88a1dda5dd02ba013b4566283a238b79a")


# ------------------------------------------------------------------ tiers
def tier_exhibit(ex: dict) -> None:
    section("exhibit  (examples/demo/exhibits/exhibit.json)")
    m, e, c = ex["masking"], ex["exclusion"], ex["counts"]

    check("exhibit", "purpose", ex["purpose"], "direct_care")
    check("exhibit", "measured at", ex["when"], "2026-08-04T00:00:00+00:00")
    check("exhibit", "patients", c["patients"], 200)
    check("exhibit", "documents", c["documents"], 1000)
    check("exhibit", "consented subjects", c["consented_subjects"], 79)
    check("exhibit", "non-consented subjects", c["non_consented_subjects"],
          120)

    check("exhibit", "probes run, masking", m["probes_run"], 113)
    check("exhibit", "probes run, exclusion", e["probes_run"], 113)
    check("exhibit", "total leaks, masking", m["total_leaks"], 537)
    check("exhibit", "total leaks, exclusion", e["total_leaks"], 0)
    check("exhibit", "leak records present, masking", len(m["leaks"]), 537)
    check("exhibit", "leak records present, exclusion", len(e["leaks"]), 0)
    check("exhibit", "by_vector sums to the total",
          sum(m["by_vector"].values()), m["total_leaks"])

    for vector, count in (("narrative", 489), ("oracle", 30),
                          ("small_cell", 16), ("count", 2)):
        check("exhibit", f"{vector} leaks, masking",
              m["by_vector"].get(vector, 0), count)
        check("exhibit", f"{vector} leaks, exclusion",
              e["by_vector"].get(vector, 0), 0)

    narrative = [x for x in m["leaks"] if x["vector"] == "narrative"]
    check("exhibit", "narrative quote, verbatim", narrative[0]["evidence"],
          NARRATIVE_QUOTE)
    check("exhibit", "narrative quote provenance",
          (narrative[0]["probe_id"], narrative[0]["subject"],
           narrative[0]["doc_id"]), ("D0003", "P138", "D0683"))
    check("exhibit", "narrative quote as shortened on the page",
          narrative[0]["evidence"].startswith(NARRATIVE_QUOTE_SHORT), True)
    second = next(x for x in narrative if x["doc_id"] == "D0543")
    check("exhibit", "second narrative quote, as shortened on the page",
          second["evidence"].startswith(NARRATIVE_QUOTE_2), True)
    check("exhibit", "second narrative quote provenance",
          (second["probe_id"], second["subject"]), ("D0003", "P110"))
    check("exhibit", "distinct documents leaked, narrative",
          len({x["doc_id"] for x in narrative}), 59)
    check("exhibit", "distinct people leaked, narrative",
          len({x["subject"] for x in narrative}), 26)
    check("exhibit", "probes that leaked, narrative",
          len({x["probe_id"] for x in narrative}), 60)

    probe_d0003 = [x for x in narrative if x["probe_id"] == "D0003"]
    check("exhibit", "leaks surfaced by probe D0003", len(probe_d0003), 4)
    check("exhibit", "subjects surfaced by probe D0003",
          sorted(x["subject"] for x in probe_d0003),
          ["P050", "P110", "P127", "P138"])
    check("exhibit", "documents surfaced by probe D0003",
          sorted(x["doc_id"] for x in probe_d0003),
          ["D0248", "D0543", "D0637", "D0683"])

    oracle = [x for x in m["leaks"] if x["vector"] == "oracle"]
    check("exhibit", "oracle quote, verbatim", oracle[0]["evidence"],
          ORACLE_QUOTE)
    check("exhibit", "oracle quote provenance",
          (oracle[0]["probe_id"], oracle[0]["subject"], oracle[0]["doc_id"]),
          ("ZZORACLE-010-kejr", "P004", "D0018"))

    cells = [x for x in m["leaks"] if x["vector"] == "small_cell"]
    check("exhibit", "small-cell quote, verbatim", cells[0]["evidence"],
          SMALL_CELL_QUOTE)
    check("exhibit", "small-cell quote subject", cells[0]["subject"], "P184")
    check("exhibit", "strata of exactly one person",
          sum(1 for x in cells if "has 1 member(s)" in x["evidence"]), 5)

    counts = [x["evidence"] for x in m["leaks"] if x["vector"] == "count"]
    check("exhibit", "count-channel evidence, verbatim", counts, COUNT_QUOTES)

    check("exhibit", "query plan, exclusion", ex["query_plan"]["exclusion"],
          PLAN_EXCLUSION)
    check("exhibit", "query plan, masking", ex["query_plan"]["masking"],
          PLAN_MASKING)

    d = ex["revocation_drill"]
    check("exhibit", "drill subject", d["subject"], "P001")
    check("exhibit", "drill purpose", d["purpose"], "direct_care")
    check("exhibit", "every surface dark", d["all_dark"], True)
    check("exhibit", "worst surface, seconds", d["worst_seconds"], 0.0799)
    check("exhibit", "worst surface, printed to 2 dp",
          f"{d['worst_seconds']:.2f}", "0.08")
    check("exhibit", "worst surface is the slowest measured surface",
          d["worst_seconds"],
          max(s["seconds_to_dark"] for s in d["surfaces"]))
    check("exhibit", "worst surface is inside the 5 s target",
          d["worst_seconds"] < 5.0, True)
    check("exhibit", "drill total, seconds", d["total_seconds"], 0.16)
    check("exhibit", "drill surfaces",
          [(s["surface"], s["visible_before"], s["visible_after"],
            s["seconds_to_dark"]) for s in d["surfaces"]], DRILL_ROWS)
    check("exhibit", "every drill surface counted as a pass",
          all(s["ok"] for s in d["surfaces"]), True)


def tier_manifest(ex: dict, manifest_path: Path) -> None:
    section("manifest  (corpus/data/manifest.json)")
    if not manifest_path.exists():
        skip("manifest", "every manifest figure", f"no {manifest_path}")
        return
    man = json.loads(manifest_path.read_text(encoding="utf-8"))

    check("manifest", "seed", man["seed"], 20260804)
    check("manifest", "synthetic only", man["synthetic_only"], True)
    check("manifest", "generator sha256", man["generator_sha256"],
          "482304f5aaa31fa09fe98f540d929a291952de8d857097bd7dd4ae833a18c450")
    check("manifest", "manifest now matches exhibit when", man["now"],
          ex["when"])
    check("manifest", "patients", man["counts"]["patients"], 200)
    check("manifest", "documents", man["counts"]["documents"], 1000)
    check("manifest", "consent records", man["counts"]["consents"], 170)

    states = man["consent_states"]
    check("manifest", "consent states",
          [states[k] for k in ("active_direct_care", "active_research",
                               "expired", "revoked", "never", "future")],
          [80, 30, 20, 30, 30, 10])
    check("manifest", "consent states cover every patient",
          sum(states.values()), man["counts"]["patients"])

    traps = len(man["narrative_traps"])
    cells = len(man["small_cells"])
    pairs = len(man["oracle_pairs"])
    check("manifest", "seeded narrative traps", traps, 60)
    check("manifest", "seeded small cells", cells, 20)
    check("manifest", "seeded oracle pairs", pairs, 30)
    check("manifest", "probes = traps + cells + pairs + 3 paging queries",
          traps + cells + pairs + 3, ex["masking"]["probes_run"])
    check("manifest", "oracle pairs on non-direct-care subjects",
          sum(1 for p in man["oracle_pairs"]
              if p["consent_state"] != "active_direct_care"),
          ex["masking"]["by_vector"]["oracle"])
    check("manifest", "small cells holding a non-consented member",
          sum(1 for c in man["small_cells"]
              if c["non_consented"]["direct_care"]),
          ex["masking"]["by_vector"]["small_cell"])
    check("manifest", "identifiers with known offsets", man["identifier_count"],
          5000)
    check("manifest", "identifiers per document",
          man["identifier_count"] // man["counts"]["documents"], 5)

    pair = man["oracle_pairs"][0]
    check("manifest", "oracle pair tokens",
          (pair["present_token"], pair["absent_token"]),
          ("ZZORACLE-010-kejr", "ZZORACLE-010-vbbs"))
    check("manifest", "oracle pair record",
          (pair["doc_id"], pair["patient_id"], pair["consent_state"]),
          ("D0018", "P004", "never"))

    by_doc = {t["doc_id"]: t for t in man["narrative_traps"]}
    check("manifest", "trap behind the demo query",
          (by_doc["D0003"]["patient_id"], by_doc["D0003"]["nickname"],
           by_doc["D0003"]["age"], by_doc["D0003"]["occupation"],
           by_doc["D0003"]["place"], by_doc["D0003"]["consent_state"]),
          ("P001", "Larkspur", 48, "salt panner", "Aldermoor",
           "active_direct_care"))
    check("manifest", "trap behind the quoted leak",
          (by_doc["D0683"]["patient_id"], by_doc["D0683"]["nickname"],
           by_doc["D0683"]["age"], by_doc["D0683"]["occupation"],
           by_doc["D0683"]["place"], by_doc["D0683"]["consent_state"]),
          ("P138", "Beacon", 42, "lock keeper", "Cinderfell", "revoked"))
    check("manifest", "leaked documents that were not seeded traps",
          sum(1 for d in ("D0248", "D0543", "D0637", "D0683")
              if d not in by_doc), 2)


def tier_live(ex: dict) -> None:
    section("live  (replay against src/consent_gate over corpus/data)")
    src, data = ROOT / "src", ROOT / "corpus" / "data"
    if not src.exists() or not (data / "documents.json").exists():
        skip("live", "every replayed figure", f"no {src} or {data}")
        return
    sys.path.insert(0, str(src))
    try:
        from datetime import datetime

        from consent_gate.consent import Purpose
        from consent_gate.exhibit import (build, load_corpus,
                                          narrative_queries,
                                          non_consented_for)
        from consent_gate.gate import MaskingGate, QueryGate
    except Exception as exc:                                   # noqa: BLE001
        skip("live", "every replayed figure", f"import failed: {exc}")
        return

    corpus = load_corpus(data)
    store, index = build(corpus)
    when = datetime.fromisoformat(corpus.manifest["now"])
    purpose = Purpose.DIRECT_CARE
    hidden = non_consented_for(store, corpus, purpose, when)
    consented = store.consented_subjects(purpose, when)

    check("live", "consented at the measured instant", len(consented), 80)
    check("live", "non-consented at the measured instant", len(hidden), 120)
    check("live", "the two partition the corpus",
          len(consented) + len(hidden), ex["counts"]["patients"])
    check("live", "P001 is consented before the drill", "P001" in consented,
          True)

    trap = next(t for t in narrative_queries(corpus) if t["doc_id"] == "D0003")
    check("live", "demo query", trap["query"], QUERY)

    masking, exclusion = MaskingGate(store, index), QueryGate(store, index)
    rm = masking.search(trap["query"], purpose=purpose, at=when)
    re_ = exclusion.search(trap["query"], purpose=purpose, at=when)

    check("live", "matches reported, masking", rm.count, 533)
    check("live", "matches reported, exclusion", re_.count, 204)
    check("live", "pages of results, masking",
          -(-rm.count // rm.page_size), 54)
    check("live", "pages of results, exclusion",
          -(-re_.count // re_.page_size), 21)
    check("live", "page size", (rm.page_size, re_.page_size), (10, 10))
    check("live", "full first page under both, no short page",
          (len(rm.hits), len(re_.hits)), (10, 10))
    check("live", "first page, masking",
          [(h.doc_id, h.subject, round(h.score, 4)) for h in rm.hits],
          MASKING_PAGE)
    check("live", "first page, exclusion",
          [(h.doc_id, h.subject, round(h.score, 4)) for h in re_.hits],
          EXCLUSION_PAGE)
    check("live", "non-consented records on the page, masking",
          [h.doc_id for h in rm.hits if h.subject in hidden],
          ["D0683", "D0637", "D0543", "D0248"])
    check("live", "non-consented records on the page, exclusion",
          [h.doc_id for h in re_.hits if h.subject in hidden], [])

    # the four ways consent fails on that page, as the page labels them
    consents = json.loads((data / "consents.json").read_text(encoding="utf-8"))
    by_subject: dict[str, list[dict]] = {}
    for record in consents:
        by_subject.setdefault(record["subject"], []).append(record)
    labelled = {}
    for subject in ("P138", "P127", "P110", "P050"):
        rows = by_subject[subject]
        labelled[subject] = (
            sorted({p for r in rows for p in r["purposes"]}),
            [r["revoked_at"] for r in rows],
            [r["valid_from"] for r in rows])
    check("live", "P138 labelled revoked", labelled["P138"],
          (["research"], ["2025-08-02T00:00:00+00:00"],
           ["2024-11-03T00:00:00+00:00"]))
    check("live", "P127 labelled other purpose", labelled["P127"],
          (["outreach", "research"], [None], ["2024-07-08T00:00:00+00:00"]))
    check("live", "P110 labelled other purpose", labelled["P110"],
          (["research"], [None], ["2023-08-07T00:00:00+00:00"]))
    check("live", "P050 labelled not yet valid", labelled["P050"],
          (["direct_care"], [None], ["2026-08-21T00:00:00+00:00"]))
    check("live", "days between the query instant and P050's consent",
          (datetime.fromisoformat("2026-08-21T00:00:00+00:00") - when).days, 17)

    leaked = next(h for h in rm.hits if h.doc_id == "D0683")
    check("live", "masked document, verbatim", leaked.text, MASKED_D0683)
    check("live", "every direct identifier replaced",
          sorted({t for t in ("[NAME]", "[MRN]", "[PHONE]", "[EMAIL]",
                              "[ADDRESS]") if t in leaked.text}),
          ["[ADDRESS]", "[EMAIL]", "[MRN]", "[NAME]", "[PHONE]"])
    raw = json.loads((data / "documents.json").read_text(encoding="utf-8"))
    surfaces = [i["surface"] for d in raw if d["doc_id"] == "D0683"
                for i in d["identifiers"]]
    check("live", "identifier surfaces remaining in the masked text",
          [s for s in surfaces if s in leaked.text], [])
    check("live", "identifier surfaces in that document", len(surfaces), 5)

    pair = corpus.manifest["oracle_pairs"][0]
    fp = {}
    for name, gate in (("masking", masking), ("exclusion", exclusion)):
        present = gate.search(pair["present_token"], purpose=purpose, at=when)
        absent = gate.search(pair["absent_token"], purpose=purpose, at=when)
        fp[name] = (present.fingerprint(), absent.fingerprint(),
                    present.count, absent.count)
    check("live", "oracle pair, masking: present token response",
          (fp["masking"][0], fp["masking"][2]), (FP_PRESENT_MASKING, 1))
    check("live", "oracle pair, masking: absent token response",
          (fp["masking"][1], fp["masking"][3]), (FP_SAME, 0))
    check("live", "oracle pair, masking: distinguishable",
          fp["masking"][0] != fp["masking"][1], True)
    check("live", "oracle pair, exclusion: both fingerprints",
          (fp["exclusion"][0], fp["exclusion"][1]), (FP_SAME, FP_SAME))
    check("live", "oracle pair, exclusion: both counts",
          (fp["exclusion"][2], fp["exclusion"][3]), (0, 0))

    walked = {}
    for name, gate in (("masking", masking), ("exclusion", exclusion)):
        for q in ("review", "follow up"):
            first = gate.search(q, purpose=purpose, at=when)
            seen, page = 0, 0
            while True:
                r = gate.search(q, purpose=purpose, at=when, page=page)
                seen += len(r.hits)
                if not r.has_more or page > 50:
                    break
                page += 1
            walked[(name, q)] = (first.count, seen, page > 50)
    check("live", "'review' count and page walk, masking",
          walked[("masking", "review")], (847, 520, True))
    check("live", "'review' count and page walk, exclusion",
          walked[("exclusion", "review")], (326, 326, False))
    check("live", "'follow up' count and page walk, masking",
          walked[("masking", "follow up")], (625, 520, True))
    check("live", "'follow up' count and page walk, exclusion",
          walked[("exclusion", "follow up")], (248, 248, False))

    store.revoke("P001", at=when)
    check("live", "consented after the drill revoked P001",
          len(store.consented_subjects(purpose, when)),
          ex["counts"]["consented_subjects"])


def tier_repo() -> None:
    section("repo  (tests/)")
    tests = ROOT / "tests"
    if not tests.exists():
        skip("repo", "test counts", f"no {tests}")
        return
    counted = _collected_tests()
    if counted is None:
        # Fall back to counting test functions in the source. It undercounts
        # parametrised cases, so it is reported as a floor rather than as the
        # figure the page prints.
        floor = 0
        for path in sorted(tests.rglob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            floor += sum(
                1 for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name.startswith("test_"))
        skip("repo", "tests",
             f"pytest could not collect; {floor} test functions in source")
        skip("repo", "sabotage cases", "pytest could not collect")
        return
    check("repo", "tests", sum(counted.values()), 70)
    check("repo", "sabotage cases", counted.get("test_sabotage.py", 0), 9)


def _collected_tests() -> dict[str, int] | None:
    """Ask pytest what it collects, per file.

    Counting ``def test_`` in the source undercounts: one case in
    tests/test_gate.py is parametrised five ways.
    """
    import subprocess
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            cwd=ROOT, capture_output=True, text=True, timeout=300)
    except Exception:                                          # noqa: BLE001
        return None
    counted: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        name, sep, tail = line.partition(".py: ")
        if sep and tail.strip().isdigit():
            counted[Path(name.strip()).name + ".py"] = int(tail.strip())
    return counted or None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exhibits", default=str(HERE / "exhibits"),
                    help="directory holding exhibit.json")
    ap.add_argument("--no-live", action="store_true",
                    help="skip the replay against src/ and corpus/")
    args = ap.parse_args(argv[1:])

    exhibits = Path(args.exhibits).resolve()
    ex = json.loads((exhibits / "exhibit.json").read_text(encoding="utf-8"))
    print(f"exhibits:  {exhibits}")
    print(f"repo root: {ROOT}")

    tier_exhibit(ex)
    tier_manifest(ex, ROOT / "corpus" / "data" / "manifest.json")
    if args.no_live:
        section("live  (replay against src/consent_gate over corpus/data)")
        skip("live", "every replayed figure", "disabled with --no-live")
    else:
        tier_live(ex)
    tier_repo()

    total = len(PASSED) + len(FAILED)
    print(f"\n{len(PASSED)}/{total} checks passed, {len(SKIPPED)} skipped")
    for label in FAILED:
        print(f"  FAILED: {label}")
    if SKIPPED:
        print("  numbers left unchecked by this run:")
        for label in SKIPPED:
            print(f"    {label}")
    print("\nunchecked by design, with the file that records each:")
    for figure, where in (
        ("byte-reproducibility of the corpus from seed 20260804",
         "corpus/generate.py, asserted by tests/test_corpus.py"),
        ("Postgres + pgvector behaviour (no Postgres in this environment)",
         "README.md, Not verified"),
        ("response-time channel",
         "README.md, Not verified; exhibits/exhibit.md, What this does not "
         "close"),
    ):
        print(f"    {figure}\n      {where}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
