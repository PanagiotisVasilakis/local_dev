from __future__ import annotations

from dataclasses import dataclass

from local_dev.structure._contract_helpers import require_digest, require_nonempty, require_path
from local_dev.structure._contract_helpers import sorted_paths
from local_dev.structure._contracts_core import StructuralFileStatus


@dataclass(frozen=True, slots=True)
class StructuralFileReport:
    path: str
    status: StructuralFileStatus
    symbol_count: int
    import_count: int
    error_message: str | None = None

    def __post_init__(self) -> None:
        require_path(self.path)
        if not isinstance(self.status, StructuralFileStatus):
            raise TypeError("status must be StructuralFileStatus")
        for name in ("symbol_count", "import_count"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.status is StructuralFileStatus.INDEXED:
            if self.error_message is not None:
                raise ValueError("indexed files must not have error_message")
        elif self.symbol_count or self.import_count:
            raise ValueError("non-indexed files must not expose structural rows")
        if self.status is StructuralFileStatus.PARSE_ERROR:
            require_nonempty(self.error_message, "error_message")
        elif self.error_message is not None:
            raise ValueError("only parse_error files may have error_message")


@dataclass(frozen=True, slots=True)
class StructuralSyncResult:
    snapshot_fingerprint: str
    rebuilt_paths: tuple[str, ...]
    removed_paths: tuple[str, ...]
    unchanged_paths: tuple[str, ...]
    reports: tuple[StructuralFileReport, ...]
    symbol_count: int
    import_count: int

    def __post_init__(self) -> None:
        require_digest(self.snapshot_fingerprint, "snapshot_fingerprint")
        rebuilt = sorted_paths(self.rebuilt_paths, "rebuilt_paths")
        removed = sorted_paths(self.removed_paths, "removed_paths")
        unchanged = sorted_paths(self.unchanged_paths, "unchanged_paths")
        sets = (set(rebuilt), set(removed), set(unchanged))
        if sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2]:
            raise ValueError("sync path sets must be disjoint")
        reports = tuple(self.reports)
        if not all(isinstance(report, StructuralFileReport) for report in reports):
            raise TypeError("reports must contain StructuralFileReport values")
        report_paths = tuple(report.path for report in reports)
        if report_paths != tuple(sorted(set(report_paths))):
            raise ValueError("reports must contain unique sorted paths")
        if set(report_paths) != sets[0] | sets[2]:
            raise ValueError("reports must cover all current repository paths")
        for name in ("symbol_count", "import_count"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.symbol_count != sum(report.symbol_count for report in reports):
            raise ValueError("symbol_count does not match reports")
        if self.import_count != sum(report.import_count for report in reports):
            raise ValueError("import_count does not match reports")
        object.__setattr__(self, "rebuilt_paths", rebuilt)
        object.__setattr__(self, "removed_paths", removed)
        object.__setattr__(self, "unchanged_paths", unchanged)
        object.__setattr__(self, "reports", reports)
