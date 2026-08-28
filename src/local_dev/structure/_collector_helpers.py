from __future__ import annotations

import ast


def target_names(node: ast.AST) -> tuple[tuple[str, ast.AST], ...]:
    if isinstance(node, ast.Name):
        return ((node.id, node),)
    if isinstance(node, (ast.Tuple, ast.List)):
        result: list[tuple[str, ast.AST]] = []
        for item in node.elts:
            result.extend(target_names(item))
        return tuple(result)
    return ()


def class_signature(node: ast.ClassDef) -> str:
    type_params = getattr(node, "type_params", ())
    prefix = node.name
    if type_params:
        prefix += "[" + ", ".join(ast.unparse(item) for item in type_params) + "]"
    arguments = [ast.unparse(base) for base in node.bases]
    arguments.extend(
        (
            f"{keyword.arg}={ast.unparse(keyword.value)}"
            if keyword.arg
            else f"**{ast.unparse(keyword.value)}"
        )
        for keyword in node.keywords
    )
    return prefix + ("(" + ", ".join(arguments) + ")" if arguments else "")
