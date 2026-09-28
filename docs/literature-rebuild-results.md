# Literature rebuild verification, 2026-09-27

Rebuilt all seven authoritative PDFs using the unchanged splitter (256/50) and
existing 768-dimensional embedding model. Replacing reproducible sources avoids
guessing which of the historical 1,724 excess equal-text rows were disposable.
The two web rows were preserved byte-for-byte. No other tables or schemas changed.

| Source | Before | After rebuild and both repeat checks |
| --- | ---: | ---: |
| ASHRAE-55-2013.pdf | 1172 | 614 |
| DOE M&V Guidelines 2015.pdf | 1238 | 652 |
| M&V Protocol 2001.pdf | 836 | 441 |
| Mobaraki et al 2021.pdf | 840 | 438 |
| Mobaraki et al 2022.pdf | 275 | 293 |
| Venkata et al 2024.pdf | 246 | 265 |
| WMO 2024.pdf | 1638 | 1729 |
| Existing web sources | 2 | 2 |
| **Total** | **6247** | **4434** |

Fresh parsing need not reproduce historical chunk boundaries, so the new total
is not the old total minus the historical duplicate estimate.

Verification:

- Each PDF page has one parent document ID; no repeated source/page/text/position
  groups remain. M&V page 14 has eight distinct texts and eight distinct vectors.
- All 4,432 prepared PDF chunk occurrences match storage exactly. All 3,347
  adjacent overlapping chunk pairs remain; no content deduplication was applied.
- Re-ingesting M&V leaves its 441 rows and the total 4,434 unchanged.
- A second complete PDF ingestion leaves every source count and total unchanged.
- A real failure injected after deletion and the first insert restored the entire
  original row fingerprint through transaction rollback.

## Requested retrieval

Question: `According to the literature, what relative humidity range is recommended for indoor environments?`

Unchanged direct literature retrieval, `RAG_K=5`. Page labels are stored PDF page
labels. Scores rounded to six decimals; full precision and node IDs are in the report.

| Rank | Before: source/page | Score | After: source/page | Score |
| --- | --- | ---: | --- | ---: |
| 1 | M&V Protocol 2001.pdf / 14 | 0.699159 | ASHRAE-55-2013.pdf / 24 | 0.734007 |
| 2 | M&V Protocol 2001.pdf / 14 | 0.699159 | M&V Protocol 2001.pdf / 14 | 0.701428 |
| 3 | M&V Protocol 2001.pdf / 14 | 0.697954 | M&V Protocol 2001.pdf / 37 | 0.700054 |
| 4 | M&V Protocol 2001.pdf / 14 | 0.697954 | M&V Protocol 2001.pdf / 14 | 0.698064 |
| 5 | M&V Protocol 2001.pdf / 37 | 0.693534 | ASHRAE-55-2013.pdf / 24 | 0.682184 |

Before ranks 1/2 and 3/4 were duplicate pairs. After results are distinct chunks:
ASHRAE humidity limits, discomfort at high humidity, humid-climate risks, humidity
extremes and thermal comfort, and ASHRAE's absence of a minimum humidity limit.
Same-page results now represent different passages.

The authenticated `/rag/query?framework=llamaindex` call also returned HTTP 200
with those five nodes, one embedding and one synthesis call, in 23.779 seconds.
It ran through TestClient without startup, so the snapshot scheduler did not run.
The unchanged synthesis incorrectly treated upper humidity limits as a recommended
range. This is a separate answer-quality issue, not a duplicate-index failure;
no prompt or synthesis changes were made. The full response is saved as
`api-query-after.json` beside the report.

Validation: 133 tests passed; the existing
`test_require_status_token_rejects_missing_header` failure remains unrelated.
Python compilation and diff whitespace checks passed.

Local artifacts (ignored by Git) are in
`scratch/literature-rebuild-20260927T160441Z/`: `before.csv` is the full original
table backup; `report.json` contains counts, fingerprints, verification results
and before/after retrieval excerpts. Retain the backup until satisfied.
