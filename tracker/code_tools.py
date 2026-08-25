from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from .models import ChangeRecommendation, Evidence
from .repository import SourceFile

if TYPE_CHECKING:
    from .semantic import SerenaAnalyzer


class ReadOnlyToolError(ValueError):
    pass


@dataclass(frozen=True)
class ToolResult:
    content: str
    summary: str


class ReadOnlyRepositoryTools:
    """A closed, read-only tool surface over one immutable repository snapshot."""

    def __init__(
        self,
        files: tuple[SourceFile, ...],
        project_map: str,
        semantic: SerenaAnalyzer | None = None,
        max_output_chars: int = 12000,
    ) -> None:
        self.files = {item.path: item for item in files}
        self.project_map_markdown = project_map
        self.semantic = semantic
        self.max_output_chars = max_output_chars
        self.inspected_paths: set[str] = set()

    def execute(self, action: dict) -> ToolResult:
        name = str(action.get("action") or "").strip()
        if name in {"project_map", "project_understanding"}:
            return ToolResult(
                self._bounded(self.project_map_markdown),
                "读取版本化项目理解索引",
            )
        if name == "list_files":
            prefix = self._prefix(str(action.get("path_prefix") or ""))
            paths = [path for path in sorted(self.files) if path.startswith(prefix)]
            limit = self._limit(action.get("max_results"), 200, 500)
            shown = paths[:limit]
            content = "\n".join(shown) or "没有匹配文件"
            if len(paths) > len(shown):
                content += f"\n... 另有 {len(paths) - len(shown)} 个文件"
            return ToolResult(self._bounded(content), f"列出文件 {prefix or '/'}")
        if name == "read_file":
            path = self._path(str(action.get("path") or ""))
            source = self.files[path]
            lines = source.content.splitlines()
            start = max(1, self._integer(action.get("start_line"), 1))
            end = min(len(lines), self._integer(action.get("end_line"), start + 199))
            if end < start:
                raise ReadOnlyToolError("end_line 不能小于 start_line")
            if end - start > 239:
                end = start + 239
            self.inspected_paths.add(path)
            content = "\n".join(
                f"{index}: {lines[index - 1]}" for index in range(start, end + 1)
            )
            return ToolResult(self._bounded(content or "文件为空"), f"读取 {path}:{start}-{end}")
        if name == "search_code":
            pattern = str(action.get("pattern") or "").strip()
            if not 2 <= len(pattern) <= 200:
                raise ReadOnlyToolError("搜索表达式长度必须在 2 到 200 字符之间")
            regex = re.compile(re.escape(pattern), re.IGNORECASE)
            prefix = self._prefix(str(action.get("path_prefix") or ""))
            limit = self._limit(action.get("max_results"), 30, 80)
            return self._search(regex, prefix, limit, f"搜索代码 {pattern}")
        if name == "symbols_overview":
            semantic = self._semantic_or_raise()
            path = self._path(str(action.get("path") or ""))
            try:
                content = semantic.symbols_overview(
                    path,
                    depth=max(0, min(self._integer(action.get("depth"), 0), 2)),
                )
            except Exception as exc:
                raise ReadOnlyToolError(f"Serena 无法读取符号概览：{exc}") from exc
            self.inspected_paths.add(path)
            return ToolResult(self._bounded(content), f"语义读取 {path} 的符号概览")
        if name == "find_symbol":
            symbol = str(action.get("name") or "").strip()
            if not re.fullmatch(r"/?[A-Za-z_$][A-Za-z0-9_$/.:<>\[\]-]{0,239}", symbol):
                raise ReadOnlyToolError("符号名格式无效")
            if self.semantic:
                relative_path = self._prefix(str(action.get("path") or action.get("relative_path") or ""))
                try:
                    content = self.semantic.find_symbol(
                        symbol,
                        relative_path=relative_path,
                        include_body=bool(action.get("include_body", False)),
                        depth=max(0, min(self._integer(action.get("depth"), 0), 2)),
                    )
                except Exception as exc:
                    raise ReadOnlyToolError(f"Serena 查找符号失败：{exc}") from exc
                self._mark_paths_from_result(content)
                return ToolResult(self._bounded(content), f"语义查找符号 {symbol}")
            regex = re.compile(rf"(?<![A-Za-z0-9_$]){re.escape(symbol)}(?![A-Za-z0-9_$])")
            return self._search(regex, "", 50, f"查找符号 {symbol}")
        if name == "find_references":
            semantic = self._semantic_or_raise()
            symbol = str(action.get("name_path") or action.get("name") or "").strip()
            path = self._path(str(action.get("path") or action.get("relative_path") or ""))
            try:
                content = semantic.find_references(symbol, path)
            except Exception as exc:
                raise ReadOnlyToolError(f"Serena 查找引用失败：{exc}") from exc
            self.inspected_paths.add(path)
            self._mark_paths_from_result(content)
            return ToolResult(self._bounded(content), f"语义追踪 {path} 中 {symbol} 的引用")
        if name == "search_pattern":
            semantic = self._semantic_or_raise()
            pattern = str(action.get("pattern") or "").strip()
            relative_path = self._prefix(str(action.get("path") or action.get("relative_path") or ""))
            try:
                content = semantic.search_pattern(pattern, relative_path=relative_path)
            except Exception as exc:
                raise ReadOnlyToolError(f"Serena 搜索模式失败：{exc}") from exc
            self._mark_paths_from_result(content)
            return ToolResult(self._bounded(content), f"语义后备搜索 {pattern}")
        raise ReadOnlyToolError(f"不允许的工具：{name or '(empty)'}")

    def _semantic_or_raise(self) -> SerenaAnalyzer:
        if not self.semantic:
            raise ReadOnlyToolError("此代码快照未启用 Serena 语义分析")
        return self.semantic

    def _mark_paths_from_result(self, content: str) -> None:
        # Serena returns JSON containing repository-relative paths. Only accept
        # paths that are present in this immutable snapshot.
        for candidate in re.findall(r'"([^"\n]+)"', content):
            if candidate in self.files:
                self.inspected_paths.add(candidate)

    def _search(self, regex: re.Pattern[str], prefix: str, limit: int, summary: str) -> ToolResult:
        blocks: list[str] = []
        matches = 0
        for path, source in sorted(self.files.items()):
            if prefix and not path.startswith(prefix):
                continue
            line_numbers = [
                index
                for index, line in enumerate(source.content.splitlines())
                if regex.search(line)
            ]
            if not line_numbers:
                continue
            self.inspected_paths.add(path)
            remaining = max(1, limit - matches)
            chosen = line_numbers[:remaining]
            blocks.append(f"FILE {path}")
            blocks.append(self._ast_context(path, source.content, chosen))
            matches += len(chosen)
            if matches >= limit or sum(len(item) for item in blocks) >= self.max_output_chars:
                break
        content = "\n".join(blocks) if blocks else "没有匹配代码"
        return ToolResult(self._bounded(content), summary)

    @staticmethod
    def _ast_context(path: str, content: str, zero_based_lines: list[int]) -> str:
        try:
            from grep_ast import TreeContext

            context = TreeContext(
                path,
                content,
                color=False,
                line_number=True,
                parent_context=True,
                child_context=True,
                last_line=False,
                margin=1,
                mark_lois=True,
                loi_pad=1,
            )
            context.add_lines_of_interest(zero_based_lines)
            context.add_context()
            rendered = context.format().strip()
            if rendered:
                return rendered
        except Exception:
            pass
        lines = content.splitlines()
        selected: set[int] = set()
        for index in zero_based_lines:
            selected.update(range(max(0, index - 2), min(len(lines), index + 3)))
        return "\n".join(f"{index + 1}: {lines[index]}" for index in sorted(selected))

    def verify_recommendation(
        self,
        item: ChangeRecommendation,
    ) -> tuple[ChangeRecommendation | None, Evidence | None, str | None]:
        if item.path not in self.files:
            return None, None, f"模型引用了不存在的文件 `{item.path}`，已从建议中移除。"
        source = self.files[item.path]
        lines = source.content.splitlines()
        if not lines:
            return None, None, f"模型引用了空文件 `{item.path}`，已从建议中移除。"
        symbol_line = 0
        if item.symbol:
            pattern = re.compile(
                rf"(?<![A-Za-z0-9_$]){re.escape(item.symbol)}(?![A-Za-z0-9_$])"
            )
            symbol_line = next(
                (index for index, line in enumerate(lines, start=1) if pattern.search(line)),
                0,
            )
        start = symbol_line or max(1, min(item.line_start, len(lines)))
        end = max(start, min(item.line_end or start, len(lines)))
        inspected = item.path in self.inspected_paths
        verified = inspected and (not item.symbol or bool(symbol_line))
        confidence = "verified" if verified else "inferred"
        checked = ChangeRecommendation(
            path=item.path,
            line_start=start,
            line_end=end,
            symbol=item.symbol if symbol_line else "",
            instruction=item.instruction,
            confidence=confidence,
        )
        excerpt_start = max(1, start - 2)
        excerpt_end = min(len(lines), max(end, start + 2))
        excerpt = "\n".join(
            f"{index}: {lines[index - 1]}" for index in range(excerpt_start, excerpt_end + 1)
        )
        evidence = Evidence(
            path=item.path,
            line_start=start,
            line_end=end,
            excerpt=excerpt,
            score=1.0 if verified else 0.5,
            matched_terms=("agent-inspected",) if inspected else ("path-exists",),
            symbols=(item.symbol,) if symbol_line else (),
        )
        warning = None
        if not verified:
            warning = f"`{item.path}` 存在，但符号或调查过程不足，已标为 inferred。"
        return checked, evidence, warning

    def _path(self, value: str) -> str:
        normalized = PurePosixPath(value.replace("\\", "/")).as_posix().lstrip("./")
        if not normalized or normalized.startswith("/") or ".." in PurePosixPath(normalized).parts:
            raise ReadOnlyToolError("文件路径无效")
        if normalized not in self.files:
            raise ReadOnlyToolError(f"文件不存在：{normalized}")
        return normalized

    @staticmethod
    def _prefix(value: str) -> str:
        normalized = PurePosixPath(value.replace("\\", "/")).as_posix().lstrip("./")
        if normalized == ".":
            return ""
        if normalized.startswith("/") or ".." in PurePosixPath(normalized).parts:
            raise ReadOnlyToolError("路径前缀无效")
        return normalized

    @staticmethod
    def _integer(value: object, fallback: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    @classmethod
    def _limit(cls, value: object, fallback: int, maximum: int) -> int:
        return max(1, min(cls._integer(value, fallback), maximum))

    def _bounded(self, value: str) -> str:
        if len(value) <= self.max_output_chars:
            return value
        return value[: self.max_output_chars] + "\n... 工具输出已按字符预算截断"
