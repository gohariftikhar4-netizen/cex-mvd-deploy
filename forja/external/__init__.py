"""External (real-world) data adapters.

STRICTLY SEPARATE from the frozen Benchmark V2 machinery. Nothing in
`forja/bench/corpusgen/` may import this package, and nothing here may write
into the V2 corpus, gold, or scoring. V2 stays frozen; this package exists
for the external-validation track only.
"""
