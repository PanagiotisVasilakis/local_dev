"""Public deterministic repository scanner."""

from local_dev.repository import _scanner_base as _base
from local_dev.repository._scanner_hardened import RepositoryScanner

RepositoryScanPolicy = _base.RepositoryScanPolicy
detect_language = _base.detect_language

# Kept as a module-level hook for race-injection tests and internal diagnostics.
_open_child_directory = _base._open_child_directory

__all__ = [
    "RepositoryScanPolicy",
    "RepositoryScanner",
    "detect_language",
]
