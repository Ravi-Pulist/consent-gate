# consent-gate (Consent-Aware Retrieval Layer)

## Overview

A retrieval layer that treats consent the way the law does: permission to
*use*, bound to a purpose and revocable, not permission to *name*. A record
without an active purpose-bound consent is not retrieved, not counted, not
aggregated, never reaches the prompt, and cannot be distinguished from a
record that does not exist.

Verifier positioning: the deliverable is an exhibit a client can re-run, not a
platform.

## Tech Stack

Python 3.10 - stdlib only for the library (sqlite3, hashlib, hmac). No
service dependencies, no model downloads, no network at query time. The
scaffold's default node/react/postgres/aws stack did not survive contact with
the problem.

## Domain

healthcare (Healthcare & Life Sciences). GDPR and India's DPDP both bind
consent to a purpose and permit withdrawal.

## Measured results

Corpus: 200 patients, 1000 documents, seeded with three trap families. For
purpose `direct_care`, 79 subjects consented and 120 not.

| | masking | exclusion |
|---|---|---|
| probes run | 113 | 113 |
| total leaks | 537 | 0 |
| narrative | 489 | 0 |
| oracle | 30 | 0 |
| small cell | 16 | 0 |
| count / pagination | 2 | 0 |

The masking baseline redacts every direct identifier at 100% recall, possible
only because the corpus is synthetic and every offset is known. Every leak
above survives a perfect masker.

Revocation mid-session with caches warm: every surface dark (vector+keyword
search, result cache, prompt context, aggregates) with the worst surface at
0.08 s against a 5 s target.

Tests: 70, including 9 sabotage cases, all caught.

## Design decisions that carried the result

1. Exclusion is a property of the candidate set, not the result set. The
   consented subject set is materialised and joined into the scan, so a
   non-consented record is never scored. Post-filtering leaks through counts,
   scores and pagination even when it returns no forbidden document -
   `tests/test_sabotage.py` implements it deliberately and the probes catch it
   through the count channel.
2. Purpose is mandatory and the vocabulary is closed. A purpose the caller can
   invent is a text box that always says yes.
3. Fail closed. An unreachable store returns empty plus an explicit error.
4. Append-only consent, enforced by database triggers. Revocation is an
   insert, so the state at any past instant stays reconstructable.
5. Caches stamped with consent versions, so revocation makes entries
   unreadable rather than merely scheduled for eviction.
6. Keyed pseudonyms, no linkage table anywhere in the package.

## What the build found

The semantic-recall path was itself a channel. At 256 hashed dimensions a
token present nowhere in the corpus scored 0.4 cosine against unrelated notes
purely through hash collisions, and those collisions were served as search
results. Two queries that should both have returned nothing returned
*different* nothing. Raised to 1024 dimensions with a 0.55 similarity floor,
recorded in the code with the reason.

## Not verified

- Timing side channel. Exclusion closes the content channel; response time is
  separate and is not claimed closed.
- Corpus totals. Publishing overall corpus size reopens differencing.
- Postgres + pgvector. The adapter interface is the extension point, but no
  Postgres was available in this environment, so the reference path was not
  run. All published numbers come from the SQLite path and its query plan is
  what is quoted.
- Retrieval quality is not what the exhibit tests.

## RMAD evidence

- `rmad doctor` - 69 passed, 0 failed, HEALTHY
- `rmad index build` - 18,414 LOC, 0 extraction errors, exact Python AST
- Task `P08-GATE`, 9 acceptance criteria, all symbol-mapped
- O1 criteria realised, O3 no regression, O4 no structural debt, O6 observed
  running

## Follow-ups

- Run the pgvector adapter once a Postgres instance is available and publish
  its EXPLAIN beside the SQLite one.
- Measure the timing channel on the oracle pairs and publish the distribution
  whichever way it comes out.
