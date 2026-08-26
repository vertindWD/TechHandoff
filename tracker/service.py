from __future__ import annotations

import json
import re
import hashlib
from pathlib import Path

from .config import Settings
from .feishu import FeishuClient
from .code_tools import ReadOnlyRepositoryTools
from .generator import build_manager_proposal, build_proposal
from .github import GitHubClient, GitHubRepositorySyncer
from .llm import ModelError, OpenAICompatibleModel
from .models import Project, Proposal
from .planning_agent import ReadOnlyPlanningAgent
from .project_map import build_project_map
from .repository import RepositoryReader, extract_search_terms
from .requirements import extract_requirement
from .semantic import (
    SEMANTIC_INDEX_MARKER,
    SemanticAnalysisError,
    SerenaAnalyzer,
    SerenaSemanticManager,
    semantic_source_digest,
)
from .store import Store


class ProjectNotFoundError(LookupError):
    pass


class TrackerService:
    def __init__(self, settings: Settings, store: Store | None = None) -> None:
        self.settings = settings
        self.settings.ensure_directories()
        self.store = store or Store(settings.database_path)
        self.reader = RepositoryReader(
            allowed_roots=settings.allowed_repo_roots,
            max_file_bytes=settings.max_file_bytes,
            max_files=settings.max_files,
        )
        self.github = GitHubRepositorySyncer(
            GitHubClient(
                settings.github_api_url,
                settings.github_token,
                settings.github_api_version,
            ),
            self.store,
            self.reader,
            settings.github_full_sync_threshold,
        )
        self.model = (
            OpenAICompatibleModel(
                settings.model_base_url,
                settings.model_api_key,
                settings.model_name,
                json_retries=settings.model_json_retries,
            )
            if settings.model_enabled
            else None
        )
        self.planning_agent = (
            ReadOnlyPlanningAgent(
                self.model,
                max_steps=self.settings.agent_max_steps,
                progress=lambda message: print(f"[只读调查] {message}", flush=True),
            )
            if self.model
            else None
        )
        self.semantic = (
            SerenaSemanticManager(
                settings.semantic_data_dir,
                settings.gopls_path,
                settings.semantic_max_sessions,
                settings.go_binary_path,
                settings.semantic_max_languages,
            )
            if settings.semantic_enabled and settings.semantic_data_dir
            else None
        )
        self.feishu = (
            FeishuClient(
                settings.feishu_base_url,
                settings.feishu_app_id,
                settings.feishu_app_secret,
                settings.feishu_tenant_domain,
            )
            if settings.feishu_enabled
            else None
        )

    def bootstrap_projects(self) -> int:
        path = self.settings.projects_file
        if not path or not path.is_file():
            return 0
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw.get("projects", []) if isinstance(raw, dict) else raw
        count = 0
        for item in items:
            data = dict(item)
            repo_path = Path(str(data.get("repo_path") or data.get("repository") or ""))
            if str(repo_path) and str(repo_path) != "." and not repo_path.is_absolute():
                data["repo_path"] = str((path.parent / repo_path).resolve())
            self.register_project(Project.from_dict(data))
            count += 1
        return count

    def register_project(self, project: Project) -> None:
        if project.uses_github:
            if not project.github_ref:
                raise ValueError("GitHub 项目必须配置 github_ref")
        else:
            self.reader.validate_root(project.repo_path)
        self.store.upsert_project(project)

    @staticmethod
    def parse_github_repository(value: str) -> tuple[str, str] | None:
        match = re.search(
            r"https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)",
            value,
            flags=re.IGNORECASE,
        )
        if not match:
            match = re.fullmatch(
                r"\s*([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?\s*",
                value,
                flags=re.IGNORECASE,
            )
        if not match:
            return None
        return match.group(1), match.group(2).removesuffix(".git")

    def register_github_repository(self, value: str, ref: str = "") -> Project:
        parsed = self.parse_github_repository(value)
        if not parsed:
            raise ValueError("GitHub 仓库必须使用 https://github.com/owner/repo 或 owner/repo")
        owner, repo = parsed
        existing = self.store.find_github_project(owner, repo)
        if existing:
            return existing
        metadata = self.github.client.get_repository(owner, repo)
        canonical_owner, canonical_repo = metadata["full_name"].split("/", 1)
        base_id = re.sub(
            r"[^a-z0-9_-]+",
            "-",
            f"{canonical_owner}-{canonical_repo}".casefold(),
        ).strip("-")[:64]
        project_id = base_id or f"github-{hashlib.sha256(metadata['full_name'].encode()).hexdigest()[:12]}"
        collision = self.store.find_project(project_id)
        if collision and collision.github_full_name.casefold() != metadata["full_name"].casefold():
            suffix = hashlib.sha256(metadata["full_name"].encode()).hexdigest()[:8]
            project_id = f"{project_id[:55]}-{suffix}"
        project = Project(
            project_id=project_id,
            name=str(metadata["name"]),
            github_owner=canonical_owner,
            github_repo=canonical_repo,
            github_ref=ref or str(metadata["default_branch"]),
            default_branch=str(metadata["default_branch"]),
            aliases=(metadata["full_name"], canonical_repo),
        )
        self.register_project(project)
        return project

    def identify_projects(self, text: str) -> list[Project]:
        normalized = text.casefold()
        github = self.parse_github_repository(text.strip())
        if github:
            project = self.store.find_github_project(*github)
            if project:
                return [project]
        matches: list[Project] = []
        for project in self.store.list_projects():
            candidates = (
                project.project_id,
                project.name,
                project.github_full_name,
                *project.aliases,
            )
            if any(
                candidate and len(candidate.strip()) >= 2 and candidate.casefold() in normalized
                for candidate in candidates
            ):
                matches.append(project)
        return matches

    def sync_github_project(
        self,
        project_selector: str,
        target_commit_sha: str = "",
        force_full: bool = False,
    ) -> dict[str, object]:
        project = self.store.find_project(project_selector)
        if project is None:
            raise ProjectNotFoundError(f"未注册项目：{project_selector}")
        result = self.github.sync(project, target_commit_sha, force_full)
        snapshot, files = self.github.cached_sources(project)
        project_map, semantic = self._prepare_code_intelligence(project, snapshot, files)
        return {
            **result,
            "project_understanding_characters": len(project_map),
            "code_intelligence": "serena" if semantic else "text-fallback",
        }

    def _sources(self, project: Project):
        if not project.uses_github:
            return self.reader.scan(project)
        if not self.store.get_repository_snapshot(project.project_id):
            self.github.sync(project)
        return self.github.cached_sources(project)

    def _prepare_code_intelligence(
        self,
        project: Project,
        snapshot,
        files,
    ) -> tuple[str, SerenaAnalyzer | None]:
        cached = self.store.get_project_map(project.project_id, snapshot.version)
        if self.semantic:
            try:
                analyzer = self.semantic.prepare(project.project_id, snapshot.version, files)
                digest_line = f"- source_digest: `{semantic_source_digest(files)}`"
                if (
                    cached.startswith(SEMANTIC_INDEX_MARKER)
                    and digest_line in cached
                    and "semantic_files: 0" not in cached
                ):
                    return cached, analyzer
                understanding = analyzer.build_project_understanding(
                    project.name,
                    project.project_id,
                    snapshot.version,
                    files,
                    self.settings.semantic_max_index_chars,
                )
                self.store.save_project_map(project.project_id, snapshot.version, understanding)
                print(
                    f"[语义分析] 已建立 project={project.project_id} "
                    f"version={snapshot.version} chars={len(understanding)}",
                    flush=True,
                )
                return understanding, analyzer
            except SemanticAnalysisError as exc:
                print(
                    f"[语义分析] Serena 不可用，降级为文本地图：{exc}",
                    flush=True,
                )
        if cached and "semantic_files: 0" not in cached:
            return cached, None
        project_map = build_project_map(
            project,
            snapshot,
            files,
            self.settings.database_path.parent / "tree-sitter",
            allow_parser_download=bool(self.model),
        )
        self.store.save_project_map(project.project_id, snapshot.version, project_map)
        return project_map, None

    def generate_proposal(
        self,
        project_selector: str,
        meeting_notes: str,
        source_label: str = "直接输入",
        publish_to_feishu: bool = False,
        feishu_client: FeishuClient | None = None,
    ) -> Proposal:
        project = self.store.find_project(project_selector)
        if project is None:
            raise ProjectNotFoundError(f"未注册项目：{project_selector}")
        requirement = extract_requirement(meeting_notes)
        snapshot, files = self._sources(project)
        memory = self.search_memory(project.project_id, meeting_notes, limit=8)
        if self.planning_agent:
            project_map, semantic = self._prepare_code_intelligence(project, snapshot, files)
            tools = ReadOnlyRepositoryTools(files, project_map, semantic=semantic)
            outcome = self.planning_agent.run(
                project,
                snapshot,
                meeting_notes,
                requirement,
                memory,
                tools,
            )
            proposal = build_manager_proposal(
                project,
                snapshot,
                outcome.requirement,
                outcome.recommendations,
                outcome.evidence,
                outcome.suggested_tests,
                outcome.risks,
                outcome.analysis_steps,
                source_label,
            )
        else:
            # Deterministic offline compatibility mode for local examples and tests.
            # Production plans require a model-driven investigation; this fallback is
            # intentionally not presented as agent-verified analysis.
            evidence = self.reader.find_evidence(files, meeting_notes, self.settings.max_evidence)
            proposal = build_proposal(project, snapshot, requirement, evidence, source_label, memory)
        project_output_id = hashlib.sha256(project.project_id.encode("utf-8")).hexdigest()[:16]
        output_path = self.settings.output_dir / f"{project_output_id}-latest.md"
        output_path.write_text(proposal.markdown, encoding="utf-8")
        proposal.output_path = str(output_path)
        if publish_to_feishu:
            publisher = feishu_client or self.feishu
            if not publisher:
                raise RuntimeError("未配置飞书凭证，无法发布飞书文档")
            document = publisher.create_document(
                f"{project.name}技术改动建议-{proposal.proposal_id[:8]}",
                project.document_folder_token,
            )
            publisher.append_markdown(document.document_id, proposal.markdown)
            proposal.feishu_document_id = document.document_id
            proposal.feishu_document_url = document.url
            proposal.status = "published"
        return proposal

    def search_memory(self, project_id: str, query: str, limit: int = 8) -> tuple[dict, ...]:
        query_terms = extract_search_terms(query)
        kind_priority = {
            "confirmed_decision": 7,
            "constraint": 6,
            "preference": 5,
            "requirement": 4,
            "open_question": 4,
        }
        best_by_content: dict[str, tuple[float, int, dict]] = {}
        for index, item in enumerate(self.store.list_memory(project_id)):
            if str(item.get("source") or "").startswith("proposal:"):
                continue
            content = str(item.get("content") or "").lower()
            score = sum(1.0 + min(len(term), 8) / 8.0 for term in query_terms if term.lower() in content)
            score += max(0.0, 0.5 - index * 0.01)
            priority = kind_priority.get(str(item.get("kind") or ""), 0)
            score += priority * 0.01
            if score > 0.0:
                key = " ".join(content.split())
                current = best_by_content.get(key)
                if current is None or priority > current[1] or (priority == current[1] and score > current[0]):
                    best_by_content[key] = (score, priority, item)
        ranked = [(score, item) for score, _, item in best_by_content.values()]
        ranked.sort(key=lambda value: -value[0])
        return tuple(item for _, item in ranked[:limit])

    def remember(
        self,
        project_selector: str,
        kind: str,
        content: str,
        source: str = "conversation",
    ) -> dict[str, str]:
        allowed_kinds = {
            "confirmed_decision",
            "constraint",
            "preference",
            "open_question",
            "requirement",
        }
        if kind not in allowed_kinds:
            raise ValueError(f"不支持的记忆类型：{kind}")
        content = content.strip()
        if not content or len(content) > 4000:
            raise ValueError("记忆内容长度必须在 1 到 4000 字符之间")
        project = self.store.find_project(project_selector)
        if project is None:
            raise ProjectNotFoundError(f"未注册项目：{project_selector}")
        snapshot, _ = self._sources(project)
        memory_id = self.store.add_memory(
            project.project_id,
            kind,
            content,
            source[:500],
            snapshot.version,
        )
        return {
            "memory_id": memory_id,
            "project_id": project.project_id,
            "kind": kind,
            "repository_version": snapshot.version,
        }

    def build_context(
        self,
        project_selector: str,
        query: str,
        max_chars: int = 24000,
    ) -> dict[str, object]:
        project = self.store.find_project(project_selector)
        if project is None:
            raise ProjectNotFoundError(f"未注册项目：{project_selector}")
        snapshot, files = self._sources(project)
        evidence = self.reader.find_evidence(files, query, self.settings.max_evidence)
        memory = self.search_memory(project.project_id, query, limit=10)
        used = 0
        selected_evidence: list[dict] = []
        for item in evidence:
            data = item.to_dict()
            cost = len(item.excerpt) + len(item.path) + 200
            if selected_evidence and used + cost > max_chars:
                break
            selected_evidence.append(data)
            used += cost
        selected_memory: list[dict] = []
        for item in memory:
            cost = len(str(item.get("content") or "")) + 100
            if used + cost > max_chars:
                break
            selected_memory.append(item)
            used += cost
        return {
            "project_id": project.project_id,
            "repository": project.github_full_name or project.repo_path,
            "repository_version": snapshot.version,
            "evidence": selected_evidence,
            "memory": selected_memory,
            "context_characters": used,
            "estimated_tokens_upper_bound": (used + 2) // 3,
        }

    def read_meeting_source(
        self,
        value: str,
        feishu_client: FeishuClient | None = None,
    ) -> tuple[str, str]:
        stripped = value.strip()
        looks_like_minute_token = bool(
            re.fullmatch(r"obcn[A-Za-z0-9_-]{8,}", stripped, flags=re.IGNORECASE)
        )
        looks_like_document_token = bool(
            re.fullmatch(r"(?:doxcn|doccn)[A-Za-z0-9_-]+", stripped)
        )
        if "/minutes/" in value.casefold() or looks_like_minute_token:
            reader = feishu_client or self.feishu
            if not reader:
                raise RuntimeError("读取飞书妙记需要配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET")
            minute_token = reader.extract_minute_token(value)
            return reader.read_minute_transcript(minute_token), f"飞书妙记 {minute_token}"
        if "/docx/" in value.casefold() or looks_like_document_token:
            reader = feishu_client or self.feishu
            if not reader:
                raise RuntimeError("读取飞书文档需要配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET")
            document_id = reader.extract_document_id(value)
            return reader.read_document_text(document_id), f"飞书文档 {document_id}"
        return value.strip(), "飞书消息正文"

    def refresh_project(self, project_selector: str) -> dict[str, object]:
        project = self.store.find_project(project_selector)
        if project is None:
            raise ProjectNotFoundError(f"未注册项目：{project_selector}")
        if project.uses_github:
            return self.sync_github_project(project_selector)
        snapshot, _ = self.reader.scan(project)
        return {
            "project_id": project.project_id,
            "repository_version": snapshot.version,
        }
