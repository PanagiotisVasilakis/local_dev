from local_dev.repository.contracts import RepositoryContentKind


def classify_content(size: int, sample: bytes) -> RepositoryContentKind:
    if size == 0:
        return RepositoryContentKind.EMPTY
    if sample.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return RepositoryContentKind.TEXT
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return RepositoryContentKind.TEXT
    if b"\x00" in sample:
        return RepositoryContentKind.BINARY
    controls = sum(
        1
        for byte in sample
        if (byte < 32 and byte not in {8, 9, 10, 12, 13}) or byte == 127
    )
    if sample and controls / len(sample) > 0.05:
        return RepositoryContentKind.BINARY
    return RepositoryContentKind.TEXT
