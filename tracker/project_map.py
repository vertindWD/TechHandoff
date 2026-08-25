from __future__ import annotations

from pathlib import Path
from threading import Lock

from .models import Project, RepositorySnapshot
from .repository import SYMBOL_PATTERNS, SourceFile


_PACK_LOCK = Lock()
_PACK_CACHE = ""


def configure_tree_sitter(cache_dir: Path) -> None:
    """Keep downloaded parsers in project data instead of a user home directory."""
    global _PACK_CACHE
    target = str(cache_dir.resolve())
    with _PACK_LOCK:
        if _PACK_CACHE == target:
            return
        cache_dir.mkdir(parents=True, exist_ok=True)
        from tree_sitter_language_pack import PackConfig, configure

        configure(PackConfig(cache_dir=target))
        _PACK_CACHE = target


def _fallback_outline(source: SourceFile) -> list[str]:
    result: list[str] = []
    for line_number, line in enumerate(source.content.splitlines(), start=1):
        for pattern in SYMBOL_PATTERNS:
            match = pattern.search(line)
            if match:
                result.append(f"  - L{line_number} `{match.group(1)}`")
                break
    return result[:40]


def _structure_lines(items: list[object], depth: int = 1) -> list[str]:
    lines: list[str] = []
    for item in items:
        name = str(getattr(item, "name", "") or "").strip()
        signature = str(getattr(item, "signature", "") or "").strip()
        kind = str(getattr(item, "kind", "") or "symbol")
        span = getattr(item, "span", None)
        line_number = int(getattr(span, "start_line", 0)) + 1 if span else 1
        label = signature or name or kind
        label = " ".join(label.split())[:240]
        lines.append(f"{'  ' * depth}- L{line_number} `{label}` ({kind})")
        children = list(getattr(item, "children", ()) or ())
        lines.extend(_structure_lines(children, depth + 1))
    return lines


def build_project_map(
    project: Project,
    snapshot: RepositorySnapshot,
    files: tuple[SourceFile, ...],
    cache_dir: Path,
    max_chars: int = 60000,
    allow_parser_download: bool = False,
) -> str:
    """Build a persisted Aider-style repository map from paths and AST structure."""
    configure_tree_sitter(cache_dir)
    downloaded: set[str] = set()
    try:
        from tree_sitter_language_pack import (
            detect_language_from_path,
            download,
            downloaded_languages,
        )

        requested = sorted(
            {
                language
                for source in files
                if (language := detect_language_from_path(source.path))
            }
        )
        downloaded = set(downloaded_languages())
        missing = [language for language in requested if language not in downloaded]
        if allow_parser_download and missing:
            download(missing)
            downloaded = set(downloaded_languages())
    except Exception:
        downloaded = set()
    lines = [
        f"# {project.name} repository map",
        "",
        f"- repository_version: `{snapshot.version}`",
        f"- text_files: {snapshot.file_count}",
        "- purpose: read-only navigation context; not an implementation claim",
        "",
        "## Files and symbols",
        "",
    ]
    used_ast = 0
    fallback_files = 0
    for source in files:
        if sum(len(item) + 1 for item in lines) >= max_chars:
            lines.append("- ... map truncated by character budget")
            break
        lines.append(f"- `{source.path}`")
        outline: list[str] = []
        try:
            from tree_sitter_language_pack import (
                ProcessConfig,
                detect_language_from_path,
                process,
            )

            language = detect_language_from_path(source.path)
            if language and language in downloaded:
                result = process(
                    source.content,
                    ProcessConfig(
                        language=language,
                        structure=True,
                        imports=False,
                        exports=False,
                        comments=False,
                        docstrings=False,
                        symbols=False,
                        diagnostics=False,
                        max_source_bytes=1_000_000,
                        parse_timeout_ms=2000,
                    ),
                )
                outline = _structure_lines(list(result.get("structure", ())))
        except Exception:
            outline = []
        if outline:
            used_ast += 1
            lines.extend(outline[:80])
        else:
            fallback = _fallback_outline(source)
            if fallback:
                fallback_files += 1
                lines.extend(fallback)

    lines.extend(
        [
            "",
            "## Map quality",
            "",
            f"- AST outlined files: {used_ast}",
            f"- fallback outlined files: {fallback_files}",
            "- Exact recommendations must still be verified by reading current source in the agent loop.",
            "",
        ]
    )
    return "\n".join(lines)[:max_chars]
