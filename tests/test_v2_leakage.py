"""Leakage separation (HYPOTHESIS_V2.md): matching must not see gold.

AST-level checks: the workflow arms and the phase-1 runner may not IMPORT the
gold side (goldgen, corpusgen manifests/archetypes, labeling) nor open gold
files; prose mentioning these concepts is fine — code touching them is not."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parent.parent / "forja"

FORBIDDEN_MODULES = ("goldgen", "labeling", "archetypes", "corpusgen",
                     "evaluation.gold", "score_v2")
FORBIDDEN_FILE_LITERALS = ("manifest.json", "labels.json", "labels_human")


def _imports_of(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names.append(mod)
            names += [f"{mod}.{a.name}" for a in node.names]
    return names


def _string_literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            values.append(node.value)
    # Exclude docstrings (module/class/function first-statement strings).
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                doc_nodes.add(doc)
    return [v for v in values if v not in doc_nodes]


def _assert_clean(path: Path) -> None:
    for imp in _imports_of(path):
        for bad in FORBIDDEN_MODULES:
            assert bad not in imp, f"{path} imports forbidden module {imp!r}"
    for lit in _string_literals(path):
        for bad in FORBIDDEN_FILE_LITERALS:
            assert bad not in lit, f"{path} references gold file {lit!r}"


def test_workflows_never_import_gold():
    for path in sorted((ROOT / "workflows").rglob("*.py")):
        _assert_clean(path)


def test_phase1_runner_never_imports_gold():
    _assert_clean(ROOT / "bench" / "run_v2.py")


def test_pipeline_never_imports_bench_or_evaluation_gold():
    for path in sorted((ROOT / "pipeline").rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        assert "from ..bench" not in src and "forja.bench" not in src, path
        assert "evaluation.gold" not in src and "from ..evaluation" not in src, \
            f"{path} imports the gold side"


def test_deterministic_modules_never_import_llm():
    """V1 rule with the V2 exception list: only profiling and softpref (plus
    the orchestrator) may touch the LLM boundary inside the pipeline."""
    allowed = {"profiling.py", "softpref.py", "__init__.py"}
    for path in sorted((ROOT / "pipeline").rglob("*.py")):
        if path.name in allowed:
            continue
        for imp in _imports_of(path):
            assert "llm" not in imp, f"{path} imports the LLM boundary ({imp})"
    for name in ("taxonomy.py", "schemas.py", "textutil.py"):
        for imp in _imports_of(ROOT / name):
            assert "llm" not in imp, f"{name} imports the LLM boundary"
    for path in sorted((ROOT / "evaluation").rglob("*.py")):
        for imp in _imports_of(path):
            assert "llm" not in imp, f"{path} imports the LLM boundary"
