from __future__ import annotations

import ast
import io
import tokenize

from local_dev.structure._collector import ParsedStructure, PythonStructureCollector
from local_dev.structure.contracts import StructuralIndexError, StructuralIndexPolicy


def parse_python(
    payload: bytes,
    path: str,
    file_sha256: str,
    policy: StructuralIndexPolicy,
) -> ParsedStructure:
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(payload).readline)
        text = payload.decode(encoding)
    except (SyntaxError, UnicodeDecodeError, LookupError) as exc:
        raise SyntaxError(f"source decoding failed: {exc}") from exc
    try:
        tree = ast.parse(text, filename=path, mode="exec", feature_version=(3, 12))
    except SyntaxError:
        raise
    except (RecursionError, OverflowError) as exc:
        raise StructuralIndexError(f"Python AST parser resource limit exceeded: {path}") from exc
    node_count = 0
    try:
        for _ in ast.walk(tree):
            node_count += 1
            if node_count > policy.max_ast_nodes:
                raise StructuralIndexError(f"file exceeds AST node limit: {path}")
    except StructuralIndexError:
        raise
    except (RecursionError, OverflowError) as exc:
        raise StructuralIndexError(f"Python AST walk resource limit exceeded: {path}") from exc
    try:
        compile(tree, path, "exec")
    except (SyntaxError, ValueError, TypeError) as exc:
        raise SyntaxError(str(exc)) from exc
    except (RecursionError, OverflowError) as exc:
        raise StructuralIndexError(f"Python compiler resource limit exceeded: {path}") from exc
    collector = PythonStructureCollector(
        path,
        file_sha256,
        policy.max_symbols_per_file,
        policy.max_imports_per_file,
    )
    try:
        collector.visit(tree)
    except StructuralIndexError:
        raise
    except (RecursionError, OverflowError) as exc:
        raise StructuralIndexError(
            f"Python structural traversal resource limit exceeded: {path}"
        ) from exc
    return collector.result()
