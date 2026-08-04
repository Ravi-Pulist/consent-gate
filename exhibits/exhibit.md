# Consent as exclusion, not masking

Same corpus. Same queries. Two configurations. The only difference is where consent is applied: masking redacts what it retrieved, exclusion never retrieves it.

| | masking | exclusion |
|---|---|---|
| probes run | 113 | 113 |
| **total leaks** | **537** | **0** |
| count leaks | 2 | 0 |
| narrative leaks | 489 | 0 |
| oracle leaks | 30 | 0 |
| small_cell leaks | 16 | 0 |

Corpus: 200 patients, 1000 documents. For purpose `direct_care` at 2026-08-04T00:00:00+00:00, 79 subjects are consented and 120 are not.

> The masking configuration masks every direct identifier at **100% recall**, which is only possible because the corpus is synthetic and every identifier's offset is known. Every leak below survives a perfect masker. A real NER masker also misses identifiers outright, so this understates the failure.

## The narrative leak

The name is gone. The sentence still describes one person.

**Masking: 489 leaks. Exclusion: 0.**

> This contact was a scheduled review of Harrowfield anaemia. Beacon, as the ward has called them since childhood, is 42 and still working as a lock keeper on the Cinderfell shorefront. Transport support was offered and de

`subject P138` · `D0683` · surfaced by probe `D0003`

> The care team reviewed the current management of Harrowfield anaemia. Sleep and appetite were described as ordinary. No safeguarding concerns were identified at this contact. Home readings brought to the appointment were

`subject P127` · `D0637` · surfaced by probe `D0003`

> The encounter covered ongoing management of Sable-tract colitis and nothing further. Wick, as the ward has called them since childhood, is 32 and still working as a cartwright on the Bramleigh shorefront. Fasting bloods 

`subject P110` · `D0543` · surfaced by probe `D0003`

_486 further instances in the JSON output._

## The small-cell leak

It does not matter that the record was masked. It was counted, and the count is the disclosure.

**Masking: 16 leaks. Exclusion: 0.**

> stratum cond=Ashcombe myopathy|region=Vellum Bay has 2 member(s) in the counted set, of which 1 withdrew consent: ['P184']

`subject P184` · `cond=Ashcombe myopathy|region=Vellum Bay` · surfaced by probe `cond=Ashcombe myopathy|region=Vellum Bay`

> stratum cond=Brackwater neuropathy|region=Netherby Cross has 2 member(s) in the counted set, of which 1 withdrew consent: ['P100']

`subject P100` · `cond=Brackwater neuropathy|region=Netherby Cross` · surfaced by probe `cond=Brackwater neuropathy|region=Netherby Cross`

> stratum cond=Cindral ataxia|region=Quillmoor has 3 member(s) in the counted set, of which 1 withdrew consent: ['P091']

`subject P091` · `cond=Cindral ataxia|region=Quillmoor` · surfaced by probe `cond=Cindral ataxia|region=Quillmoor`

_13 further instances in the JSON output._

## The search-oracle leak

A token unique to a withdrawn record, against a token that exists nowhere. Any observable difference answers 'is this person in your index?'

**Masking: 30 leaks. Exclusion: 0.**

> querying a token unique to a withdrawn record returned 1 match(es) and 1 hit(s); querying a token that exists nowhere returned 0 match(es) and 0 hit(s). The two are distinguishable, so the index answers questions about w

`subject P004` · `D0018` · surfaced by probe `ZZORACLE-010-kejr`

> querying a token unique to a withdrawn record returned 1 match(es) and 1 hit(s); querying a token that exists nowhere returned 0 match(es) and 0 hit(s). The two are distinguishable, so the index answers questions about w

`subject P023` · `D0118` · surfaced by probe `ZZORACLE-014-mlyf`

> querying a token unique to a withdrawn record returned 1 match(es) and 1 hit(s); querying a token that exists nowhere returned 0 match(es) and 0 hit(s). The two are distinguishable, so the index answers questions about w

`subject P025` · `D0131` · surfaced by probe `ZZORACLE-018-tpnm`

_27 further instances in the JSON output._

## The predicate is inside the query plan

Not applied to results afterwards. This is the engine's own plan for the query that ran:

**Exclusion**
```
SCAN d
SEARCH c USING COVERING INDEX sqlite_autoindex_consented_1 (subject=?)
USE TEMP B-TREE FOR ORDER BY
```

**Masking** (no consent predicate exists to push down)
```
SCAN d
USE TEMP B-TREE FOR ORDER BY
```

## Revocation, measured

Mid-session revocation of `P001` with caches warm. Every surface dark: **True**. Worst surface: **0.0799 s**.

| surface | visible before | visible after | seconds to dark |
|---|---|---|---|
| vector+keyword search | True | False | 0.0799 |
| result cache | True | False | 0.0001 |
| prompt context | True | False | 0.0002 |
| aggregates | True | False | 0.0000 |

> A surface that was never visible before revocation proves nothing about revocation, so it is not counted as a pass.

## What this does not close

- **Timing.** Exclusion closes the content channel. Response time is a separate channel and is measured separately; it is not claimed closed here.
- **Corpus totals.** If overall corpus size is published, consented-count changes leak consent *events* by differencing. The gate exposes no totals; a deployment that does reopens this.
- **Retrieval quality** is not what this exhibit tests. The embeddings are a deterministic hashed bag of words so a sceptic can reproduce the run without downloading a model.
