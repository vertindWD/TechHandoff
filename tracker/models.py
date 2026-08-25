from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value if str(item).strip())


@dataclass(frozen=True)
class Project:
    project_id: str
    name: str
    repo_path: str = ""
    github_owner: str = ""
    github_repo: str = ""
    github_ref: str = "main"
    aliases: tuple[str, ...] = ()
    default_branch: str = "main"
    document_folder_token: str = ""
    owners: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = (
        ".env",
        ".env.*",
        "secrets",
        "secrets/*",
        "production",
        "production/*",
    )
    test_commands: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        project_id = str(data.get("project_id") or data.get("id") or "").strip()
        name = str(data.get("name") or "").strip()
        repo_path = str(data.get("repo_path") or data.get("repository") or "").strip()
        github_owner = str(data.get("github_owner") or "").strip()
        github_repo = str(data.get("github_repo") or "").strip()
        github_repository = str(data.get("github_repository") or "").strip()
        if github_repository and "/" in github_repository:
            github_owner, github_repo = github_repository.split("/", 1)
        if not project_id or not name:
            raise ValueError("project_id 和 name 不能为空")
        if not repo_path and not (github_owner and github_repo):
            raise ValueError("repo_path 或 github_owner + github_repo 至少配置一种")
        return cls(
            project_id=project_id,
            name=name,
            repo_path=repo_path,
            github_owner=github_owner,
            github_repo=github_repo.removesuffix(".git"),
            github_ref=str(data.get("github_ref") or data.get("default_branch") or "main"),
            aliases=_strings(data.get("aliases")),
            default_branch=str(data.get("default_branch") or "main"),
            document_folder_token=str(data.get("document_folder_token") or ""),
            owners=_strings(data.get("owners")),
            allowed_paths=_strings(data.get("allowed_paths")),
            forbidden_paths=_strings(data.get("forbidden_paths"))
            or cls.__dataclass_fields__["forbidden_paths"].default,
            test_commands=_strings(data.get("test_commands")),
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for key in (
            "aliases",
            "owners",
            "allowed_paths",
            "forbidden_paths",
            "test_commands",
        ):
            result[key] = list(result[key])
        return result

    @property
    def uses_github(self) -> bool:
        return bool(self.github_owner and self.github_repo)

    @property
    def github_full_name(self) -> str:
        return f"{self.github_owner}/{self.github_repo}" if self.uses_github else ""


@dataclass(frozen=True)
class Requirement:
    business_goal: str
    requested_changes: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    unknowns: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "business_goal": self.business_goal,
            "requested_changes": list(self.requested_changes),
            "acceptance_criteria": list(self.acceptance_criteria),
            "unknowns": list(self.unknowns),
        }


@dataclass(frozen=True)
class Evidence:
    path: str
    line_start: int
    line_end: int
    excerpt: str
    score: float
    matched_terms: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["matched_terms"] = list(self.matched_terms)
        result["symbols"] = list(self.symbols)
        return result


@dataclass(frozen=True)
class ChangeRecommendation:
    path: str
    line_start: int
    line_end: int
    symbol: str
    instruction: str
    confidence: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RepositorySnapshot:
    repo_path: str
    version: str
    file_count: int
    skipped_file_count: int


@dataclass
class Proposal:
    proposal_id: str
    project_id: str
    project_name: str
    repository_version: str
    generated_at: str
    source_label: str
    requirement: Requirement
    evidence: tuple[Evidence, ...]
    markdown: str
    recommendations: tuple[ChangeRecommendation, ...] = ()
    suggested_tests: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    analysis_steps: tuple[str, ...] = ()
    status: str = "draft"
    output_path: str = ""
    feishu_document_id: str = ""
    feishu_document_url: str = ""

    def to_dict(self, include_markdown: bool = True) -> dict[str, Any]:
        result = {
            "proposal_id": self.proposal_id,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "repository_version": self.repository_version,
            "generated_at": self.generated_at,
            "source_label": self.source_label,
            "requirement": self.requirement.to_dict(),
            "evidence": [item.to_dict() for item in self.evidence],
            "recommendations": [item.to_dict() for item in self.recommendations],
            "suggested_tests": list(self.suggested_tests),
            "risks": list(self.risks),
            "analysis_steps": list(self.analysis_steps),
            "status": self.status,
            "output_path": self.output_path,
            "feishu_document_id": self.feishu_document_id,
            "feishu_document_url": self.feishu_document_url,
        }
        if include_markdown:
            result["markdown"] = self.markdown
        return result


@dataclass
class Job:
    job_id: str
    status: str
    project_selector: str
    source_label: str
    proposal_id: str = ""
    error: str = ""
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
