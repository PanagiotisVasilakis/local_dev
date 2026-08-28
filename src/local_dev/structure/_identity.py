from __future__ import annotations

import hashlib
import json


def digest_payload(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def symbol_id(**fields: object) -> str:
    return digest_payload({"type": "symbol", **fields})


def import_id(**fields: object) -> str:
    return digest_payload({"type": "import", **fields})
