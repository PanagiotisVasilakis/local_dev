from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from local_dev.repository.contracts import RepositoryEntry
from local_dev.retrieval.contracts import LexicalIndexError, LexicalIndexPolicy


@dataclass(frozen=True, slots=True)
class PreparedChunk:
    chunk_index: int
    chunk_id: str
    start_line: int
    end_line: int
    content: str
    content_sha256: str


def decode_text(payload: bytes) -> tuple[str, bool]:
    if payload.startswith(b"\xef\xbb\xbf"):
        return payload.decode("utf-8-sig"), False
    if payload.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        try:
            return payload.decode("utf-32"), False
        except UnicodeDecodeError:
            pass
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return payload.decode("utf-16"), False
        except UnicodeDecodeError:
            pass
    try:
        return payload.decode("utf-8"), False
    except UnicodeDecodeError:
        return payload.decode("utf-8", errors="replace"), True


def chunk_text(
    text: str,
    entry: RepositoryEntry,
    policy: LexicalIndexPolicy,
) -> tuple[PreparedChunk, ...]:
    lines = text.splitlines(keepends=True)
    if not lines:
        return (_make_chunk(entry, 0, 1, 1, ""),)

    chunks: list[PreparedChunk] = []

    def append_chunk(start_line: int, end_line: int, content: str) -> None:
        if len(chunks) >= policy.max_chunks_per_file:
            raise LexicalIndexError(
                f"file produced more than {policy.max_chunks_per_file} lexical chunks: "
                f"{entry.path}"
            )
        chunks.append(
            _make_chunk(
                entry,
                len(chunks),
                start_line,
                end_line,
                content,
            )
        )

    start = 0
    while start < len(lines):
        if len(lines[start]) > policy.max_chunk_chars:
            line = lines[start]
            for offset in range(0, len(line), policy.max_chunk_chars):
                append_chunk(
                    start + 1,
                    start + 1,
                    line[offset : offset + policy.max_chunk_chars],
                )
            start += 1
            continue

        end = min(len(lines), start + policy.chunk_lines)
        while end > start + 1:
            content_length = sum(len(line) for line in lines[start:end])
            if content_length <= policy.max_chunk_chars:
                break
            end -= 1

        append_chunk(start + 1, end, "".join(lines[start:end]))
        if end >= len(lines):
            break
        start = max(start + 1, end - policy.overlap_lines)

    return tuple(chunks)


def chunk_id(
    *,
    path: str,
    file_sha256: str,
    chunk_index: int,
    start_line: int,
    end_line: int,
    content_sha256: str,
) -> str:
    payload = {
        "path": path,
        "file_sha256": file_sha256,
        "chunk_index": chunk_index,
        "start_line": start_line,
        "end_line": end_line,
        "content_sha256": content_sha256,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _make_chunk(
    entry: RepositoryEntry,
    chunk_index: int,
    start_line: int,
    end_line: int,
    content: str,
) -> PreparedChunk:
    content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return PreparedChunk(
        chunk_index=chunk_index,
        chunk_id=chunk_id(
            path=entry.path,
            file_sha256=entry.sha256,
            chunk_index=chunk_index,
            start_line=start_line,
            end_line=end_line,
            content_sha256=content_sha256,
        ),
        start_line=start_line,
        end_line=end_line,
        content=content,
        content_sha256=content_sha256,
    )
