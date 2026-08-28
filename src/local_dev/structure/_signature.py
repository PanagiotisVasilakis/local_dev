from __future__ import annotations

import ast


def render_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    defaults: list[ast.expr | None] = (
        [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    )
    parts: list[str] = []
    posonly_count = len(args.posonlyargs)
    for index, (argument, default) in enumerate(zip(positional, defaults, strict=True)):
        parts.append(_argument(argument, default))
        if posonly_count and index + 1 == posonly_count:
            parts.append("/")
    if args.vararg is not None:
        parts.append("*" + _argument(args.vararg, None))
    elif args.kwonlyargs:
        parts.append("*")
    for argument, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        parts.append(_argument(argument, default))
    if args.kwarg is not None:
        parts.append("**" + _argument(args.kwarg, None))
    type_params = getattr(node, "type_params", ())
    type_prefix = ""
    if type_params:
        type_prefix = "[" + ", ".join(ast.unparse(item) for item in type_params) + "]"
    result = f"{node.name}{type_prefix}({', '.join(parts)})"
    if node.returns is not None:
        result += " -> " + ast.unparse(node.returns)
    return result


def _argument(argument: ast.arg, default: ast.expr | None) -> str:
    value = argument.arg
    if argument.annotation is not None:
        value += ": " + ast.unparse(argument.annotation)
    if default is not None:
        value += " = " + ast.unparse(default)
    return value
