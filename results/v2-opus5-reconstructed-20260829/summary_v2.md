# Benchmark V2 summary

- mode: **live** | model: `claude-opus-5` | date: 2026-08-29 | jobs: 1000 (slice 1000) | candidates: 19

| Metric | B0 | B1 | B2 | B3 |
|---|---|---|---|---|
| P@10 (grade ≥ 1) | 0.8211 | 0.84 | 0.88 | 0.7533 |
| Strong P@10 (grade 2) | 0.7105 | 0.7133 | 0.78 | 0.7133 |
| Recall@10 | 0.6616 | 0.6432 | 0.6608 | 0.5833 |
| Recall@50 | 0.8068 | 0.7623 | 0.9325 | 0.9345 |
| Recall@50 (capped, diagnostic) | 0.8068 | 0.7623 | 0.9325 | 0.9345 |
| NDCG@10 | 0.8852 | 0.8973 | 0.9342 | 0.8228 |
| Violation rate (truth-judged) | 0.0906 | 0.0 | 0.0333 | 0.1133 |
| Hallucination rate | 0.0 | 0.0 | 0.0 | 0.0 |
| Unsupported evidence rate | 0.0 | 0.0126 | 0.0 | 0.0 |
| Opportunity loss (grade-2 missed) | 0.1922 | 0.1863 | 0.1475 | 0.201 |
| False-negative rate (top-50) | 0.1932 | 0.2377 | 0.0675 | 0.0655 |
| Total violations | 17 | 0 | 5 | 17 |
| Model calls | 19 | 45 | 15 | 30 |
| Cost (USD, live only) | 7.46 | 10.59 | 3.47 | 1.49 |
| Wall time (s) | 0.64 | 0.53 | 0.51 | 1.24 |

**Human active minutes per completed case: NOT MEASURED** — requires blinded review sessions (`python -m forja.bench.review`). The verdict in HYPOTHESIS_V2.md cannot be evaluated without it.

Violations by strata (which trap types fooled each arm):

- B0: {'ambiguous_language': 4, 'near_duplicate_trap': 2, 'planted_near': 2, 'trap': 11, 'trap_textonly': 2}
- B1: {}
- B2: {'ambiguous_language': 1, 'filler': 2, 'planted_near': 1, 'trap': 1, 'trap_textonly': 1}
- B3: {'ambiguous_language': 2, 'filler': 4, 'planted_near': 1, 'trap': 3, 'trap_textonly': 9}