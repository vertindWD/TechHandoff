from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .models import Project, RepositorySnapshot
from .repository import RepositoryReader, SourceFile, TEXT_EXTENSIONS, TEXT_FILENAMES
from .store import Store


class GitHubAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubCommit:
    commit_sha: str
    tree_sha: str


@dataclass(frozen=True)
class GitHubTreeFile:
    path: str
    blob_sha: str
    size: int


class GitHubClient:
    def __init__(
        self,
        api_url: str,
        token: str = "",
        api_version: str = "2026-03-10",
        timeout: int = 60,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.api_version = api_version
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.api_version,
            "User-Agent": "project-tracker-agent/0.3",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request_bytes(self, path: str, max_bytes: int = 120 * 1024 * 1024) -> bytes:
        request = urllib.request.Request(f"{self.api_url}{path}", headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = response.read(max_bytes + 1)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise GitHubAPIError(f"GitHub HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise GitHubAPIError(f"GitHub 请求失败：{exc}") from exc
        if len(data) > max_bytes:
            raise GitHubAPIError(f"GitHub 响应超过安全上限 {max_bytes} bytes")
        return data

    def _request_json(self, path: str) -> dict[str, Any]:
        try:
            value = json.loads(self._request_bytes(path).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubAPIError("GitHub 返回了无法解析的 JSON") from exc
        if not isinstance(value, dict):
            raise GitHubAPIError("GitHub 返回值不是 JSON 对象")
        return value

    @staticmethod
    def _repo_path(owner: str, repo: str) -> str:
        return f"/repos/{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(repo, safe='')}"

    def get_commit(self, owner: str, repo: str, ref: str) -> GitHubCommit:
        encoded_ref = urllib.parse.quote(ref, safe="")
        data = self._request_json(f"{self._repo_path(owner, repo)}/commits/{encoded_ref}")
        commit_sha = str(data.get("sha") or "")
        tree_sha = str(((data.get("commit") or {}).get("tree") or {}).get("sha") or "")
        if not commit_sha or not tree_sha:
            raise GitHubAPIError("GitHub commit 响应缺少 commit/tree SHA")
        return GitHubCommit(commit_sha=commit_sha, tree_sha=tree_sha)

    def get_repository(self, owner: str, repo: str) -> dict[str, Any]:
        data = self._request_json(self._repo_path(owner, repo))
        full_name = str(data.get("full_name") or "")
        default_branch = str(data.get("default_branch") or "")
        if not full_name or not default_branch:
            raise GitHubAPIError("GitHub 仓库信息缺少 full_name 或 default_branch")
        return {
            "name": str(data.get("name") or repo),
            "full_name": full_name,
            "default_branch": default_branch,
            "private": bool(data.get("private", False)),
            "html_url": str(data.get("html_url") or f"https://github.com/{full_name}"),
        }

    def get_tree(self, owner: str, repo: str, tree_sha: str) -> tuple[GitHubTreeFile, ...]:
        encoded_sha = urllib.parse.quote(tree_sha, safe="")
        data = self._request_json(f"{self._repo_path(owner, repo)}/git/trees/{encoded_sha}?recursive=1")
        if data.get("truncated"):
            raise GitHubAPIError(
                "GitHub 递归 Tree 被截断；为避免静默漏文件，本次同步已停止。请拆分仓库或实现分层 Tree 同步。"
            )
        files: list[GitHubTreeFile] = []
        for item in data.get("tree") or []:
            if item.get("type") != "blob":
                continue
            path = str(item.get("path") or "")
            sha = str(item.get("sha") or "")
            if path and sha:
                files.append(GitHubTreeFile(path=path, blob_sha=sha, size=int(item.get("size") or 0)))
        return tuple(files)

    def get_blob(self, owner: str, repo: str, blob_sha: str) -> bytes:
        encoded_sha = urllib.parse.quote(blob_sha, safe="")
        data = self._request_json(f"{self._repo_path(owner, repo)}/git/blobs/{encoded_sha}")
        if data.get("encoding") != "base64":
            raise GitHubAPIError("GitHub blob 不是 base64 编码")
        try:
            return base64.b64decode(str(data.get("content") or ""), validate=False)
        except ValueError as exc:
            raise GitHubAPIError("GitHub blob base64 解码失败") from exc

    def download_tarball(self, owner: str, repo: str, commit_sha: str) -> bytes:
        encoded_sha = urllib.parse.quote(commit_sha, safe="")
        return self._request_bytes(f"{self._repo_path(owner, repo)}/tarball/{encoded_sha}")


def verify_webhook_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    if not secret or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


class GitHubRepositorySyncer:
    def __init__(
        self,
        client: GitHubClient,
        store: Store,
        reader: RepositoryReader,
        full_sync_threshold: int = 100,
    ) -> None:
        self.client = client
        self.store = store
        self.reader = reader
        self.full_sync_threshold = full_sync_threshold

    def sync(
        self,
        project: Project,
        target_commit_sha: str = "",
        force_full: bool = False,
    ) -> dict[str, Any]:
        if not project.uses_github:
            raise ValueError("该项目没有配置 GitHub 仓库")
        commit = self.client.get_commit(
            project.github_owner,
            project.github_repo,
            target_commit_sha or project.github_ref,
        )
        previous_snapshot = self.store.get_repository_snapshot(project.project_id)
        if previous_snapshot and previous_snapshot["commit_sha"] == commit.commit_sha and not force_full:
            return {
                "project_id": project.project_id,
                "github_repository": project.github_full_name,
                "commit_sha": commit.commit_sha,
                "changed": 0,
                "deleted": 0,
                "file_count": int(previous_snapshot["file_count"]),
                "mode": "unchanged",
            }

        tree = self.client.get_tree(project.github_owner, project.github_repo, commit.tree_sha)
        eligible: dict[str, GitHubTreeFile] = {}
        skipped = 0
        for item in tree:
            if not self._eligible(project, item.path, item.size):
                skipped += 1
                continue
            if len(eligible) >= self.reader.max_files:
                skipped += 1
                continue
            eligible[item.path] = item

        old_map = self.store.repository_blob_map(project.project_id)
        changed_paths = sorted(
            path for path, item in eligible.items() if old_map.get(path) != item.blob_sha
        )
        deleted_paths = sorted(path for path in old_map if path not in eligible)
        full_sync = force_full or not old_map or len(changed_paths) > self.full_sync_threshold

        if full_sync:
            records = self._records_from_tarball(project, commit.commit_sha, eligible)
            self.store.replace_repository_files(project.project_id, records)
            mode = "full"
        else:
            records = []
            rejected_changed: list[str] = []
            for path in changed_paths:
                tree_file = eligible[path]
                raw = self.client.get_blob(project.github_owner, project.github_repo, tree_file.blob_sha)
                record = self._record(path, tree_file.blob_sha, raw)
                if record:
                    records.append(record)
                else:
                    rejected_changed.append(path)
            self.store.apply_repository_changes(
                project.project_id,
                records,
                [*deleted_paths, *rejected_changed],
            )
            mode = "incremental"

        file_count = len(self.store.repository_blob_map(project.project_id))
        self.store.save_repository_snapshot(
            project.project_id,
            commit.commit_sha,
            commit.tree_sha,
            file_count,
            skipped,
        )
        stale_proposals: list[str] = []
        for proposal in self.store.list_proposals(project.project_id):
            if proposal.get("repository_version") != commit.commit_sha and proposal.get("status") != "stale":
                proposal_id = str(proposal.get("proposal_id") or "")
                if proposal_id:
                    self.store.update_proposal_status(proposal_id, "stale")
                    stale_proposals.append(proposal_id)
        stale_memory = self.store.mark_versioned_memory_stale(project.project_id, commit.commit_sha)
        return {
            "project_id": project.project_id,
            "github_repository": project.github_full_name,
            "commit_sha": commit.commit_sha,
            "changed": len(changed_paths),
            "deleted": len(deleted_paths),
            "file_count": file_count,
            "skipped_file_count": skipped,
            "mode": mode,
            "stale_proposal_ids": stale_proposals,
            "stale_memory_count": stale_memory,
        }

    def cached_sources(self, project: Project) -> tuple[RepositorySnapshot, tuple[SourceFile, ...]]:
        snapshot = self.store.get_repository_snapshot(project.project_id)
        if not snapshot:
            raise GitHubAPIError("GitHub 仓库尚未同步")
        rows = self.store.list_repository_files(project.project_id)
        return (
            RepositorySnapshot(
                repo_path=f"github:{project.github_full_name}",
                version=str(snapshot["commit_sha"]),
                file_count=int(snapshot["file_count"]),
                skipped_file_count=int(snapshot["skipped_file_count"]),
            ),
            tuple(SourceFile(path=str(row["path"]), content=str(row["content"])) for row in rows),
        )

    def _eligible(self, project: Project, path: str, size: int) -> bool:
        if size > self.reader.max_file_bytes:
            return False
        name = path.rsplit("/", 1)[-1]
        suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if suffix not in TEXT_EXTENSIONS and name not in TEXT_FILENAMES:
            return False
        return self.reader.allowed_by_project(path, project)

    def _records_from_tarball(
        self,
        project: Project,
        commit_sha: str,
        eligible: dict[str, GitHubTreeFile],
    ) -> list[dict[str, Any]]:
        archive = self.client.download_tarball(project.github_owner, project.github_repo, commit_sha)
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        try:
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tar:
                for member in tar:
                    if not member.isfile() or member.issym() or member.islnk():
                        continue
                    parts = member.name.split("/", 1)
                    if len(parts) != 2:
                        continue
                    relative = parts[1]
                    tree_file = eligible.get(relative)
                    if not tree_file or member.size > self.reader.max_file_bytes:
                        continue
                    handle = tar.extractfile(member)
                    raw = handle.read(self.reader.max_file_bytes + 1) if handle else b""
                    record = self._record(relative, tree_file.blob_sha, raw)
                    if record:
                        records.append(record)
                        seen.add(relative)
                    if len(records) >= self.reader.max_files:
                        break
        except (tarfile.TarError, OSError) as exc:
            raise GitHubAPIError(f"GitHub tarball 解析失败：{exc}") from exc
        missing = set(eligible) - seen
        if missing and len(records) < min(len(eligible), self.reader.max_files):
            raise GitHubAPIError(f"GitHub tarball 缺少 {len(missing)} 个应索引文件，已停止替换旧索引")
        return records

    def _record(self, path: str, blob_sha: str, raw: bytes) -> dict[str, Any] | None:
        if len(raw) > self.reader.max_file_bytes or b"\x00" in raw:
            return None
        content = raw.decode("utf-8", errors="replace")
        return {
            "path": path,
            "blob_sha": blob_sha,
            "size": len(raw),
            "content": content,
        }
