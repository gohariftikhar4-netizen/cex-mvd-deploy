# cex-mvd-deploy

Two independent projects live here:

- **Forja Work benchmark harness** (`forja/`) — Benchmark V2 tests whether
  Forja beats a competent production-grade AI baseline on measured human
  work. Start with [EDGE.md](EDGE.md) and [HYPOTHESIS_V2.md](HYPOTHESIS_V2.md)
  (both frozen), then [BENCHMARK.md](BENCHMARK.md) (methodology) and
  [RED_TEAM_V2.md](RED_TEAM_V2.md) (current honest answers). Run:
  `python3 -m pytest tests/ -q`, then see BENCHMARK.md §5.
- **CEX microstructure recorder** (`cex_microstructure_mvd/`) — self-contained
  VPS deployment package for recording public crypto market data. See
  [cex_microstructure_mvd/README_DEPLOY.md](cex_microstructure_mvd/README_DEPLOY.md).

AI assistants: read [CLAUDE.md](CLAUDE.md) before changing anything.
