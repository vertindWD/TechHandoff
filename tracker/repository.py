from __future__ import annotations

import fnmatch
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from .models import Evidence, Project, RepositorySnapshot


TEXT_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".graphql",
    ".h",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".kts",
    ".md",
    ".php",
    ".proto",
    ".py",
    ".rb",
    ".rs",
    ".scss",
    ".sh",
    ".sql",
    ".svelte",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}

TEXT_FILENAMES = {
    "CHANGELOG",
    "CONTRIBUTING",
    "Dockerfile",
    "Gemfile",
    "LICENSE",
    "Makefile",
    "Pipfile",
    "README",
    "build.gradle",
    "build.gradle.kts",
    "go.mod",
    "go.sum",
    "gradle.properties",
    "package-lock.json",
    "pnpm-lock.yaml",
    "requirements.txt",
    "settings.gradle",
    "settings.gradle.kts",
    "yarn.lock",
}

# Known binary artifacts are rejected before GitHub blob downloads. Files with
# unknown extensions remain eligible and are classified from their content so
# new or uncommon programming languages do not require a code change here.
BINARY_EXTENSIONS = {
    ".7z",
    ".a",
    ".apk",
    ".avi",
    ".bin",
    ".bmp",
    ".bz2",
    ".class",
    ".db",
    ".dll",
    ".dmg",
    ".doc",
    ".docx",
    ".eot",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp3",
    ".mp4",
    ".o",
    ".obj",
    ".otf",
    ".pdf",
    ".png",
    ".pyc",
    ".pyo",
    ".rar",
    ".so",
    ".sqlite",
    ".sqlite3",
    ".tar",
    ".tif",
    ".tiff",
    ".ttf",
    ".wav",
    ".wasm",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
    ".xls",
    ".xlsx",
    ".xz",
    ".zip",
}

SKIP_DIRECTORIES = {
    ".git",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".vscode",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "vendor",
    "venv",
}

STOP_TERMS = {
    "一个",
    "一下",
    "这个",
    "那个",
    "我们",
    "用户",
    "希望",
    "需要",
    "可以",
    "增加",
    "新增",
    "修改",
    "功能",
    "项目",
    "系统",
    "页面",
    "进行",
    "相关",
}

DOMAIN_TRANSLATIONS = {
    "订单": ("order",),
    "通知": ("notification", "notify"),
    "发送": ("send", "resend"),
    "重新发送": ("resend",),
    "详情": ("detail",),
    "按钮": ("button",),
    "用户": ("user",),
    "客户": ("customer", "client"),
    "权限": ("permission", "authorize", "role"),
    "失败": ("fail", "error"),
    "成功": ("success", "sent"),
    "登录": ("login", "signin", "auth"),
    "上传": ("upload",),
    "下载": ("download",),
    "删除": ("delete", "remove"),
    "搜索": ("search", "query"),
    "支付": ("payment", "pay"),
}

SYMBOL_PATTERNS = (
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)"),
    re.compile(r"^\s*class\s+([A-Za-z_]\w*)"),
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"),
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*="),
    re.compile(r"^\s*(?:public|private|protected)?\s*(?:static\s+)?[\w<>\[\], ?]+\s+([A-Za-z_]\w*)\s*\("),
)

SYMBOL_STOP_TERMS = {
    "Error",
    "Exception",
    "PermissionError",
    "ValueError",
    "else",
    "for",
    "if",
    "return",
    "switch",
    "while",
}


class RepositoryAccessError(ValueError):
    pass


@dataclass(frozen=True)
class SourceFile:
    path: str
    content: str


def is_candidate_text_path(path: str) -> bool:
    """Accept known and unknown source formats while rejecting obvious binaries."""
    name = Path(path).name
    suffix = Path(name).suffix.casefold()
    if name in TEXT_FILENAMES or suffix in TEXT_EXTENSIONS:
        return True
    return suffix not in BINARY_EXTENSIONS


def is_probably_text(raw: bytes) -> bool:
    """Content-based fallback for extensionless and uncommon source languages."""
    if not raw:
        return True
    sample = raw[:8192]
    if b"\x00" in sample:
        return False
    allowed_controls = {8, 9, 10, 12, 13}
    disallowed = sum(byte < 32 and byte not in allowed_controls or byte == 127 for byte in sample)
    return disallowed / len(sample) <= 0.05


def extract_search_terms(text: str) -> tuple[str, ...]:
    terms: set[str] = set()
    for chinese, translations in DOMAIN_TRANSLATIONS.items():
        if chinese in text:
            terms.update(translations)
    for word in re.findall(r"[A-Za-z_][A-Za-z0-9_-]{1,}", text):
        terms.add(word.lower())
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        if len(run) <= 8:
            terms.add(run)
        for size in (2, 3, 4):
            for index in range(0, len(run) - size + 1):
                token = run[index : index + size]
                if token not in STOP_TERMS:
                    terms.add(token)
    return tuple(sorted(terms, key=lambda item: (-len(item), item)))


class RepositoryReader:
    def __init__(
        self,
        allowed_roots: tuple[Path, ...],
        max_file_bytes: int = 524288,
        max_files: int = 5000,
    ) -> None:
        self.allowed_roots = tuple(root.resolve() for root in allowed_roots)
        self.max_file_bytes = max_file_bytes
        self.max_files = max_files

    def validate_root(self, repo_path: str) -> Path:
        root = Path(repo_path).expanduser().resolve()
        if not root.is_dir():
            raise RepositoryAccessError(f"代码仓库目录不存在：{root}")
        if not any(root == allowed or root.is_relative_to(allowed) for allowed in self.allowed_roots):
            allowed_text = ", ".join(str(item) for item in self.allowed_roots)
            raise RepositoryAccessError(f"仓库不在允许目录中：{root}；允许目录：{allowed_text}")
        return root

    def allowed_by_project(self, relative: str, project: Project) -> bool:
        parts = Path(relative).parts
        if any(part in SKIP_DIRECTORIES for part in parts):
            return False
        if project.allowed_paths:
            if not any(relative == prefix.rstrip("/") or relative.startswith(prefix.rstrip("/") + "/") for prefix in project.allowed_paths):
                return False
        for pattern in project.forbidden_paths:
            if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(Path(relative).name, pattern):
                return False
        return True

    def scan(self, project: Project) -> tuple[RepositorySnapshot, tuple[SourceFile, ...]]:
        root = self.validate_root(project.repo_path)
        files: list[SourceFile] = []
        skipped = 0
        digest = hashlib.sha256()
        candidates = sorted(path for path in root.rglob("*") if path.is_file() and not path.is_symlink())
        for path in candidates:
            relative = path.relative_to(root).as_posix()
            if not self.allowed_by_project(relative, project):
                skipped += 1
                continue
            if not is_candidate_text_path(relative):
                skipped += 1
                continue
            try:
                if path.stat().st_size > self.max_file_bytes:
                    skipped += 1
                    continue
                raw = path.read_bytes()
            except OSError:
                skipped += 1
                continue
            if not is_probably_text(raw):
                skipped += 1
                continue
            content = raw.decode("utf-8", errors="replace")
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(raw)
            files.append(SourceFile(path=relative, content=content))
            if len(files) >= self.max_files:
                skipped += max(0, len(candidates) - len(files))
                break
        version = self._git_head(root) or f"snapshot-{digest.hexdigest()[:12]}"
        return (
            RepositorySnapshot(
                repo_path=str(root),
                version=version,
                file_count=len(files),
                skipped_file_count=skipped,
            ),
            tuple(files),
        )

    @staticmethod
    def _git_head(root: Path) -> str:
        git_dir = root / ".git"
        if not git_dir.is_dir():
            return ""
        try:
            head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
            if not head.startswith("ref: "):
                return head[:40]
            ref = head[5:].strip()
            ref_path = git_dir / ref
            if ref_path.is_file():
                return ref_path.read_text(encoding="utf-8").strip()[:40]
            packed = git_dir / "packed-refs"
            if packed.is_file():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    if line.endswith(f" {ref}"):
                        return line.split(" ", 1)[0][:40]
        except OSError:
            return ""
        return ""

    def find_evidence(
        self,
        files: tuple[SourceFile, ...],
        query: str,
        limit: int = 12,
    ) -> tuple[Evidence, ...]:
        terms = extract_search_terms(query)
        scored: list[tuple[float, SourceFile, tuple[str, ...]]] = []
        for source in files:
            path_lower = source.path.lower()
            content_lower = source.content.lower()
            matched: list[str] = []
            score = 0.0
            for term in terms:
                term_lower = term.lower()
                path_count = path_lower.count(term_lower)
                content_count = content_lower.count(term_lower)
                if path_count or content_count:
                    matched.append(term)
                    score += path_count * 10.0
                    score += min(content_count, 8) * (1.0 + min(len(term), 8) / 8.0)
            if score:
                if source.path.startswith(("test", "tests", "spec")) or "/test" in source.path:
                    score += 1.5
                scored.append((score, source, tuple(matched)))
        scored.sort(key=lambda item: (-item[0], item[1].path))
        # A meeting usually names business concepts, while related tests and service
        # files often contain only English identifiers. Follow verified symbols from
        # the strongest direct hits for one bounded second hop.
        direct_paths = {item[1].path for item in scored[: min(8, limit)]}
        related_terms: set[str] = set()
        for score, source, matched in scored[: min(8, limit)]:
            seed = self._make_evidence(source, matched, score)
            related_terms.update(
                symbol for symbol in seed.symbols if len(symbol) >= 4 and symbol not in SYMBOL_STOP_TERMS
            )
        if related_terms:
            existing_paths = {item[1].path for item in scored}
            for source in files:
                if source.path in direct_paths:
                    continue
                matched_relations = tuple(
                    sorted(term for term in related_terms if term in source.content or term.lower() in source.path.lower())
                )
                if not matched_relations:
                    continue
                relation_score = 3.0 + min(6.0, len(matched_relations) * 1.5)
                if source.path.startswith(("test", "tests", "spec")) or "/test" in source.path:
                    relation_score += 2.0
                if source.path in existing_paths:
                    for index, (score, existing, matched) in enumerate(scored):
                        if existing.path == source.path:
                            scored[index] = (
                                score + relation_score,
                                existing,
                                tuple(dict.fromkeys((*matched, *matched_relations))),
                            )
                            break
                else:
                    scored.append((relation_score, source, matched_relations))
        scored.sort(key=lambda item: (-item[0], item[1].path))
        evidence: list[Evidence] = []
        for score, source, matched in scored[:limit]:
            evidence.append(self._make_evidence(source, matched, score))
        return tuple(evidence)

    @staticmethod
    def _make_evidence(source: SourceFile, matched: tuple[str, ...], score: float) -> Evidence:
        lines = source.content.splitlines()
        best_index = 0
        best_hits = -1
        lowered_terms = tuple(term.lower() for term in matched)
        for index, line in enumerate(lines):
            lower = line.lower()
            hits = sum(1 for term in lowered_terms if term in lower)
            if hits > best_hits:
                best_index = index
                best_hits = hits
        start = max(0, best_index - 2)
        end = min(len(lines), best_index + 3)
        excerpt = "\n".join(f"{index + 1}: {lines[index]}" for index in range(start, end))
        symbols: list[str] = []
        for line in lines[max(0, best_index - 20) : min(len(lines), best_index + 8)]:
            for pattern in SYMBOL_PATTERNS:
                match = pattern.search(line)
                if (
                    match
                    and match.group(1) not in SYMBOL_STOP_TERMS
                    and match.group(1) not in symbols
                ):
                    symbols.append(match.group(1))
        return Evidence(
            path=source.path,
            line_start=start + 1,
            line_end=end,
            excerpt=excerpt,
            score=round(score, 2),
            matched_terms=tuple(matched[:12]),
            symbols=tuple(symbols[:8]),
        )
