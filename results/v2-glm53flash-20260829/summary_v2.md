# Benchmark V2 summary

- mode: **live** | model: `z-ai/glm-5.3-flash` | date: 2026-08-29 | jobs: 1000 (slice 1000) | candidates: 24

| Metric | B0 | B1 | B2 | B3 |
|---|---|---|---|---|
| P@10 (grade ≥ 1) | 0.3 | 0.44 | 0.8083 | 0.7333 |
| Strong P@10 (grade 2) | 0.28 | 0.4 | 0.7708 | 0.6917 |
| Recall@10 | 0.1092 | 0.2262 | 0.6665 | 0.6181 |
| Recall@50 | 0.1092 | 0.2262 | 0.9446 | 0.9444 |
| Recall@50 (capped, diagnostic) | 0.1092 | 0.2262 | 0.9446 | 0.9444 |
| NDCG@10 | 0.2904 | 0.5244 | 0.9357 | 0.8186 |
| Violation rate (truth-judged) | 0.4 | 0.0 | 0.0833 | 0.1375 |
| Hallucination rate | 0.0 | 0.0 | 0.0 | 0.0 |
| Unsupported evidence rate | 0.358 | 0.211 | 0.0 | 0.0 |
| Opportunity loss (grade-2 missed) | 0.7718 | 0.6453 | 0.1137 | 0.1887 |
| False-negative rate (top-50) | 0.8908 | 0.7738 | 0.0554 | 0.0556 |
| Total violations | 4 | 0 | 20 | 33 |
| Model calls | 5 | 15 | 24 | 48 |
| Cost (USD, live only) | 0.07 | 0.07 | 0.05 | 0.01 |
| Wall time (s) | 650.26 | 981.61 | 1218.64 | 900.82 |

**Human active minutes per completed case: NOT MEASURED** — requires blinded review sessions (`python -m forja.bench.review`). The verdict in HYPOTHESIS_V2.md cannot be evaluated without it.

Violations by strata (which trap types fooled each arm):

- B0: {'filler': 2, 'misleading_title': 2}
- B1: {}
- B2: {'ambiguous_language': 6, 'filler': 5, 'planted_near': 1, 'trap': 5, 'trap_textonly': 9}
- B3: {'ambiguous_language': 7, 'filler': 10, 'planted_near': 2, 'trap': 6, 'trap_textonly': 15}