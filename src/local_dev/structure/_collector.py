from __future__ import annotations

import ast
from dataclasses import dataclass

from local_dev.structure._collector_helpers import class_signature, target_names
from local_dev.structure._collector_support import CollectorSupport
from local_dev.structure._signature import render_signature
from local_dev.structure.contracts import ImportKind, StructuralImport, StructuralSymbol, SymbolKind


@dataclass(frozen=True, slots=True)
class ParsedStructure:
    symbols: tuple[StructuralSymbol, ...]
    imports: tuple[StructuralImport, ...]


class PythonStructureCollector(CollectorSupport, ast.NodeVisitor):
    def __init__(self, path: str, file_sha256: str, max_symbols: int, max_imports: int) -> None:
        self.path = path
        self.file_sha256 = file_sha256
        self.max_symbols = max_symbols
        self.max_imports = max_imports
        self.symbols: list[StructuralSymbol] = []
        self.imports: list[StructuralImport] = []
        self._scopes: list[StructuralSymbol] = []
        self._symbol_ordinal = 0
        self._import_ordinal = 0

    def result(self) -> ParsedStructure:
        return ParsedStructure(tuple(self.symbols), tuple(self.imports))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        symbol = self._definition(node, SymbolKind.CLASS, signature=class_signature(node))
        self._within(symbol, node.body)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        kind = SymbolKind.METHOD if self._inside_class() else SymbolKind.FUNCTION
        symbol = self._definition(node, kind, signature=render_signature(node))
        self._within(symbol, node.body)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        kind = SymbolKind.ASYNC_METHOD if self._inside_class() else SymbolKind.ASYNC_FUNCTION
        symbol = self._definition(node, kind, signature=render_signature(node))
        self._within(symbol, node.body)

    def visit_Assign(self, node: ast.Assign) -> None:
        if self._variable_scope_allowed():
            for target in node.targets:
                for name, target_node in target_names(target):
                    self._variable(target_node, name, SymbolKind.VARIABLE)
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if self._variable_scope_allowed():
            for name, target_node in target_names(node.target):
                self._variable(target_node, name, SymbolKind.VARIABLE)
        if node.value is not None:
            self.visit(node.value)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)

    def visit_TypeAlias(self, node: ast.TypeAlias) -> None:  # Python 3.12+
        name = ast.unparse(node.name)
        self._variable(node, name, SymbolKind.TYPE_ALIAS)
        self.visit(node.value)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._add_import(node, ImportKind.IMPORT, alias.name, alias.name, alias.asname, 0)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self._add_import(
                node,
                ImportKind.FROM_IMPORT,
                node.module,
                alias.name,
                alias.asname,
                node.level,
            )

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.visit(node.body)
