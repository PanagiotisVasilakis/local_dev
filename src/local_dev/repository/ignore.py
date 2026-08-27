from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True, slots=True)
class _IgnoreRule:
    base_path: str
    regex: re.Pattern[str]
    basename_only: bool
    directory_only: bool
    negated: bool

    def matches(self, path: str, *, is_dir: bool) -> bool:
        if self.directory_only and not is_dir:
            return False
        relative = _relative_to_base(path, self.base_path)
        if relative is None:
            return False
        if self.basename_only:
            return self.regex.fullmatch(PurePosixPath(relative).name) is not None
        return self.regex.fullmatch(relative) is not None


class RepositoryIgnoreRules:
    """Repository-local .gitignore matcher for deterministic scanner selection."""

    def __init__(self, rules: tuple[_IgnoreRule, ...]) -> None:
        self._rules = rules

    @classmethod
    def from_sources(
        cls,
        sources: tuple[tuple[str, str], ...],
    ) -> "RepositoryIgnoreRules":
        rules: list[_IgnoreRule] = []
        ordered = sorted(
            sources,
            key=lambda item: (
                len(PurePosixPath(item[0]).parts),
                item[0],
            ),
        )
        for ignore_path, raw in ordered:
            parent = PurePosixPath(ignore_path).parent
            base_relative = "" if parent.as_posix() == "." else parent.as_posix()
            for line_number, line in enumerate(raw.splitlines(), start=1):
                try:
                    rule = _parse_rule(base_relative, line)
                except re.error as exc:
                    raise ValueError(
                        f"invalid ignore pattern in {ignore_path} at line {line_number}"
                    ) from exc
                if rule is not None:
                    rules.append(rule)
        return cls(tuple(rules))

    def has_ignored_ancestor(self, path: str) -> bool:
        pure = PurePosixPath(path)
        parts = pure.parts
        for end in range(1, len(parts)):
            ancestor = PurePosixPath(*parts[:end]).as_posix()
            decision = self._decision(ancestor, is_dir=True)
            if decision is True:
                return True
        return False

    def is_ignored(self, path: str, *, is_dir: bool) -> bool:
        if self.has_ignored_ancestor(path):
            return True
        decision = self._decision(path, is_dir=is_dir)
        return decision is True

    def _decision(self, path: str, *, is_dir: bool) -> bool | None:
        decision: bool | None = None
        for rule in self._rules:
            if rule.matches(path, is_dir=is_dir):
                decision = not rule.negated
        return decision


def _parse_rule(base_path: str, raw_line: str) -> _IgnoreRule | None:
    line = _trim_unescaped_trailing_spaces(raw_line)
    if not line:
        return None
    if line.startswith(r"\#"):
        line = line[1:]
    elif line.startswith("#"):
        return None

    negated = False
    if line.startswith(r"\!"):
        line = line[1:]
    elif line.startswith("!"):
        negated = True
        line = line[1:]
    if not line:
        return None

    directory_only = line.endswith("/") and not line.endswith(r"\/")
    if directory_only:
        line = line[:-1]
    anchored = line.startswith("/")
    if anchored:
        line = line[1:]
    if not line:
        return None

    basename_only = not anchored and "/" not in line
    regex = re.compile(_glob_regex(line))
    return _IgnoreRule(
        base_path=base_path,
        regex=regex,
        basename_only=basename_only,
        directory_only=directory_only,
        negated=negated,
    )


def _glob_regex(pattern: str) -> str:
    pieces = ["^"]
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "\\" and index + 1 < length:
            index += 1
            pieces.append(re.escape(pattern[index]))
        elif char == "*":
            if index + 1 < length and pattern[index + 1] == "*":
                index += 1
                if index + 1 < length and pattern[index + 1] == "/":
                    index += 1
                    pieces.append("(?:.*/)?")
                else:
                    pieces.append(".*")
            else:
                pieces.append("[^/]*")
        elif char == "?":
            pieces.append("[^/]")
        elif char == "[":
            closing = pattern.find("]", index + 1)
            if closing <= index + 1:
                pieces.append(r"\[")
            else:
                content = pattern[index + 1 : closing]
                if content.startswith("!"):
                    content = content[1:]
                    if not content:
                        pieces.append(r"\[")
                        index += 1
                        continue
                    content = "^" + content
                elif content.startswith("^"):
                    content = "\\" + content
                content = content.replace("\\", r"\\")
                pieces.append("[" + content + "]")
                index = closing
        else:
            pieces.append(re.escape(char))
        index += 1
    pieces.append("$")
    return "".join(pieces)


def _relative_to_base(path: str, base_path: str) -> str | None:
    if not base_path:
        return path
    if path == base_path:
        return ""
    prefix = base_path + "/"
    if not path.startswith(prefix):
        return None
    return path[len(prefix) :]


def _trim_unescaped_trailing_spaces(value: str) -> str:
    end = len(value)
    while end > 0 and value[end - 1] == " ":
        backslashes = 0
        cursor = end - 2
        while cursor >= 0 and value[cursor] == "\\":
            backslashes += 1
            cursor -= 1
        if backslashes % 2 == 1:
            break
        end -= 1
    return value[:end]
