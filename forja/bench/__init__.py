"""Benchmark V2 infrastructure.

Leakage rule (enforced by tests): modules under forja.workflows must never
import anything from this package's gold/manifest side (corpusgen manifests,
goldgen, labeling). Dataset generation, gold labeling, matching, and scoring
are separate phases with separate entry points.
"""
