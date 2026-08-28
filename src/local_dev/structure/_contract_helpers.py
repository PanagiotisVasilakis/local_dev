from __future__ import annotations


def require_digest(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def require_nonempty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def require_path(value: object) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "\x00" in value:
        raise ValueError("path must be a normalized repository-relative string")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("path must be a normalized repository-relative string")
    return value


def require_parent(symbol_id: object, qualified_name: object) -> None:
    if (symbol_id is None) != (qualified_name is None):
        raise ValueError("symbol id and qualified name must be present together")
    if symbol_id is not None:
        require_digest(symbol_id, "scope/parent symbol id")
        require_nonempty(qualified_name, "scope/parent qualified name")


def require_span(
    start_line: object,
    start_col: object,
    end_line: object,
    end_col: object,
) -> None:
    if not isinstance(start_line, int) or isinstance(start_line, bool) or start_line < 1:
        raise ValueError("start_line must be a positive integer")
    if not isinstance(end_line, int) or isinstance(end_line, bool) or end_line < start_line:
        raise ValueError("end_line must not precede start_line")
    if not isinstance(start_col, int) or isinstance(start_col, bool) or start_col < 0:
        raise ValueError("start_col must be a non-negative integer")
    if not isinstance(end_col, int) or isinstance(end_col, bool) or end_col < 0:
        raise ValueError("end_col must be a non-negative integer")
    if end_line == start_line and end_col < start_col:
        raise ValueError("same-line end_col must not precede start_col")


def sorted_paths(values: object, name: str) -> tuple[str, ...]:
    try:
        result = tuple(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError(f"{name} must be iterable") from exc
    for value in result:
        require_path(value)
    if result != tuple(sorted(set(result))):
        raise ValueError(f"{name} must contain unique sorted paths")
    return result
