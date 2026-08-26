from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
from collections import OrderedDict
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from .repository import SourceFile

if TYPE_CHECKING:
    from serena.agent import SerenaAgent


SEMANTIC_INDEX_MARKER = "<!-- semantic-index:serena-2 -->"


class SemanticAnalysisError(RuntimeError):
    pass


class SerenaAnalyzer:
    """Read-only Serena/LSP facade for one immutable repository snapshot."""

    def __init__(self, agent: SerenaAgent, project_root: Path, language_servers: tuple[str, ...]) -> None:
        self._agent = agent
        self.project_root = project_root
        self.language_servers = language_servers
        self._lock = threading.RLock()

    def close(self) -> None:
        # SerenaAgent.shutdown() terminates the whole process. on_shutdown() only
        # releases the language servers owned by this in-process integration.
        with self._lock:
            self._agent.on_shutdown()

    def symbols_overview(self, relative_path: str, depth: int = 0) -> str:
        from serena.tools.symbol_tools import GetSymbolsOverviewTool

        path = self._safe_path(relative_path)
        tool = self._agent.get_tool(GetSymbolsOverviewTool)
        return self._execute(lambda: tool.apply(path, depth=max(0, min(depth, 2))))

    def find_symbol(
        self,
        name_path_pattern: str,
        relative_path: str = "",
        include_body: bool = False,
        depth: int = 0,
    ) -> str:
        from serena.tools.symbol_tools import FindSymbolTool

        pattern = name_path_pattern.strip()
        if not pattern or len(pattern) > 240:
            raise SemanticAnalysisError("符号名称不能为空且不能超过 240 字符")
        path = self._safe_path(relative_path, allow_empty=True)
        tool = self._agent.get_tool(FindSymbolTool)
        return self._execute(
            lambda: tool.apply(
                name_path_pattern=pattern,
                relative_path=path,
                include_body=bool(include_body),
                depth=max(0, min(depth, 2)),
                max_matches=80,
                max_answer_chars=20_000,
            )
        )

    def find_references(self, name_path: str, relative_path: str) -> str:
        from serena.tools.symbol_tools import FindReferencingSymbolsTool

        symbol = name_path.strip()
        if not symbol or len(symbol) > 240:
            raise SemanticAnalysisError("符号名称不能为空且不能超过 240 字符")
        path = self._safe_path(relative_path)
        tool = self._agent.get_tool(FindReferencingSymbolsTool)
        return self._execute(
            lambda: tool.apply(
                name_path=symbol,
                relative_path=path,
                max_answer_chars=20_000,
            )
        )

    def search_pattern(
        self,
        pattern: str,
        relative_path: str = "",
        code_only: bool = True,
    ) -> str:
        from serena.tools.file_tools import SearchForPatternTool

        value = pattern.strip()
        if not 2 <= len(value) <= 240:
            raise SemanticAnalysisError("搜索模式长度必须在 2 到 240 字符之间")
        path = self._safe_path(relative_path, allow_empty=True)
        tool = self._agent.get_tool(SearchForPatternTool)
        return self._execute(
            lambda: tool.apply(
                substring_pattern=value,
                relative_path=path,
                restrict_search_to_code_files=bool(code_only),
                max_answer_chars=20_000,
            )
        )

    def can_analyze(self, relative_path: str) -> bool:
        suffix = Path(relative_path).suffix.casefold()
        return any(suffix in _analysis_extensions(server) for server in self.language_servers)

    def build_project_understanding(
        self,
        project_name: str,
        project_id: str,
        repository_version: str,
        files: tuple[SourceFile, ...],
        max_chars: int,
    ) -> str:
        sections = [
            SEMANTIC_INDEX_MARKER,
            f"# {project_name} 项目理解索引",
            "",
            f"- project_id: `{project_id}`",
            f"- repository_version: `{repository_version}`",
            f"- source_digest: `{semantic_source_digest(files)}`",
            f"- engine: `Serena 1.7.0 + {', '.join(self.language_servers)}`",
            f"- indexed_text_files: {len(files)}",
            "- 说明：这是完整文件清单和语言服务器符号索引；它用于导航，最终建议仍需读取源码并检查引用。",
            "",
            "## 完整文件清单",
            "",
        ]
        sections.extend(f"- `{item.path}`" for item in files)
        sections.extend(["", "## 语义符号索引", ""])

        analyzed = 0
        failed: list[str] = []
        for source in files:
            if not self.can_analyze(source.path):
                continue
            try:
                overview = self.symbols_overview(source.path, depth=1)
            except Exception as exc:  # one unsupported/broken file must not discard the full index
                failed.append(f"{source.path}: {type(exc).__name__}")
                if analyzed == 0 and len(failed) >= 3:
                    break
                continue
            block = f"### `{source.path}`\n\n```json\n{overview}\n```\n"
            if sum(len(item) for item in sections) + len(block) > max_chars:
                failed.append(f"{source.path}: index character budget reached")
                break
            sections.append(block)
            analyzed += 1

        if analyzed == 0 and any(self.can_analyze(item.path) for item in files):
            raise SemanticAnalysisError(
                "语言服务器未能分析任何源码：" + "; ".join(failed[:3])
            )
        sections.extend(
            [
                "",
                "## 索引状态",
                "",
                f"- semantic_files: {analyzed}",
                f"- non_semantic_or_failed_files: {len(files) - analyzed}",
            ]
        )
        if failed:
            sections.append("- failures: " + "; ".join(failed[:30]))
        return "\n".join(sections).strip() + "\n"

    def _execute(self, task) -> str:
        try:
            with self._lock:
                return str(self._agent.execute_task(task, timeout=240))
        except Exception as exc:
            raise SemanticAnalysisError(str(exc)) from exc

    @staticmethod
    def _safe_path(value: str, allow_empty: bool = False) -> str:
        normalized = PurePosixPath(value.replace("\\", "/")).as_posix().lstrip("./")
        if allow_empty and normalized in ("", "."):
            return ""
        if not normalized or normalized.startswith("/") or ".." in PurePosixPath(normalized).parts:
            raise SemanticAnalysisError("仓库内路径无效")
        return normalized


class SerenaSemanticManager:
    """Materializes versioned snapshots and owns a small LRU of Serena sessions."""

    def __init__(
        self,
        data_dir: Path,
        gopls_path: Path | None = None,
        max_sessions: int = 2,
        go_binary_path: Path | None = None,
        max_languages: int = 6,
    ) -> None:
        self.data_dir = data_dir.resolve()
        self.workspace_dir = self.data_dir / "snapshots"
        self.metadata_dir = self.data_dir / "serena-projects"
        self.home_dir = self.data_dir / "home"
        self.cache_dir = self.data_dir / "cache"
        self.go_dir = self.data_dir / "go"
        for directory in (
            self.workspace_dir,
            self.metadata_dir,
            self.home_dir,
            self.cache_dir,
            self.go_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        os.environ["SERENA_HOME"] = str(self.home_dir)
        os.environ.setdefault("XDG_CACHE_HOME", str(self.cache_dir))
        os.environ.setdefault("GOPATH", str(self.go_dir))
        if gopls_path:
            resolved = gopls_path.resolve()
            if resolved.is_file():
                os.environ["PATH"] = str(resolved.parent) + os.pathsep + os.environ.get("PATH", "")
        go_binary = go_binary_path.resolve() if go_binary_path else _discover_go_toolchain()
        if go_binary and go_binary.is_file():
            os.environ["PATH"] = str(go_binary.parent) + os.pathsep + os.environ.get("PATH", "")
            os.environ.setdefault("GOTOOLCHAIN", "local")

        self.max_sessions = max(1, min(max_sessions, 8))
        self.max_languages = max(1, min(max_languages, 12))
        self._sessions: OrderedDict[str, SerenaAnalyzer] = OrderedDict()
        self._lock = threading.RLock()

    def prepare(
        self,
        project_id: str,
        repository_version: str,
        files: tuple[SourceFile, ...],
    ) -> SerenaAnalyzer:
        source_digest = semantic_source_digest(files)
        key = f"{project_id}\0{repository_version}\0{source_digest}"
        with self._lock:
            cached = self._sessions.get(key)
            if cached:
                self._sessions.move_to_end(key)
                return cached

            project_root = self._materialize(project_id, repository_version, source_digest, files)
            analyzer = self._create_analyzer(project_id, project_root, files)
            self._sessions[key] = analyzer
            while len(self._sessions) > self.max_sessions:
                _, old = self._sessions.popitem(last=False)
                old.close()
            return analyzer

    def close(self) -> None:
        with self._lock:
            for analyzer in self._sessions.values():
                analyzer.close()
            self._sessions.clear()

    def _materialize(
        self,
        project_id: str,
        repository_version: str,
        source_digest: str,
        files: tuple[SourceFile, ...],
    ) -> Path:
        digest = hashlib.sha256(
            f"{project_id}\0{repository_version}\0{source_digest}".encode()
        ).hexdigest()[:20]
        safe_project = re.sub(r"[^a-zA-Z0-9_-]+", "-", project_id).strip("-")[:40] or "project"
        target = self.workspace_dir / f"{safe_project}-{digest}"
        marker = target / ".tracker-snapshot.json"
        if marker.is_file():
            return target

        staging = self.workspace_dir / f".{target.name}.staging-{os.getpid()}-{threading.get_ident()}"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        try:
            for source in files:
                relative = PurePosixPath(source.path.replace("\\", "/"))
                if relative.is_absolute() or ".." in relative.parts:
                    continue
                output = staging.joinpath(*relative.parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(source.content, encoding="utf-8")
            marker_payload = {
                "project_id": project_id,
                "repository_version": repository_version,
                "source_digest": source_digest,
                "file_count": len(files),
            }
            (staging / ".tracker-snapshot.json").write_text(
                json.dumps(marker_payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            try:
                staging.rename(target)
            except FileExistsError:
                shutil.rmtree(staging)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return target

    def _create_analyzer(
        self,
        project_id: str,
        project_root: Path,
        files: tuple[SourceFile, ...],
    ) -> SerenaAnalyzer:
        try:
            from serena.agent import SerenaAgent
            from serena.config.serena_config import ProjectConfig, RegisteredProject, SerenaConfig
            from solidlsp.ls_config import LanguageServerId
        except ImportError as exc:
            raise SemanticAnalysisError("未安装 serena-agent") from exc

        language_servers = _detect_language_servers(
            files,
            LanguageServerId,
            max_languages=self.max_languages,
        )
        if not language_servers:
            raise SemanticAnalysisError("仓库中没有检测到当前已支持的语义语言")
        project_config = ProjectConfig(
            project_name=f"tracker-{project_id}-{project_root.name[-20:]}",
            language_servers=list(language_servers),
            read_only=True,
            ignored_paths=[".git", ".tracker-snapshot.json"],
        )
        registered = RegisteredProject(str(project_root), project_config)
        config = SerenaConfig(
            projects=[registered],
            base_modes=("interactive",),
            web_dashboard=False,
            web_dashboard_open_on_launch=False,
            gui_log_window=False,
            tool_timeout=240,
            default_max_tool_answer_chars=20_000,
            project_serena_folder_location=str(self.metadata_dir / "$projectFolderName" / ".serena"),
            trusted_project_path_patterns=[str(self.workspace_dir / "**")],
        )
        agent = SerenaAgent(project=str(project_root), serena_config=config)
        return SerenaAnalyzer(
            agent,
            project_root,
            tuple(server.value for server in language_servers),
        )


_LANGUAGE_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "ada": (".adb", ".ads"),
    "al": (".al",),
    "bash": (".sh", ".bash", ".zsh"),
    "bsl": (".bsl", ".os"),
    "clojure": (".clj", ".cljs", ".cljc"),
    "cpp": (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"),
    "crystal": (".cr",),
    "csharp": (".cs",),
    "cue": (".cue",),
    "dart": (".dart",),
    "elixir": (".ex", ".exs"),
    "elm": (".elm",),
    "erlang": (".erl", ".hrl"),
    "fortran": (".f", ".for", ".f90", ".f95", ".f03", ".f08"),
    "fsharp": (".fs", ".fsi", ".fsx"),
    "gdscript": (".gd",),
    "gleam": (".gleam",),
    "go": (".go",),
    "groovy": (".groovy",),
    "haskell": (".hs", ".lhs"),
    "haxe": (".hx",),
    "hlsl": (".hlsl", ".glsl", ".vert", ".frag", ".wgsl"),
    "html": (".html", ".htm"),
    "java": (".java",),
    "julia": (".jl",),
    "kotlin": (".kt",),
    "latex": (".tex", ".bib"),
    "lean4": (".lean",),
    "lua": (".lua",),
    "luau": (".luau",),
    "matlab": (".m",),
    "msl": (".msl",),
    "nextflow": (".nf",),
    "nix": (".nix",),
    "ocaml": (".ml", ".mli"),
    "pascal": (".pas", ".pp"),
    "perl": (".pl", ".pm"),
    "php": (".php",),
    "powershell": (".ps1", ".psm1", ".psd1"),
    "python": (".py", ".pyi"),
    "qml": (".qml",),
    "r": (".r",),
    "rego": (".rego",),
    "ruby": (".rb",),
    "rust": (".rs",),
    "scala": (".scala", ".sc"),
    "scss": (".scss", ".sass", ".css"),
    "solidity": (".sol",),
    "svelte": (".svelte",),
    "swift": (".swift",),
    "systemverilog": (".sv", ".svh", ".v", ".vh"),
    "terraform": (".tf", ".tfvars"),
    "toml": (".toml",),
    "typescript": (".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs"),
    "vue": (".vue",),
    "wolfram": (".wl", ".wls"),
    "yaml": (".yaml", ".yml"),
    "zig": (".zig",),
}

_FRAMEWORK_MANIFESTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "angular": ("typescript", ("angular.json",)),
    "deno": ("typescript", ("deno.json", "deno.jsonc")),
}

_FRAMEWORK_REPLACEMENTS: dict[str, str] = {
    "svelte": "typescript",
    "vue": "typescript",
}

_ANALYSIS_EXTENSION_OVERRIDES: dict[str, tuple[str, ...]] = {
    "angular": (".ts", ".tsx", ".js", ".jsx", ".html", ".css", ".scss"),
    "deno": _LANGUAGE_EXTENSIONS["typescript"],
    "svelte": (".svelte", *_LANGUAGE_EXTENSIONS["typescript"]),
    "vue": (".vue", *_LANGUAGE_EXTENSIONS["typescript"]),
}

_SECONDARY_LANGUAGES = {"html", "scss", "toml", "yaml"}


def _analysis_extensions(language: str) -> tuple[str, ...]:
    return _ANALYSIS_EXTENSION_OVERRIDES.get(language, _LANGUAGE_EXTENSIONS.get(language, ()))


def _detect_language_servers(
    files: tuple[SourceFile, ...],
    enum_type,
    max_languages: int = 6,
) -> tuple:
    counts: dict[str, int] = {}
    file_names = {Path(source.path).name.casefold() for source in files}
    for source in files:
        suffix = Path(source.path).suffix.casefold()
        for language, extensions in _LANGUAGE_EXTENSIONS.items():
            if suffix in extensions:
                counts[language] = counts.get(language, 0) + 1
                break

    for framework, (replaced, manifests) in _FRAMEWORK_MANIFESTS.items():
        if any(manifest in file_names for manifest in manifests) and replaced in counts:
            counts[framework] = counts.pop(replaced)
    for framework, replaced in _FRAMEWORK_REPLACEMENTS.items():
        if framework in counts and replaced in counts:
            counts[framework] += counts.pop(replaced)

    # Code languages are preferred over config/markup servers. The cap keeps a
    # polyglot monorepo from starting every detected server at once.
    selected = sorted(
        counts,
        key=lambda item: (item in _SECONDARY_LANGUAGES, -counts[item], item),
    )[: max(1, max_languages)]
    by_value = {item.value: item for item in enum_type}
    return tuple(by_value[item] for item in selected if item in by_value)


def semantic_source_digest(files: tuple[SourceFile, ...]) -> str:
    digest = hashlib.sha256()
    for source in files:
        digest.update(source.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(source.content.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()[:20]


def _discover_go_toolchain() -> Path | None:
    candidates: list[tuple[tuple[int, ...], Path]] = []
    module_root = Path.home() / "go" / "pkg" / "mod" / "golang.org"
    for binary in module_root.glob("toolchain@v*-go*.linux-*/bin/go"):
        match = re.search(r"-go([0-9.]+)\.linux-", binary.as_posix())
        if not match:
            continue
        version = tuple(int(part) for part in match.group(1).split("."))
        candidates.append((version, binary))
    return max(candidates, default=((), None), key=lambda item: item[0])[1]
