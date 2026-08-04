"""Tests for the seeded synthetic corpus.

These are not tests of the generator's taste. They are tests of the four
properties the leak demonstration rests on, each of which is a load-bearing
claim in the published report:

* the corpus is what the manifest says it is (counts, states, traps),
* every direct identifier sits at a recorded offset, so the masking baseline
  can be perfect by construction rather than by hope,
* the oracle pairs really are matched: one token in exactly one hidden
  record, one token in nothing at all,
* the committed corpus is the corpus the generator produces, so a sceptic
  who rebuilds it gets the published bytes.

The last one matters most in practice. A checked-in corpus that has drifted
from its generator is a corpus nobody can reproduce, and an irreproducible
corpus turns the whole exhibit into an assertion.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "corpus" / "data"
GENERATOR = REPO_ROOT / "corpus" / "generate.py"
DATA_FILES = ("patients.json", "documents.json", "consents.json",
              "manifest.json")

if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from consent_gate.consent import Consent, Purpose, allow  # noqa: E402

NON_CONSENTED_STATES = frozenset({"expired", "revoked", "never", "future"})
EXPECTED_STATES = frozenset({
    "active_direct_care", "active_research", "expired", "revoked", "never",
    "future",
})

MRN_RE = re.compile(r"^MRN-ZZ-\d{6}$")
PHONE_RE = re.compile(r"^\(555\) 555-01\d{2}$")
NAME_RE = re.compile(r"^[A-Z][a-z]+ [A-Z][a-z]+$")
ADDRESS_RE = re.compile(r"^\d+ [A-Za-z ]+, [A-Za-z ]+ ZZ-\d{5}$")

#: A corpus that names a real hospital, insurer or vendor is no longer
#: obviously synthetic to a reader skimming it, and "obviously synthetic" is
#: the only defence a published corpus has.
REAL_INSTITUTIONS = (
    "mayo clinic", "cleveland clinic", "johns hopkins", "kaiser",
    "massachusetts general", "mount sinai", "cedars-sinai", "nhs",
    "national health service", "apollo hospital", "fortis", "aiims",
    "medanta", "max healthcare", "manipal", "epic systems", "cerner",
    "meditech", "allscripts", "athenahealth", "unitedhealth", "aetna",
    "anthem", "humana", "cigna", "bupa", "medicare", "medicaid", "tricare",
    "pfizer", "novartis", "astrazeneca", "glaxosmithkline", "roche",
    "merck", "sanofi", "who.int", "world health organization",
)


def _load(directory: Path, name: str):
    return json.loads((directory / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def patients():
    return _load(DATA_DIR, "patients.json")


@pytest.fixture(scope="module")
def documents():
    return _load(DATA_DIR, "documents.json")


@pytest.fixture(scope="module")
def consents():
    return _load(DATA_DIR, "consents.json")


@pytest.fixture(scope="module")
def manifest():
    return _load(DATA_DIR, "manifest.json")


@pytest.fixture(scope="module")
def now(manifest):
    return datetime.fromisoformat(manifest["now"])


def _build(target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--out", str(target)],
        capture_output=True, text=True)
    assert result.returncode == 0, (
        f"generator failed: {result.stdout}\n{result.stderr}")
    return target


@pytest.fixture(scope="module")
def rebuilds(tmp_path_factory):
    """Two independent rebuilds, used for reproducibility and for drift."""
    root = tmp_path_factory.mktemp("corpus_rebuild")
    return _build(root / "a"), _build(root / "b")


def _consent_objects(records: list[dict]) -> list[Consent]:
    return [
        Consent(
            subject=record["subject"],
            purposes=frozenset(Purpose.parse(p) for p in record["purposes"]),
            valid_from=datetime.fromisoformat(record["valid_from"]),
            valid_until=(None if record["valid_until"] is None
                         else datetime.fromisoformat(record["valid_until"])),
            revoked_at=(None if record["revoked_at"] is None
                        else datetime.fromisoformat(record["revoked_at"])),
            scope=frozenset(record["scope"]),
            version=record["version"],
        )
        for record in records
    ]


@pytest.fixture(scope="module")
def consents_by_subject(consents):
    grouped: dict[str, list[Consent]] = {}
    for record, obj in zip(consents, _consent_objects(consents)):
        grouped.setdefault(record["subject"], []).append(obj)
    return grouped


# --------------------------------------------------------------------------
# Shape and counts
# --------------------------------------------------------------------------


def test_counts_match_manifest(manifest, patients, documents, consents):
    assert manifest["counts"]["patients"] == len(patients)
    assert manifest["counts"]["documents"] == len(documents)
    assert manifest["counts"]["consents"] == len(consents)
    assert manifest["manifest_version"] == "1.0"
    assert manifest["seed"] == 20260804
    assert manifest["synthetic_only"] is True


def test_corpus_is_roughly_the_planned_size(patients, documents):
    assert 190 <= len(patients) <= 210
    assert 950 <= len(documents) <= 1050


def test_ids_are_unique_and_referential(patients, documents, consents):
    patient_ids = [p["patient_id"] for p in patients]
    assert len(set(patient_ids)) == len(patient_ids)
    doc_ids = [d["doc_id"] for d in documents]
    assert len(set(doc_ids)) == len(doc_ids)
    known = set(patient_ids)
    assert {d["patient_id"] for d in documents} <= known
    assert {c["subject"] for c in consents} <= known
    # the subject is the raw id; the HMAC pseudonym is applied at load time
    assert all(p["subject"] == p["patient_id"] for p in patients)


def test_every_patient_has_at_least_one_document(patients, documents):
    with_docs = {d["patient_id"] for d in documents}
    assert with_docs == {p["patient_id"] for p in patients}


# --------------------------------------------------------------------------
# Consent records
# --------------------------------------------------------------------------


def test_every_consent_record_constructs(consents):
    objects = _consent_objects(consents)
    assert len(objects) == len(consents)
    for obj in objects:
        assert obj.purposes
        assert obj.valid_from.tzinfo is not None


def test_consent_states_are_all_populated(patients, manifest):
    counted: dict[str, int] = {}
    for patient in patients:
        counted[patient["consent_state"]] = (
            counted.get(patient["consent_state"], 0) + 1)
    assert set(counted) == EXPECTED_STATES
    assert counted == manifest["consent_states"]
    for state, count in counted.items():
        assert count >= 5, f"state {state} has only {count} patients"


def test_never_consented_patients_have_no_record(patients, consents):
    subjects = {c["subject"] for c in consents}
    for patient in patients:
        if patient["consent_state"] == "never":
            assert patient["patient_id"] not in subjects
        else:
            assert patient["patient_id"] in subjects


def test_decision_function_has_population_on_both_sides(
        patients, consents_by_subject, now):
    """Every branch the gate can take is exercised by real patients."""
    for purpose in Purpose:
        allowed = sum(
            1 for p in patients
            if allow(consents_by_subject.get(p["patient_id"], []),
                     purpose, now))
        assert 0 < allowed < len(patients), (
            f"purpose {purpose.value} is all-yes or all-no")

    for patient in patients:
        records = consents_by_subject.get(patient["patient_id"], [])
        permitted = any(
            allow(records, purpose, now) for purpose in Purpose)
        if patient["consent_state"] in NON_CONSENTED_STATES:
            assert not permitted, (
                f"{patient['patient_id']} is labelled "
                f"{patient['consent_state']} but is permitted for something")
        else:
            assert permitted, (
                f"{patient['patient_id']} is labelled active but is "
                "permitted for nothing")


def test_scope_branch_has_population(consents):
    assert any(c["scope"] for c in consents)
    assert any(not c["scope"] for c in consents)


# --------------------------------------------------------------------------
# Direct identifiers: the perfect-mask premise
# --------------------------------------------------------------------------


def test_identifier_offsets_are_exact(documents, manifest):
    total = 0
    for document in documents:
        text = document["text"]
        for identifier in document["identifiers"]:
            total += 1
            start, end = identifier["start"], identifier["end"]
            assert text[start:end] == identifier["surface"], (
                f"{document['doc_id']}: {identifier['kind']} offset is wrong")
    assert total == manifest["identifier_count"]


def test_masking_the_recorded_spans_removes_every_identifier(documents):
    """100% recall by construction, checked rather than asserted.

    If a surface occurs twice and only one occurrence is recorded, the
    "perfect" mask leaks a direct identifier and the exhibit's central
    premise is false.
    """
    for document in documents:
        text = document["text"]
        for identifier in document["identifiers"]:
            assert text.count(identifier["surface"]) == 1, (
                f"{document['doc_id']}: {identifier['surface']!r} occurs "
                "more than once but only one span is recorded")


def test_every_document_carries_all_direct_identifier_kinds(documents):
    for document in documents:
        kinds = [i["kind"] for i in document["identifiers"]]
        assert set(kinds) == {"name", "mrn", "phone", "email", "address"}
        assert len(kinds) == len(set(kinds))


def test_identifiers_are_obviously_fake(documents):
    for document in documents:
        for identifier in document["identifiers"]:
            kind, surface = identifier["kind"], identifier["surface"]
            if kind == "mrn":
                assert MRN_RE.match(surface), surface
            elif kind == "phone":
                assert PHONE_RE.match(surface), surface
            elif kind == "email":
                assert surface.endswith(".invalid"), surface
            elif kind == "name":
                assert NAME_RE.match(surface), surface
            elif kind == "address":
                assert ADDRESS_RE.match(surface), surface


def test_no_real_institution_names(documents, patients):
    haystack = "\n".join(
        f"{d['title']}\n{d['text']}" for d in documents).lower()
    for name in REAL_INSTITUTIONS:
        assert name not in haystack, f"corpus text names {name!r}"
    regions = " ".join(
        f"{p['region']} {p['condition']}" for p in patients).lower()
    for name in REAL_INSTITUTIONS:
        assert name not in regions, f"patient metadata names {name!r}"


# --------------------------------------------------------------------------
# Trap family 1: narrative PHI
# --------------------------------------------------------------------------


def test_narrative_traps_are_seeded_and_spread(manifest, documents):
    traps = manifest["narrative_traps"]
    assert len(traps) >= 40, f"only {len(traps)} narrative traps"

    states = {t["consent_state"] for t in traps}
    assert len(states) >= 4, f"traps only span {sorted(states)}"

    hidden = [t for t in traps if t["consent_state"] in NON_CONSENTED_STATES]
    assert len(hidden) >= 5, (
        f"only {len(hidden)} traps on non-consented patients; those are the "
        "ones that leak under masking")

    by_doc = {d["doc_id"]: d for d in documents}
    for trap in traps:
        document = by_doc[trap["doc_id"]]
        assert document["patient_id"] == trap["patient_id"]
        assert document["narrative_trap"] is not None
        assert document["narrative_trap"]["span"] == trap["span"]
        start, end = trap["span"]
        passage = document["text"][start:end]
        assert passage.strip(), "trap span is empty"
        # the quasi-identifier story must survive masking the identifiers,
        # which means it must not sit inside an identifier span
        for identifier in document["identifiers"]:
            assert not (start < identifier["end"]
                        and identifier["start"] < end), (
                f"{trap['doc_id']}: trap span overlaps a direct identifier")
        assert str(trap["age"]) in passage
        assert trap["occupation"] in passage
        assert trap["place"] in passage
        assert trap["nickname"] in passage

    flagged = {d["doc_id"] for d in documents
               if d["narrative_trap"] is not None}
    assert flagged == {t["doc_id"] for t in traps}


def test_nicknames_recur_across_notes(manifest):
    by_patient: dict[str, set[str]] = {}
    for trap in manifest["narrative_traps"]:
        by_patient.setdefault(trap["patient_id"], set()).add(trap["nickname"])
    counts: dict[str, int] = {}
    for trap in manifest["narrative_traps"]:
        counts[trap["patient_id"]] = counts.get(trap["patient_id"], 0) + 1
    for patient_id, nicknames in by_patient.items():
        assert len(nicknames) == 1, (
            f"{patient_id} has more than one nickname, which weakens the "
            "cross-note linkage trap")
        assert counts[patient_id] >= 2, (
            f"{patient_id}'s nickname appears in only one note")


# --------------------------------------------------------------------------
# Trap family 2: small cells
# --------------------------------------------------------------------------


def test_small_cells_are_seeded(manifest, patients, consents_by_subject, now):
    cells = manifest["small_cells"]
    assert len(cells) >= 15, f"only {len(cells)} small-cell strata"

    by_id = {p["patient_id"]: p for p in patients}
    strata: dict[str, list[str]] = {}
    for patient in patients:
        key = f"cond={patient['condition']}|region={patient['region']}"
        strata.setdefault(key, []).append(patient["patient_id"])

    with_hidden = 0
    for cell in cells:
        assert 1 <= cell["size"] <= 3, cell["stratum"]
        assert cell["size"] == len(cell["members"])
        assert sorted(strata[cell["stratum"]]) == sorted(cell["members"]), (
            f"{cell['stratum']} membership disagrees with patients.json")
        for purpose in ("direct_care", "research"):
            expected = sorted(
                m for m in cell["members"]
                if not allow(consents_by_subject.get(m, []),
                             Purpose(purpose), now))
            assert sorted(cell["non_consented"][purpose]) == expected, (
                f"{cell['stratum']}: manifest disagrees with the decision "
                f"function for {purpose}")
        if any(cell["non_consented"][p] for p in ("direct_care", "research")):
            with_hidden += 1
        for member in cell["members"]:
            assert member in by_id

    assert with_hidden >= 10, (
        f"only {with_hidden} small cells contain a non-consented member")


def test_manifest_lists_every_small_stratum(manifest, patients):
    strata: dict[str, int] = {}
    for patient in patients:
        key = f"cond={patient['condition']}|region={patient['region']}"
        strata[key] = strata.get(key, 0) + 1
    small = {k for k, v in strata.items() if 1 <= v <= 3}
    assert small == {c["stratum"] for c in manifest["small_cells"]}


# --------------------------------------------------------------------------
# Trap family 3: the search oracle
# --------------------------------------------------------------------------


def test_oracle_pairs_are_matched(manifest, documents, patients,
                                  consents_by_subject, now):
    pairs = manifest["oracle_pairs"]
    assert len(pairs) >= 25, f"only {len(pairs)} oracle pairs"

    states = {p["patient_id"]: p["consent_state"] for p in patients}
    raw = (DATA_DIR / "documents.json").read_text(encoding="utf-8")

    present_tokens = [p["present_token"] for p in pairs]
    absent_tokens = [p["absent_token"] for p in pairs]
    assert len(set(present_tokens)) == len(present_tokens)
    assert len(set(absent_tokens)) == len(absent_tokens)
    assert not set(present_tokens) & set(absent_tokens)

    for pair in pairs:
        present = pair["present_token"]
        hits = [d for d in documents if present in d["text"]]
        assert [d["doc_id"] for d in hits] == [pair["doc_id"]], (
            f"{present} should occur in exactly {pair['doc_id']}")

        host = hits[0]["patient_id"]
        assert host == pair["patient_id"]
        assert states[host] in NON_CONSENTED_STATES, (
            f"{present} sits on a {states[host]} patient; an oracle token on "
            "a consented record proves nothing")
        assert not any(
            allow(consents_by_subject.get(host, []), purpose, now)
            for purpose in Purpose)
        assert present in hits[0]["tokens"]
        assert states[host] == pair["consent_state"]

        # the control token must not exist anywhere in the corpus file, not
        # merely be absent from the narrative text
        assert pair["absent_token"] not in raw, (
            f"control token {pair['absent_token']} exists in the corpus")

        if pair.get("combo_token"):
            combo_hits = [d["doc_id"] for d in documents
                          if pair["combo_token"] in d["text"]]
            assert combo_hits == [pair["doc_id"]]


def test_declared_tokens_match_the_text(documents):
    for document in documents:
        for token in document["tokens"]:
            assert token in document["text"]
        if not document["tokens"]:
            assert "ZZORACLE-" not in document["text"]
            assert "ZZCOMBO-" not in document["text"]


# --------------------------------------------------------------------------
# Controls: exclusion output must be non-empty and useful
# --------------------------------------------------------------------------


def test_control_records_exist(documents, consents_by_subject, now):
    controls = [
        d for d in documents
        if allow(consents_by_subject.get(d["patient_id"], []),
                 Purpose.DIRECT_CARE, now)
    ]
    assert len(controls) >= 100, (
        f"only {len(controls)} documents survive exclusion for direct_care; "
        "a demo that returns nothing proves the wrong thing")
    plain = [d for d in controls if d["narrative_trap"] is None
             and not d["tokens"]]
    assert len(plain) >= 100, "too few unremarkable consented documents"


def test_hidden_documents_exist(documents, patients):
    states = {p["patient_id"]: p["consent_state"] for p in patients}
    hidden = [d for d in documents
              if states[d["patient_id"]] in NON_CONSENTED_STATES]
    assert len(hidden) >= 100, (
        f"only {len(hidden)} documents belong to non-consented patients")


# --------------------------------------------------------------------------
# Reproducibility
# --------------------------------------------------------------------------


def test_generator_sha256_matches(manifest):
    digest = hashlib.sha256(GENERATOR.read_bytes()).hexdigest()
    assert manifest["generator_sha256"] == digest, (
        "the manifest was written by a different generate.py; rebuild the "
        "corpus")


def test_two_builds_are_byte_identical(rebuilds):
    first, second = rebuilds
    for name in DATA_FILES:
        assert (first / name).read_bytes() == (second / name).read_bytes(), (
            f"{name} differs between two runs of the same seed")


def test_committed_corpus_matches_a_fresh_build(rebuilds):
    first, _ = rebuilds
    for name in DATA_FILES:
        assert (DATA_DIR / name).read_bytes() == (first / name).read_bytes(), (
            f"corpus/data/{name} is stale; run python corpus/generate.py")
