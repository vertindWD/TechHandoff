from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import Job, Project, Proposal


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class Store:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS proposals (
                    proposal_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    repository_version TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS received_events (
                    event_id TEXT PRIMARY KEY,
                    received_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS repository_files (
                    project_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    blob_sha TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, path)
                );
                CREATE INDEX IF NOT EXISTS idx_repository_files_project
                    ON repository_files(project_id);
                CREATE TABLE IF NOT EXISTS repository_snapshots (
                    project_id TEXT PRIMARY KEY,
                    commit_sha TEXT NOT NULL,
                    tree_sha TEXT NOT NULL,
                    file_count INTEGER NOT NULL,
                    skipped_file_count INTEGER NOT NULL,
                    indexed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_entries (
                    memory_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    stale INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_entries_project
                    ON memory_entries(project_id, stale, created_at);
                CREATE TABLE IF NOT EXISTS chat_project_bindings (
                    bot_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    bound_by TEXT NOT NULL,
                    binding_source TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(bot_id, chat_id)
                );
                CREATE INDEX IF NOT EXISTS idx_chat_project_bindings_project
                    ON chat_project_bindings(project_id);
                CREATE TABLE IF NOT EXISTS project_maps (
                    project_id TEXT NOT NULL,
                    repository_version TEXT NOT NULL,
                    markdown TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, repository_version)
                );
                CREATE INDEX IF NOT EXISTS idx_project_maps_project
                    ON project_maps(project_id, generated_at);
                """
            )

    def upsert_project(self, project: Project) -> None:
        payload = json.dumps(project.to_dict(), ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO projects(project_id, name, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    name=excluded.name,
                    payload=excluded.payload,
                    updated_at=excluded.updated_at
                """,
                (project.project_id, project.name, payload, _now()),
            )

    def list_projects(self) -> list[Project]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload FROM projects ORDER BY name").fetchall()
        return [Project.from_dict(json.loads(row["payload"])) for row in rows]

    def find_project(self, selector: str) -> Project | None:
        normalized = selector.strip().casefold()
        for project in self.list_projects():
            candidates = (project.project_id, project.name, *project.aliases)
            if normalized in {item.casefold() for item in candidates}:
                return project
        return None

    def find_github_project(self, owner: str, repo: str) -> Project | None:
        target = f"{owner}/{repo}".casefold()
        for project in self.list_projects():
            if project.github_full_name.casefold() == target:
                return project
        return None

    def bind_chat_project(
        self,
        bot_id: str,
        chat_id: str,
        project_id: str,
        bound_by: str,
        binding_source: str = "command",
    ) -> None:
        if self.find_project(project_id) is None:
            raise ValueError(f"无法绑定未注册项目：{project_id}")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO chat_project_bindings(
                    bot_id, chat_id, project_id, bound_by, binding_source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_id, chat_id) DO UPDATE SET
                    project_id=excluded.project_id,
                    bound_by=excluded.bound_by,
                    binding_source=excluded.binding_source,
                    updated_at=excluded.updated_at
                """,
                (bot_id, chat_id, project_id, bound_by, binding_source, _now()),
            )

    def get_chat_project_binding(self, bot_id: str, chat_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT bot_id, chat_id, project_id, bound_by, binding_source, updated_at
                FROM chat_project_bindings
                WHERE bot_id = ? AND chat_id = ?
                """,
                (bot_id, chat_id),
            ).fetchone()
        return dict(row) if row else None

    def unbind_chat_project(self, bot_id: str, chat_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM chat_project_bindings WHERE bot_id = ? AND chat_id = ?",
                (bot_id, chat_id),
            )
        return bool(cursor.rowcount)

    def save_proposal(self, proposal: Proposal) -> None:
        payload = json.dumps(proposal.to_dict(include_markdown=True), ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO proposals(proposal_id, project_id, repository_version, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(proposal_id) DO UPDATE SET payload=excluded.payload
                """,
                (
                    proposal.proposal_id,
                    proposal.project_id,
                    proposal.repository_version,
                    payload,
                    proposal.generated_at,
                ),
            )

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM proposals WHERE proposal_id = ?", (proposal_id,)
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_proposals(self, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM proposals WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def update_proposal_status(self, proposal_id: str, status: str) -> None:
        proposal = self.get_proposal(proposal_id)
        if not proposal:
            return
        proposal["status"] = status
        payload = json.dumps(proposal, ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                "UPDATE proposals SET payload = ? WHERE proposal_id = ?",
                (payload, proposal_id),
            )

    def create_job(self, job: Job) -> None:
        now = _now()
        job.created_at = job.created_at or now
        job.updated_at = job.updated_at or now
        payload = json.dumps(job.to_dict(), ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO jobs(job_id, status, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (job.job_id, job.status, payload, job.created_at, job.updated_at),
            )

    def update_job(self, job: Job) -> None:
        job.updated_at = _now()
        payload = json.dumps(job.to_dict(), ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET status = ?, payload = ?, updated_at = ? WHERE job_id = ?",
                (job.status, payload, job.updated_at, job.job_id),
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def mark_event_once(self, event_id: str) -> bool:
        if not event_id:
            return True
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO received_events(event_id, received_at) VALUES (?, ?)",
                    (event_id, _now()),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def get_repository_snapshot(self, project_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM repository_snapshots WHERE project_id = ?", (project_id,)
            ).fetchone()
        return dict(row) if row else None

    def save_repository_snapshot(
        self,
        project_id: str,
        commit_sha: str,
        tree_sha: str,
        file_count: int,
        skipped_file_count: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO repository_snapshots(
                    project_id, commit_sha, tree_sha, file_count, skipped_file_count, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    commit_sha=excluded.commit_sha,
                    tree_sha=excluded.tree_sha,
                    file_count=excluded.file_count,
                    skipped_file_count=excluded.skipped_file_count,
                    indexed_at=excluded.indexed_at
                """,
                (project_id, commit_sha, tree_sha, file_count, skipped_file_count, _now()),
            )

    def repository_blob_map(self, project_id: str) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT path, blob_sha FROM repository_files WHERE project_id = ?", (project_id,)
            ).fetchall()
        return {str(row["path"]): str(row["blob_sha"]) for row in rows}

    def list_repository_files(self, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT path, blob_sha, size, content FROM repository_files WHERE project_id = ? ORDER BY path",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def replace_repository_files(self, project_id: str, files: list[dict[str, Any]]) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM repository_files WHERE project_id = ?", (project_id,))
            connection.executemany(
                """
                INSERT INTO repository_files(project_id, path, blob_sha, size, content, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        project_id,
                        item["path"],
                        item["blob_sha"],
                        item["size"],
                        item["content"],
                        _now(),
                    )
                    for item in files
                ],
            )

    def apply_repository_changes(
        self,
        project_id: str,
        changed: list[dict[str, Any]],
        deleted_paths: list[str],
    ) -> None:
        with self._connect() as connection:
            connection.executemany(
                "DELETE FROM repository_files WHERE project_id = ? AND path = ?",
                [(project_id, path) for path in deleted_paths],
            )
            connection.executemany(
                """
                INSERT INTO repository_files(project_id, path, blob_sha, size, content, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, path) DO UPDATE SET
                    blob_sha=excluded.blob_sha,
                    size=excluded.size,
                    content=excluded.content,
                    updated_at=excluded.updated_at
                """,
                [
                    (
                        project_id,
                        item["path"],
                        item["blob_sha"],
                        item["size"],
                        item["content"],
                        _now(),
                    )
                    for item in changed
                ],
            )

    def save_project_map(
        self,
        project_id: str,
        repository_version: str,
        markdown: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO project_maps(project_id, repository_version, markdown, generated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(project_id, repository_version) DO UPDATE SET
                    markdown=excluded.markdown,
                    generated_at=excluded.generated_at
                """,
                (project_id, repository_version, markdown, _now()),
            )

    def get_project_map(self, project_id: str, repository_version: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT markdown FROM project_maps
                WHERE project_id = ? AND repository_version = ?
                """,
                (project_id, repository_version),
            ).fetchone()
        return str(row["markdown"]) if row else ""

    def add_memory(
        self,
        project_id: str,
        kind: str,
        content: str,
        source: str,
        source_version: str,
    ) -> str:
        import hashlib

        memory_id = hashlib.sha256(
            f"{project_id}\0{kind}\0{content}\0{source_version}".encode("utf-8")
        ).hexdigest()[:24]
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO memory_entries(
                    memory_id, project_id, kind, content, source, source_version, stale, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (memory_id, project_id, kind, content, source, source_version, _now()),
            )
        return memory_id

    def list_memory(self, project_id: str, include_stale: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM memory_entries WHERE project_id = ?"
        params: tuple[Any, ...] = (project_id,)
        if not include_stale:
            query += " AND stale = 0"
        query += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def mark_versioned_memory_stale(self, project_id: str, current_version: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE memory_entries SET stale = 1
                WHERE project_id = ? AND source_version != ? AND kind IN ('code_fact', 'implementation')
                """,
                (project_id, current_version),
            )
        return int(cursor.rowcount)
