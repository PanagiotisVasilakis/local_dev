from __future__ import annotations

from local_dev.structure._contract_helpers import require_path as _require_path
from local_dev.structure.contracts import StructuralIndexError

_ESCAPE = "\x1f"


def require_path(path: object) -> str:
    return _require_path(path)


def encode_path(path: str) -> str:
    require_path(path)
    try:
        path.encode("utf-8")
    except UnicodeEncodeError:
        return _ESCAPE + "s" + path.encode("utf-8", errors="surrogatepass").hex()
    if path.startswith(_ESCAPE):
        return _ESCAPE + "u" + path
    return path


def decode_path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise StructuralIndexError("durable structural path encoding is invalid")
    if value.startswith(_ESCAPE + "s"):
        try:
            result = bytes.fromhex(value[2:]).decode("utf-8", errors="surrogatepass")
        except (ValueError, UnicodeDecodeError) as exc:
            raise StructuralIndexError("durable structural path encoding is invalid") from exc
        try:
            return require_path(result)
        except ValueError as exc:
            raise StructuralIndexError("durable structural path encoding is invalid") from exc
    if value.startswith(_ESCAPE + "u"):
        candidate = value[2:]
    elif value.startswith(_ESCAPE):
        raise StructuralIndexError("durable structural path encoding is invalid")
    else:
        candidate = value
    try:
        return require_path(candidate)
    except ValueError as exc:
        raise StructuralIndexError("durable structural path encoding is invalid") from exc
