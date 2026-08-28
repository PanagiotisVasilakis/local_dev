from __future__ import annotations

import ast

from local_dev.structure._identity import import_id, symbol_id
from local_dev.structure.contracts import (
    ImportKind,
    StructuralImport,
    StructuralIndexError,
    StructuralSymbol,
    SymbolKind,
)


class CollectorSupport:
    path: str
    file_sha256: str
    max_symbols: int
    max_imports: int
    symbols: list[StructuralSymbol]
    imports: list[StructuralImport]
    _scopes: list[StructuralSymbol]
    _symbol_ordinal: int
    _import_ordinal: int

    def _definition(
        self,
        node: ast.AST,
        kind: SymbolKind,
        *,
        signature: str | None,
    ) -> StructuralSymbol:
        name = getattr(node, "name")
        if not isinstance(name, str) or not name:
            raise StructuralIndexError(f"Python definition lacks a valid name: {self.path}")
        start_line, start_col, end_line, end_col = _node_span(node, self.path)
        parent = self._scopes[-1] if self._scopes else None
        qualified = f"{parent.qualified_name}.{name}" if parent else name
        decorators = tuple(ast.unparse(item) for item in getattr(node, "decorator_list", ()))
        self._symbol_ordinal += 1
        symbol = StructuralSymbol(
            symbol_id=symbol_id(
                path=self.path,
                file_sha256=self.file_sha256,
                kind=kind.value,
                qualified_name=qualified,
                start_line=start_line,
                start_col=start_col,
                end_line=end_line,
                end_col=end_col,
                ordinal=self._symbol_ordinal,
            ),
            path=self.path,
            file_sha256=self.file_sha256,
            kind=kind,
            name=name,
            qualified_name=qualified,
            parent_symbol_id=None if parent is None else parent.symbol_id,
            parent_qualified_name=None if parent is None else parent.qualified_name,
            start_line=start_line,
            start_col=start_col,
            end_line=end_line,
            end_col=end_col,
            signature=signature,
            decorators=decorators,
        )
        self._append_symbol(symbol)
        return symbol

    def _variable(self, node: ast.AST, name: str, kind: SymbolKind) -> None:
        start_line, start_col, end_line, end_col = _node_span(node, self.path)
        parent = self._scopes[-1] if self._scopes else None
        qualified = f"{parent.qualified_name}.{name}" if parent else name
        self._symbol_ordinal += 1
        self._append_symbol(
            StructuralSymbol(
                symbol_id=symbol_id(
                    path=self.path,
                    file_sha256=self.file_sha256,
                    kind=kind.value,
                    qualified_name=qualified,
                    start_line=start_line,
                    start_col=start_col,
                    end_line=end_line,
                    end_col=end_col,
                    ordinal=self._symbol_ordinal,
                ),
                path=self.path,
                file_sha256=self.file_sha256,
                kind=kind,
                name=name,
                qualified_name=qualified,
                parent_symbol_id=None if parent is None else parent.symbol_id,
                parent_qualified_name=None if parent is None else parent.qualified_name,
                start_line=start_line,
                start_col=start_col,
                end_line=end_line,
                end_col=end_col,
            )
        )

    def _add_import(
        self,
        node: ast.AST,
        kind: ImportKind,
        module: str | None,
        name: str,
        alias: str | None,
        level: int,
    ) -> None:
        if len(self.imports) >= self.max_imports:
            raise StructuralIndexError(f"file exceeds structural import limit: {self.path}")
        start_line, start_col, _, _ = _node_span(node, self.path)
        scope = self._scopes[-1] if self._scopes else None
        self._import_ordinal += 1
        self.imports.append(
            StructuralImport(
                import_id=import_id(
                    path=self.path,
                    file_sha256=self.file_sha256,
                    kind=kind.value,
                    module=module,
                    name=name,
                    alias=alias,
                    level=level,
                    scope_qualified_name=None if scope is None else scope.qualified_name,
                    line=start_line,
                    col=start_col,
                    ordinal=self._import_ordinal,
                ),
                path=self.path,
                file_sha256=self.file_sha256,
                kind=kind,
                module=module,
                name=name,
                alias=alias,
                level=level,
                scope_symbol_id=None if scope is None else scope.symbol_id,
                scope_qualified_name=None if scope is None else scope.qualified_name,
                line=start_line,
                col=start_col,
                ordinal=self._import_ordinal,
            )
        )

    def _append_symbol(self, symbol: StructuralSymbol) -> None:
        if len(self.symbols) >= self.max_symbols:
            raise StructuralIndexError(f"file exceeds structural symbol limit: {self.path}")
        self.symbols.append(symbol)

    def _within(self, symbol: StructuralSymbol, body: list[ast.stmt]) -> None:
        self._scopes.append(symbol)
        try:
            for statement in body:
                self.visit(statement)  # type: ignore[attr-defined]
        finally:
            self._scopes.pop()

    def _inside_class(self) -> bool:
        return bool(self._scopes and self._scopes[-1].kind is SymbolKind.CLASS)

    def _variable_scope_allowed(self) -> bool:
        return not self._scopes or self._scopes[-1].kind is SymbolKind.CLASS


def _node_span(node: ast.AST, path: str) -> tuple[int, int, int, int]:
    start_line = getattr(node, "lineno", None)
    start_col = getattr(node, "col_offset", None)
    end_line = getattr(node, "end_lineno", None)
    end_col = getattr(node, "end_col_offset", None)
    for value in (start_line, start_col, end_line, end_col):
        if not isinstance(value, int) or isinstance(value, bool):
            raise StructuralIndexError(f"Python AST node is missing source positions: {path}")
    assert isinstance(start_line, int)
    assert isinstance(start_col, int)
    assert isinstance(end_line, int)
    assert isinstance(end_col, int)
    return start_line, start_col, end_line, end_col
