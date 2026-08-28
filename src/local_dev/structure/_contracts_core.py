from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from local_dev.structure._contract_helpers import (
    require_digest,
    require_nonempty,
    require_parent,
    require_path,
    require_span,
)


class StructuralIndexError(RuntimeError):
    """Base class for deterministic structural-index failures."""


class StructuralIndexNotReady(StructuralIndexError):
    """Structural schema or repository index is unavailable."""


class StructuralIndexStale(StructuralIndexError):
    """Durable structural state does not match the supplied snapshot."""


class StructuralQueryError(StructuralIndexError):
    """A structural query violates the supported deterministic contract."""


class StructuralFileStatus(StrEnum):
    INDEXED = "indexed"
    PARSE_ERROR = "parse_error"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    UNSUPPORTED_CONTENT = "unsupported_content"
    SKIPPED_SIZE = "skipped_size"


class SymbolKind(StrEnum):
    CLASS = "class"
    FUNCTION = "function"
    ASYNC_FUNCTION = "async_function"
    METHOD = "method"
    ASYNC_METHOD = "async_method"
    VARIABLE = "variable"
    TYPE_ALIAS = "type_alias"


class ImportKind(StrEnum):
    IMPORT = "import"
    FROM_IMPORT = "from_import"


@dataclass(frozen=True, slots=True)
class StructuralIndexPolicy:
    max_file_bytes: int = 4 * 1024 * 1024
    max_ast_nodes: int = 250_000
    max_symbols_per_file: int = 50_000
    max_imports_per_file: int = 50_000
    default_limit: int = 100
    max_results: int = 2_000

    def __post_init__(self) -> None:
        for name in (
            "max_file_bytes",
            "max_ast_nodes",
            "max_symbols_per_file",
            "max_imports_per_file",
            "default_limit",
            "max_results",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.default_limit > self.max_results:
            raise ValueError("default_limit must not exceed max_results")


@dataclass(frozen=True, slots=True)
class StructuralSymbol:
    symbol_id: str
    path: str
    file_sha256: str
    kind: SymbolKind
    name: str
    qualified_name: str
    parent_symbol_id: str | None
    parent_qualified_name: str | None
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    signature: str | None = None
    decorators: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_digest(self.symbol_id, "symbol_id")
        require_path(self.path)
        require_digest(self.file_sha256, "file_sha256")
        if not isinstance(self.kind, SymbolKind):
            raise TypeError("kind must be SymbolKind")
        require_nonempty(self.name, "name")
        require_nonempty(self.qualified_name, "qualified_name")
        require_parent(self.parent_symbol_id, self.parent_qualified_name)
        require_span(self.start_line, self.start_col, self.end_line, self.end_col)
        if self.signature is not None and not isinstance(self.signature, str):
            raise TypeError("signature must be a string when present")
        decorators = tuple(self.decorators)
        if not all(isinstance(item, str) and item for item in decorators):
            raise ValueError("decorators must contain non-empty strings")
        object.__setattr__(self, "decorators", decorators)


@dataclass(frozen=True, slots=True)
class StructuralImport:
    import_id: str
    path: str
    file_sha256: str
    kind: ImportKind
    module: str | None
    name: str
    alias: str | None
    level: int
    scope_symbol_id: str | None
    scope_qualified_name: str | None
    line: int
    col: int
    ordinal: int

    def __post_init__(self) -> None:
        require_digest(self.import_id, "import_id")
        require_path(self.path)
        require_digest(self.file_sha256, "file_sha256")
        if not isinstance(self.kind, ImportKind):
            raise TypeError("kind must be ImportKind")
        if self.module is not None and (not isinstance(self.module, str) or not self.module):
            raise ValueError("module must be non-empty when present")
        require_nonempty(self.name, "name")
        if self.alias is not None and (not isinstance(self.alias, str) or not self.alias):
            raise ValueError("alias must be non-empty when present")
        if not isinstance(self.level, int) or isinstance(self.level, bool) or self.level < 0:
            raise ValueError("level must be a non-negative integer")
        require_parent(self.scope_symbol_id, self.scope_qualified_name)
        if not isinstance(self.line, int) or isinstance(self.line, bool) or self.line < 1:
            raise ValueError("line must be a positive integer")
        if not isinstance(self.col, int) or isinstance(self.col, bool) or self.col < 0:
            raise ValueError("col must be a non-negative integer")
        if not isinstance(self.ordinal, int) or isinstance(self.ordinal, bool) or self.ordinal < 0:
            raise ValueError("ordinal must be a non-negative integer")
        if self.kind is ImportKind.IMPORT:
            if self.level != 0 or self.module != self.name:
                raise ValueError("plain import requires level=0 and module=name")
        elif self.module is None and self.level == 0:
            raise ValueError("from-import requires a module or a relative level")
