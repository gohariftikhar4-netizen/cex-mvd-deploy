# cex-mvd-deploy

Two independent projects live here:

- **Forja Work benchmark harness** (`forja/`) — tests whether an AI-native
  employment workflow has a real economic advantage over a competent human
  using a general-purpose LLM. Start with [EDGE.md](EDGE.md) (frozen
  hypothesis), then [BENCHMARK.md](BENCHMARK.md) (methodology, results,
  limitations). Run: `python3 -m pytest tests/ -q` and
  `python3 -m forja.run_benchmark`.
- **CEX microstructure recorder** (`cex_microstructure_mvd/`) — self-contained
  VPS deployment package for recording public crypto market data. See
  [cex_microstructure_mvd/README_DEPLOY.md](cex_microstructure_mvd/README_DEPLOY.md).

AI assistants: read [CLAUDE.md](CLAUDE.md) before changing anything.
