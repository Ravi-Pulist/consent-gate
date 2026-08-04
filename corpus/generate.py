#!/usr/bin/env python3
"""Seeded synthetic corpus for the consent-aware retrieval layer.

Everything here is invented. No real person, place, institution or record
shape from a live system is used, and none can be: the generator has no
inputs other than a constant seed.

Three properties this file exists to guarantee, because the leak demo is
worthless without them:

1. **Determinism.** One seed, one fixed ``NOW``, no wall clock, no filesystem
   ordering, no set iteration. Two runs are byte-identical, so a sceptic can
   rebuild the corpus and diff it against the published one.

2. **A perfect masking baseline, by construction.** Every direct identifier
   in every document is emitted at a recorded character offset, so the
   masking configuration can redact all of them with 100% recall. That
   forestalls the only interesting counter-argument to the exhibit ("your
   masker was just bad"): the leaks the demo shows are leaks masking cannot
   fix even in principle, not leaks a better NER model would catch.

3. **Traps with known ground truth.** The three leak vectors are seeded
   deliberately and enumerated in the manifest, so probe results can be
   checked against what was planted rather than against a human reading the
   output.

Consent states are distributed so that every branch of the decision function
has population on both sides: active, expired, revoked, future-dated and
never-consented, times purpose, times scope.

Usage::

    python corpus/generate.py [--out corpus/data]

Note on phone numbers: the reserved fictional block 555-0100..555-0199 holds
exactly 100 numbers and this corpus has 200 patients, so numbers repeat by
design. Using a wider range would mean minting numbers that could belong to
someone. Repetition inside a fictional block is the safer defect.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random

# --------------------------------------------------------------------------
# Constants. Nothing below reads the wall clock or the environment.
# --------------------------------------------------------------------------

SEED = 20260804
NOW = datetime(2026, 8, 4, 0, 0, 0, tzinfo=timezone.utc)
MANIFEST_VERSION = "1.0"

N_PATIENTS = 200
N_DOCUMENTS = 1000

#: patients per consent state. Shares from the data plan, rounded to whole
#: patients: 40 / 15 / 10 / 15 / 15 / 5 percent.
STATE_PLAN = (
    ("active_direct_care", 80),
    ("active_research", 30),
    ("expired", 20),
    ("revoked", 30),
    ("never", 30),
    ("future", 10),
)

#: states with no active consent for any purpose at NOW. These are the
#: patients whose records must be invisible, and therefore the patients the
#: traps are planted on.
NON_CONSENTED_STATES = ("expired", "revoked", "never", "future")

PURPOSES = ("direct_care", "research", "outreach", "quality_improvement")

DOC_TYPES = (
    ("progress_note", "Progress note"),
    ("discharge_summary", "Discharge summary"),
    ("referral_letter", "Referral letter"),
    ("nursing_note", "Nursing note"),
    ("lab_report", "Laboratory report"),
    ("imaging_report", "Imaging report"),
)

# --------------------------------------------------------------------------
# Invented vocabulary. Surnames, settlements and conditions are all coinages;
# no institution of any kind is named anywhere in the corpus text.
# --------------------------------------------------------------------------

GIVEN_NAMES = (
    "Marda", "Tomsen", "Ilva", "Brannoc", "Esker", "Wynn", "Solveigh",
    "Petrik", "Ardis", "Corven", "Nessa", "Halvard", "Ottoline", "Ferren",
    "Maribeth", "Jorven", "Elspeth", "Calder", "Verity", "Oskar", "Linnet",
    "Rowan", "Sable", "Thaddeus",
)

SURNAMES = (
    "Quillfeather", "Marrowby", "Ashgate", "Penhollow", "Bracklewaite",
    "Dunmire", "Vellacott", "Thistlewood", "Oxenhale", "Corbray",
    "Fennimore", "Halstrode", "Merrowfield", "Nettlebeck", "Orrick",
    "Pallister", "Rookwood", "Selvidge", "Tarnbury", "Undercroft",
    "Vosswick", "Wrenlow", "Yarborn", "Zellick", "Brimmond", "Callowdon",
    "Dravenhill", "Edderly", "Falkmoor", "Garrowen",
)

EMAIL_DOMAINS = (
    "synthetic-care.invalid",
    "example-practice.invalid",
    "notreal-health.invalid",
    "zz-records.invalid",
)

STREET_NAMES = (
    "Thistlewick", "Harrowgate", "Kestrel", "Millrace", "Coble", "Netherby",
    "Saltgate", "Windlass", "Ferrymans", "Beacon", "Draper", "Quarryman",
    "Ropewalk", "Tanner", "Wyndham",
)

STREET_KINDS = ("Row", "Lane", "Walk", "Close", "Terrace", "Rise", "Steps",
                "Yard")

COMMON_REGIONS = ("Aldermoor", "Bramleigh", "Cinderfell", "Dunhollow")

COMMON_CONDITIONS = (
    "Harrowfield anaemia",
    "Pell's arthropathy",
    "Sable-tract colitis",
    "Vennell reflux",
    "Ondry thyroiditis",
)

RARE_REGIONS = (
    "Kirkhollow", "Eastmere", "Thornwick", "Ossary Point", "Vellum Bay",
    "Draymoor", "Halloway Sands", "Netherby Cross", "Quillmoor",
    "Redwyn Isle",
)

RARE_CONDITIONS = (
    "Kessler syndrome",
    "Marran-Doyle deficiency",
    "Vosk anomaly",
    "Pell-Rutter dystrophy",
    "Ashcombe myopathy",
    "Tarrow's granulomatosis",
    "Ilvane porphyria",
    "Brackwater neuropathy",
    "Cindral ataxia",
    "Merrow-Kane syndrome",
    "Halbrook enteropathy",
    "Ondry-Vale amyloidosis",
    "Sable ridge dysplasia",
    "Wrenfield cardiomyopathy",
    "Quillane haemolysis",
    "Tessary duct atresia",
    "Norrow band keratopathy",
    "Delphin-Marsh syndrome",
    "Ambrey nephritis",
    "Corvane immunodeficiency",
)

#: (condition, region) pairs, one per rare stratum. Conditions are unique per
#: stratum, so stratum keys never collide with the common strata.
RARE_STRATA = tuple(
    (cond, RARE_REGIONS[i % len(RARE_REGIONS)])
    for i, cond in enumerate(RARE_CONDITIONS)
)

#: how many rare strata are guaranteed to contain a non-consented member. The
#: remainder are fully consented, so a probe cannot pass by flagging every
#: small cell it sees.
MIXED_RARE_STRATA = 15

NICKNAMES = (
    "Sparrow", "Pike", "Tinder", "Marrow", "Coble", "Quill", "Bramble",
    "Whistler", "Rook", "Kettle", "Thimble", "Ember", "Larkspur", "Gable",
    "Nettle", "Halyard", "Pennant", "Cinder", "Fathom", "Beacon", "Hollow",
    "Wick", "Tally", "Salter", "Bellows", "Drift", "Gorse", "Latch",
    "Mudlark", "Pitch",
)

OCCUPATIONS = (
    "lighthouse keeper", "bell founder", "kelp harvester", "peat cutter",
    "harbour pilot", "organ tuner", "glassblower", "farrier", "cartwright",
    "lock keeper", "thatcher", "puppet maker", "clock restorer", "beekeeper",
    "sailmaker", "cooper", "stonemason", "falconer", "ferryman",
    "wheelwright", "bookbinder", "chandler", "fletcher", "millwright",
    "net mender", "salt panner", "reed cutter", "wherry skipper",
    "basket weaver", "tide bell ringer",
)

#: Each template weaves age, occupation, place and nickname into ordinary
#: prose. Redacting the header identifiers leaves every one of these intact:
#: that is the narrative leak, stated as a sentence.
TRAP_TEMPLATES = (
    "The patient, a {age}-year-old {occupation} from {place}, known to the "
    "team as {nickname}, returned for review.",
    "{nickname}, as the ward has called them since childhood, is {age} and "
    "still working as a {occupation} on the {place} shorefront.",
    "At {age} the patient remains the only {occupation} still practising in "
    "{place}, and handover continues to use the name {nickname}.",
    "Handover refers to the patient as {nickname}: a {occupation}, {age} "
    "years old, resident in {place} since the flood year.",
    "The {age}-year-old {occupation} who keeps the {place} tide bell, called "
    "{nickname} by the district nurses, attended unaccompanied.",
    "Notes from the community team describe {nickname}, {age}, the "
    "{occupation} whose workshop sits above the {place} slipway.",
)

QUASI_IDENTIFIERS = ("age", "occupation", "place", "nickname")

LEAD_SENTENCES = (
    "The care team reviewed the current management of {condition}.",
    "This contact was a scheduled review of {condition}.",
    "Attendance was for routine follow-up of {condition}.",
    "The encounter covered ongoing management of {condition} and nothing "
    "further.",
    "Review of {condition} was carried out in the usual clinic slot.",
)

FILLER_SENTENCES = (
    "Observations were stable through the visit, with no change to the "
    "{condition} regimen.",
    "Bloods were repeated and the trend since the last review is unchanged.",
    "The patient reports good adherence and no new symptoms since the "
    "previous contact.",
    "Weight and blood pressure were recorded and filed with the encounter.",
    "A follow-up appointment was offered in the routine slot.",
    "No new allergies were declared at this contact.",
    "The district team confirmed the home dressing supply is adequate.",
    "Sleep and appetite were described as ordinary.",
    "A repeat prescription for the {condition} maintenance therapy was "
    "issued.",
    "The physiotherapy referral remains open with no change in priority.",
    "Transport support was offered and declined for the next appointment.",
    "The care plan was read back and agreed without amendment.",
    "Fasting bloods are scheduled ahead of the next review.",
    "No safeguarding concerns were identified at this contact.",
    "The seasonal vaccination offer was declined at this visit.",
    "Home readings brought to the appointment were within the agreed range.",
    "The {region} community team will continue the fortnightly visits.",
    "Interpreter support was not required for this encounter.",
)

CLOSING_SENTENCES = (
    "Plan: continue current management and review in twelve weeks.",
    "Plan: no change, routine recall.",
    "Plan: repeat bloods before the next appointment, otherwise unchanged.",
    "Plan: continue as before; the patient knows how to contact the team.",
    "Plan: review at the next scheduled clinic.",
)

ORACLE_SENTENCE = (
    "Trial enrolment reference {token} was filed against this encounter."
)

ORACLE_COMBO_SENTENCE = (
    "Trial enrolment reference {token} was filed against this encounter, "
    "cross-indexed to specimen lot {combo}."
)

TOKEN_ALPHABET = "abcdefghijklmnopqrstuvwxyz"

N_ORACLE_PAIRS = 30
N_ORACLE_COMBOS = 10
TRAP_PATIENTS_PER_STATE = 4


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


class CorpusError(RuntimeError):
    """A self-check failed. The corpus is not written when this is raised."""


def _check(condition: bool, message: str) -> None:
    """Assert that survives ``python -O``.

    The self-checks below are the only thing standing between a subtly broken
    corpus and a demo that proves the wrong thing, so they must not be
    optimised away.
    """
    if not condition:
        raise CorpusError(message)


def _iso(when: datetime | None) -> str | None:
    return None if when is None else when.isoformat()


def _days(n: int) -> timedelta:
    return timedelta(days=n)


def _parse(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _allow(records: list[dict], purpose: str, when: datetime) -> bool:
    """Local mirror of ``consent_gate.consent.allow`` with ``scope=None``.

    Deliberately duplicated rather than imported: the generator must not
    break when the library it feeds is mid-edit, and the test suite asserts
    the two agree, which is stronger than sharing an implementation.
    """
    for record in records:
        if purpose not in record["purposes"]:
            continue
        revoked_at = _parse(record["revoked_at"])
        if revoked_at is not None and when >= revoked_at:
            continue
        if when < _parse(record["valid_from"]):
            continue
        valid_until = _parse(record["valid_until"])
        if valid_until is not None and when >= valid_until:
            continue
        return True
    return False


class TextBuilder:
    """Accumulates document text while recording identifier offsets.

    Offsets are taken as the text is built rather than searched for
    afterwards, because a search finds the first occurrence and the first
    occurrence is not always the one you meant.
    """

    __slots__ = ("_parts", "_len", "identifiers")

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._len = 0
        self.identifiers: list[dict] = []

    @property
    def offset(self) -> int:
        return self._len

    def add(self, text: str) -> None:
        if text:
            self._parts.append(text)
            self._len += len(text)

    def add_identifier(self, kind: str, surface: str) -> None:
        start = self._len
        self.add(surface)
        self.identifiers.append({
            "kind": kind,
            "surface": surface,
            "start": start,
            "end": self._len,
        })

    def text(self) -> str:
        return "".join(self._parts)


# --------------------------------------------------------------------------
# Patients, strata, identities
# --------------------------------------------------------------------------


def build_patients(rng: Random) -> list[dict]:
    states: list[str] = []
    for name, count in STATE_PLAN:
        states.extend([name] * count)
    _check(len(states) == N_PATIENTS,
           f"state plan sums to {len(states)}, expected {N_PATIENTS}")
    rng.shuffle(states)
    return [
        {
            "patient_id": f"P{i:03d}",
            # left raw on purpose: the HMAC pseudonym is applied at load
            # time, so no linkage table is ever written to disk here.
            "subject": f"P{i:03d}",
            "consent_state": state,
            "region": None,
            "condition": None,
        }
        for i, state in enumerate(states, start=1)
    ]


def assign_strata(rng: Random, patients: list[dict]) -> None:
    """Place patients into condition x region strata.

    Rare strata are sized 1-3 and seeded with a deliberate consent mix, so an
    aggregate over any of them either respects consent or discloses somebody.
    Common strata are sized 8 or 9, well clear of small-cell territory, so
    the small cells in the manifest are exactly the ones that were planted.
    """
    by_id = {p["patient_id"]: p for p in patients}
    non_consented = [p["patient_id"] for p in patients
                     if p["consent_state"] in NON_CONSENTED_STATES]
    consented = [p["patient_id"] for p in patients
                 if p["consent_state"] not in NON_CONSENTED_STATES]
    rng.shuffle(non_consented)
    rng.shuffle(consented)

    nc_i = 0
    c_i = 0
    for index, (condition, region) in enumerate(RARE_STRATA):
        size = (1, 2, 3)[index % 3]
        members: list[str] = []
        if index < MIXED_RARE_STRATA:
            members.append(non_consented[nc_i])
            nc_i += 1
        while len(members) < size:
            members.append(consented[c_i])
            c_i += 1
        for patient_id in members:
            by_id[patient_id]["condition"] = condition
            by_id[patient_id]["region"] = region

    remaining = [p for p in patients if p["condition"] is None]
    for k, patient in enumerate(remaining):
        patient["condition"] = COMMON_CONDITIONS[k % len(COMMON_CONDITIONS)]
        patient["region"] = COMMON_REGIONS[
            (k // len(COMMON_CONDITIONS)) % len(COMMON_REGIONS)]


def assign_identities(rng: Random, patients: list[dict]) -> dict[str, dict]:
    """One stable fake identity per patient, reused across their documents."""
    combos = [(given, surname)
              for surname in SURNAMES for given in GIVEN_NAMES]
    chosen = rng.sample(combos, N_PATIENTS)
    mrn_numbers = rng.sample(range(100000, 1000000), N_PATIENTS)

    identities: dict[str, dict] = {}
    for index, patient in enumerate(patients):
        given, surname = chosen[index]
        street = rng.choice(STREET_NAMES)
        kind = rng.choice(STREET_KINDS)
        number = rng.randrange(1, 400)
        postcode = rng.randrange(10000, 99999)
        identities[patient["patient_id"]] = {
            "name": f"{given} {surname}",
            "mrn": f"MRN-ZZ-{mrn_numbers[index]:06d}",
            # 555-0100..555-0199 is the reserved fictional block; see module
            # docstring on why numbers repeat.
            "phone": f"(555) 555-01{index % 100:02d}",
            "email": (f"{given.lower()}.{surname.lower()}"
                      f"@{EMAIL_DOMAINS[index % len(EMAIL_DOMAINS)]}"),
            "address": (f"{number} {street} {kind}, {patient['region']} "
                        f"ZZ-{postcode:05d}"),
            "age": rng.randrange(24, 89),
        }
    return identities


# --------------------------------------------------------------------------
# Consent records
# --------------------------------------------------------------------------


def build_consents(rng: Random, patients: list[dict]) -> list[dict]:
    """One record per consented patient; never-consented patients get none.

    Absence of a record is the point: deny by default means an empty list is
    a denial, so "never consented" must be modelled as nothing at all rather
    than as a record that says no.
    """
    consents: list[dict] = []
    for patient in patients:
        state = patient["consent_state"]
        if state == "never":
            continue

        valid_until: datetime | None = None
        revoked_at: datetime | None = None
        version = 1

        if state == "active_direct_care":
            purposes = ["direct_care"]
            if rng.randrange(4) == 0:
                purposes.append("quality_improvement")
            valid_from = NOW - _days(rng.randrange(180, 1200))
            if rng.randrange(3) == 0:
                valid_until = NOW + _days(rng.randrange(90, 900))
        elif state == "active_research":
            purposes = ["research"]
            if rng.randrange(3) == 0:
                purposes.append("outreach")
            valid_from = NOW - _days(rng.randrange(180, 1200))
            if rng.randrange(3) == 0:
                valid_until = NOW + _days(rng.randrange(90, 900))
        elif state == "expired":
            purposes = rng.choice(
                (["direct_care"], ["research"], ["direct_care", "research"]))
            valid_from = NOW - _days(rng.randrange(700, 1400))
            valid_until = NOW - _days(rng.randrange(15, 300))
        elif state == "revoked":
            purposes = rng.choice(
                (["direct_care"], ["research"], ["direct_care", "research"]))
            valid_from = NOW - _days(rng.randrange(500, 1200))
            if rng.randrange(3) == 0:
                valid_until = NOW + _days(rng.randrange(100, 800))
            revoked_at = NOW - _days(rng.randrange(2, 400))
            # a revocation is a new record and bumps the version, which is
            # what makes cached embeddings and result sets unreadable
            version = 2
        elif state == "future":
            purposes = rng.choice(
                (["direct_care"], ["research"], ["direct_care", "research"]))
            valid_from = NOW + _days(rng.randrange(10, 240))
            if rng.randrange(2) == 0:
                valid_until = valid_from + _days(rng.randrange(180, 900))
        else:  # pragma: no cover - guarded by the state plan
            raise CorpusError(f"unknown consent state {state!r}")

        scope: list[str] = []
        if rng.randrange(5) == 0:
            scope = rng.choice(
                (["progress_note"],
                 ["progress_note", "discharge_summary"],
                 ["lab_report"]))

        consents.append({
            "subject": patient["patient_id"],
            "purposes": purposes,
            "valid_from": _iso(valid_from),
            "valid_until": _iso(valid_until),
            "revoked_at": _iso(revoked_at),
            "scope": scope,
            "version": version,
        })

    for record in consents:
        _check(bool(record["purposes"]),
               f"consent for {record['subject']} names no purpose")
        valid_until = _parse(record["valid_until"])
        if valid_until is not None:
            _check(valid_until > _parse(record["valid_from"]),
                   f"consent for {record['subject']} expires before it begins")
    return consents


# --------------------------------------------------------------------------
# Document plan: counts, narrative traps, oracle traps
# --------------------------------------------------------------------------


def plan_document_counts(rng: Random, patients: list[dict],
                         trap_patients: list[str]) -> dict[str, int]:
    counts = {p["patient_id"]: rng.choice((3, 4, 5, 6, 7)) for p in patients}
    # trap patients need room for two or three seeded notes plus ordinary ones
    for patient_id in trap_patients:
        counts[patient_id] = max(counts[patient_id], 4)

    order = [p["patient_id"] for p in patients]
    trap_set = set(trap_patients)
    index = 0
    guard = 0
    while sum(counts.values()) != N_DOCUMENTS:
        guard += 1
        _check(guard < 100000, "document count rebalancing did not converge")
        patient_id = order[index % len(order)]
        index += 1
        total = sum(counts.values())
        if total > N_DOCUMENTS:
            floor = 4 if patient_id in trap_set else 3
            if counts[patient_id] > floor:
                counts[patient_id] -= 1
        else:
            if counts[patient_id] < 9:
                counts[patient_id] += 1
    return counts


def select_trap_patients(rng: Random, patients: list[dict]) -> list[str]:
    """Four narrative-trap patients per consent state.

    Spanning every state matters: a probe that only ever sees traps on
    non-consented patients cannot tell exclusion from a broken retriever.
    """
    selected: list[str] = []
    for state, _ in STATE_PLAN:
        candidates = sorted(p["patient_id"] for p in patients
                            if p["consent_state"] == state)
        _check(len(candidates) >= TRAP_PATIENTS_PER_STATE,
               f"state {state} has too few patients for narrative traps")
        selected.extend(rng.sample(candidates, TRAP_PATIENTS_PER_STATE))
    return selected


def make_token_suffixes(rng: Random, how_many: int) -> list[str]:
    """Distinct four-letter suffixes, so no token can collide with another."""
    seen: dict[str, None] = {}
    guard = 0
    while len(seen) < how_many:
        guard += 1
        _check(guard < 100000, "could not mint enough distinct token suffixes")
        suffix = "".join(rng.choice(TOKEN_ALPHABET) for _ in range(4))
        seen.setdefault(suffix, None)
    return list(seen)


def plan_oracle_traps(rng: Random, patients: list[dict],
                      counts: dict[str, int]) -> list[dict]:
    """Matched pairs: a token in exactly one hidden record, and a token in none.

    The control token is the whole point. Without it a probe can only observe
    "query returned nothing", which proves nothing; with it the probe can
    compare a hidden record against a record that does not exist and demand
    the two responses be identical.
    """
    non_consented = sorted(p["patient_id"] for p in patients
                           if p["consent_state"] in NON_CONSENTED_STATES)
    _check(len(non_consented) >= N_ORACLE_PAIRS,
           "not enough non-consented patients to host the oracle tokens")
    hosts = rng.sample(non_consented, N_ORACLE_PAIRS)
    suffixes = make_token_suffixes(rng, N_ORACLE_PAIRS * 2 + N_ORACLE_COMBOS)

    plans: list[dict] = []
    for index, patient_id in enumerate(hosts):
        present = f"ZZORACLE-{index + 1:03d}-{suffixes[index]}"
        absent = f"ZZORACLE-{index + 1:03d}-{suffixes[N_ORACLE_PAIRS + index]}"
        combo = None
        if index < N_ORACLE_COMBOS:
            combo = (f"ZZCOMBO-{index + 1:03d}-"
                     f"{suffixes[N_ORACLE_PAIRS * 2 + index]}")
        plans.append({
            "patient_id": patient_id,
            "doc_slot": rng.randrange(counts[patient_id]),
            "present_token": present,
            "absent_token": absent,
            "combo_token": combo,
        })
    return plans


# --------------------------------------------------------------------------
# Document rendering
# --------------------------------------------------------------------------


def render_document(rng: Random, patient: dict, identity: dict,
                    doc_id: str, doc_type: str, label: str,
                    encounter: str, trap: dict | None,
                    oracle: dict | None) -> dict:
    condition = patient["condition"]
    region = patient["region"]
    builder = TextBuilder()

    builder.add(f"{label}\n")
    builder.add("Patient: ")
    builder.add_identifier("name", identity["name"])
    builder.add("\nMRN: ")
    builder.add_identifier("mrn", identity["mrn"])
    builder.add("\nPhone: ")
    builder.add_identifier("phone", identity["phone"])
    builder.add("\nEmail: ")
    builder.add_identifier("email", identity["email"])
    builder.add("\nAddress: ")
    builder.add_identifier("address", identity["address"])
    builder.add(f"\nEncounter date: {encounter}\n")
    builder.add(f"Record type: {doc_type}\n\n")

    builder.add(rng.choice(LEAD_SENTENCES).format(condition=condition) + " ")

    narrative_trap = None
    if trap is not None:
        template = TRAP_TEMPLATES[trap["template"]]
        sentence = template.format(age=trap["age"],
                                   occupation=trap["occupation"],
                                   place=region,
                                   nickname=trap["nickname"])
        start = builder.offset
        builder.add(sentence)
        narrative_trap = {
            "span": [start, builder.offset],
            "quasi_identifiers": list(QUASI_IDENTIFIERS),
        }
        builder.add(" ")

    tokens: list[str] = []
    if oracle is not None:
        tokens.append(oracle["present_token"])
        if oracle["combo_token"] is not None:
            tokens.append(oracle["combo_token"])
            builder.add(ORACLE_COMBO_SENTENCE.format(
                token=oracle["present_token"],
                combo=oracle["combo_token"]) + " ")
        else:
            builder.add(ORACLE_SENTENCE.format(
                token=oracle["present_token"]) + " ")

    for template in rng.sample(FILLER_SENTENCES, rng.randrange(2, 5)):
        builder.add(template.format(condition=condition, region=region) + " ")
    builder.add(rng.choice(CLOSING_SENTENCES) + "\n")

    text = builder.text()
    for identifier in builder.identifiers:
        surface = identifier["surface"]
        _check(text[identifier["start"]:identifier["end"]] == surface,
               f"{doc_id}: recorded offset does not match the text")
        _check(text.count(surface) == 1,
               f"{doc_id}: identifier {surface!r} appears more than once, so "
               "masking its recorded span would not remove it")

    return {
        "doc_id": doc_id,
        "patient_id": patient["patient_id"],
        "doc_type": doc_type,
        "title": f"{label} - {condition} - {encounter}",
        "text": text,
        "identifiers": builder.identifiers,
        "tokens": tokens,
        "narrative_trap": narrative_trap,
    }


def build_documents(rng: Random, patients: list[dict],
                    identities: dict[str, dict]) -> tuple[list[dict],
                                                          list[dict],
                                                          list[dict]]:
    trap_patients = select_trap_patients(rng, patients)
    counts = plan_document_counts(rng, patients, trap_patients)
    oracle_plans = plan_oracle_traps(rng, patients, counts)

    nicknames = rng.sample(NICKNAMES, len(trap_patients))
    occupations = rng.sample(OCCUPATIONS, len(trap_patients))

    trap_profiles: dict[str, dict] = {}
    for index, patient_id in enumerate(trap_patients):
        # twelve patients carry three seeded notes and twelve carry two, so
        # the nickname recurs across notes for every one of them
        n_traps = 3 if index % 2 == 0 else 2
        trap_profiles[patient_id] = {
            "nickname": nicknames[index],
            "occupation": occupations[index],
            "age": identities[patient_id]["age"],
            "slots": sorted(rng.sample(range(counts[patient_id]), n_traps)),
            "template": index % len(TRAP_TEMPLATES),
        }

    oracle_by_patient = {plan["patient_id"]: plan for plan in oracle_plans}

    documents: list[dict] = []
    trap_records: list[dict] = []
    oracle_records: list[dict] = []
    doc_number = 0

    for patient in patients:
        patient_id = patient["patient_id"]
        identity = identities[patient_id]
        count = counts[patient_id]
        days_back = sorted(rng.sample(range(3, 1000), count), reverse=True)
        profile = trap_profiles.get(patient_id)
        oracle_plan = oracle_by_patient.get(patient_id)

        for slot in range(count):
            doc_number += 1
            doc_id = f"D{doc_number:04d}"
            doc_type, label = DOC_TYPES[rng.randrange(len(DOC_TYPES))]
            encounter = (NOW - _days(days_back[slot])).date().isoformat()

            trap = None
            if profile is not None and slot in profile["slots"]:
                trap = profile

            oracle = None
            if oracle_plan is not None and slot == oracle_plan["doc_slot"]:
                oracle = oracle_plan

            document = render_document(rng, patient, identity, doc_id,
                                       doc_type, label, encounter, trap,
                                       oracle)
            documents.append(document)

            if document["narrative_trap"] is not None:
                trap_records.append({
                    "doc_id": doc_id,
                    "patient_id": patient_id,
                    "span": document["narrative_trap"]["span"],
                    "consent_state": patient["consent_state"],
                    "quasi_identifiers": list(QUASI_IDENTIFIERS),
                    "nickname": profile["nickname"],
                    "occupation": profile["occupation"],
                    "place": patient["region"],
                    "age": profile["age"],
                })

            if oracle is not None:
                oracle_records.append({
                    "present_token": oracle["present_token"],
                    "absent_token": oracle["absent_token"],
                    "combo_token": oracle["combo_token"],
                    "doc_id": doc_id,
                    "patient_id": patient_id,
                    "consent_state": patient["consent_state"],
                })

    _check(len(documents) == N_DOCUMENTS,
           f"generated {len(documents)} documents, expected {N_DOCUMENTS}")
    _check(len(oracle_records) == N_ORACLE_PAIRS,
           f"placed {len(oracle_records)} oracle tokens, "
           f"expected {N_ORACLE_PAIRS}")
    return documents, trap_records, oracle_records


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------


def build_small_cells(patients: list[dict],
                      consents: list[dict]) -> list[dict]:
    by_subject: dict[str, list[dict]] = {}
    for record in consents:
        by_subject.setdefault(record["subject"], []).append(record)

    strata: dict[str, list[str]] = {}
    for patient in patients:
        key = f"cond={patient['condition']}|region={patient['region']}"
        strata.setdefault(key, []).append(patient["patient_id"])

    cells: list[dict] = []
    for key in sorted(strata):
        members = sorted(strata[key])
        if not 1 <= len(members) <= 3:
            continue
        non_consented = {
            purpose: [m for m in members
                      if not _allow(by_subject.get(m, []), purpose, NOW)]
            for purpose in PURPOSES
        }
        condition, region = key.split("|", 1)
        cells.append({
            "stratum": key,
            "condition": condition[len("cond="):],
            "region": region[len("region="):],
            "size": len(members),
            "members": members,
            "non_consented": non_consented,
        })
    return cells


def build_manifest(patients: list[dict], documents: list[dict],
                   consents: list[dict], trap_records: list[dict],
                   small_cells: list[dict], oracle_records: list[dict],
                   generator_sha256: str) -> dict:
    state_counts: dict[str, int] = {name: 0 for name, _ in STATE_PLAN}
    for patient in patients:
        state_counts[patient["consent_state"]] += 1

    identifier_count = sum(len(d["identifiers"]) for d in documents)

    return {
        "manifest_version": MANIFEST_VERSION,
        "seed": SEED,
        "now": _iso(NOW),
        "synthetic_only": True,
        "generator_sha256": generator_sha256,
        "counts": {
            "patients": len(patients),
            "documents": len(documents),
            "consents": len(consents),
        },
        "consent_states": state_counts,
        "narrative_traps": trap_records,
        "small_cells": small_cells,
        "oracle_pairs": oracle_records,
        "identifier_count": identifier_count,
    }


# --------------------------------------------------------------------------
# Corpus-wide self-checks
# --------------------------------------------------------------------------


def verify(patients: list[dict], documents: list[dict], consents: list[dict],
           manifest: dict) -> None:
    by_id = {p["patient_id"]: p for p in patients}
    by_subject: dict[str, list[dict]] = {}
    for record in consents:
        by_subject.setdefault(record["subject"], []).append(record)

    haystack = "\n".join(f"{d['title']}\n{d['text']}" for d in documents)

    for pair in manifest["oracle_pairs"]:
        present = pair["present_token"]
        hits = [d["doc_id"] for d in documents if present in d["text"]]
        _check(hits == [pair["doc_id"]],
               f"{present} appears in {hits}, expected only {pair['doc_id']}")
        state = by_id[pair["patient_id"]]["consent_state"]
        _check(state in NON_CONSENTED_STATES,
               f"{present} sits on a consented patient ({state})")
        _check(pair["absent_token"] not in haystack,
               f"control token {pair['absent_token']} exists in the corpus")
        if pair["combo_token"] is not None:
            combo_hits = [d["doc_id"] for d in documents
                          if pair["combo_token"] in d["text"]]
            _check(combo_hits == [pair["doc_id"]],
                   f"{pair['combo_token']} appears in {combo_hits}")

    trap_states = {t["consent_state"] for t in manifest["narrative_traps"]}
    _check(len(manifest["narrative_traps"]) >= 40,
           "fewer than 40 narrative traps")
    _check(len(trap_states) >= 4,
           "narrative traps do not span at least four consent states")
    hidden_traps = [t for t in manifest["narrative_traps"]
                    if t["consent_state"] in NON_CONSENTED_STATES]
    _check(len(hidden_traps) >= 5,
           "fewer than five narrative traps on non-consented patients")

    _check(len(manifest["small_cells"]) >= 15, "fewer than 15 small cells")
    with_hidden = [c for c in manifest["small_cells"]
                   if any(c["non_consented"][p] for p in ("direct_care",
                                                          "research"))]
    _check(len(with_hidden) >= 10,
           "fewer than 10 small cells contain a non-consented member")

    controls = sum(1 for d in documents
                   if _allow(by_subject.get(d["patient_id"], []),
                             "direct_care", NOW))
    _check(controls >= 100,
           f"only {controls} control documents; exclusion output would be "
           "too thin to be useful")

    for purpose in PURPOSES:
        allowed = sum(1 for p in patients
                      if _allow(by_subject.get(p["patient_id"], []),
                                purpose, NOW))
        _check(0 < allowed < len(patients),
               f"purpose {purpose} has population on only one side")

    _check(any(c["scope"] for c in consents), "no scoped consent records")
    _check(any(not c["scope"] for c in consents), "no unscoped consent records")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def generate() -> dict[str, object]:
    rng = Random(SEED)
    patients = build_patients(rng)
    assign_strata(rng, patients)
    identities = assign_identities(rng, patients)
    consents = build_consents(rng, patients)
    documents, trap_records, oracle_records = build_documents(
        rng, patients, identities)
    small_cells = build_small_cells(patients, consents)

    generator_sha256 = hashlib.sha256(
        Path(__file__).resolve().read_bytes()).hexdigest()
    manifest = build_manifest(patients, documents, consents, trap_records,
                              small_cells, oracle_records, generator_sha256)
    verify(patients, documents, consents, manifest)

    # the age field is scaffolding for the trap sentences, not corpus data
    public_patients = [
        {
            "patient_id": p["patient_id"],
            "subject": p["subject"],
            "consent_state": p["consent_state"],
            "region": p["region"],
            "condition": p["condition"],
        }
        for p in patients
    ]
    return {
        "patients": public_patients,
        "documents": documents,
        "consents": consents,
        "manifest": manifest,
    }


def write_json(path: Path, payload: object) -> int:
    text = json.dumps(payload, indent=2, ensure_ascii=True) + "\n"
    data = text.encode("utf-8")
    # write_bytes, not write_text: newline translation on Windows would make
    # the published corpus platform-dependent, and the corpus is a hash
    path.write_bytes(data)
    return len(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the seeded synthetic consent-gate corpus.")
    parser.add_argument(
        "--out", default=str(Path(__file__).resolve().parent / "data"),
        help="output directory (default: corpus/data)")
    args = parser.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    corpus = generate()
    sizes = {
        "patients.json": write_json(out / "patients.json",
                                    corpus["patients"]),
        "documents.json": write_json(out / "documents.json",
                                     corpus["documents"]),
        "consents.json": write_json(out / "consents.json",
                                    corpus["consents"]),
        "manifest.json": write_json(out / "manifest.json",
                                    corpus["manifest"]),
    }

    manifest = corpus["manifest"]
    print(f"seed {SEED}  now {manifest['now']}")
    print(f"patients {manifest['counts']['patients']}  "
          f"documents {manifest['counts']['documents']}  "
          f"consents {manifest['counts']['consents']}  "
          f"identifiers {manifest['identifier_count']}")
    print("consent states " + "  ".join(
        f"{k}={v}" for k, v in manifest["consent_states"].items()))
    print(f"narrative traps {len(manifest['narrative_traps'])}  "
          f"small cells {len(manifest['small_cells'])}  "
          f"oracle pairs {len(manifest['oracle_pairs'])}")
    for name, size in sizes.items():
        print(f"  {name:<16} {size:>9} bytes")
    print(f"total {sum(sizes.values())} bytes into {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
