from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_env_file(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _paths(value: str) -> tuple[Path, ...]:
    return tuple(Path(item).expanduser().resolve() for item in value.split(":") if item.strip())


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _boolean(value: str) -> bool:
    return value.strip().casefold() not in {"0", "false", "no", "off", ""}


@dataclass(frozen=True)
class Settings:
    database_path: Path
    output_dir: Path
    projects_file: Path | None
    allowed_repo_roots: tuple[Path, ...]
    max_file_bytes: int
    max_files: int
    max_evidence: int
    model_base_url: str
    model_api_key: str
    model_name: str
    github_api_url: str
    github_api_version: str
    github_token: str
    github_webhook_secret: str
    github_full_sync_threshold: int
    feishu_base_url: str
    feishu_app_id: str
    feishu_app_secret: str
    feishu_verification_token: str
    feishu_tenant_domain: str
    feishu_allowed_chat_ids: tuple[str, ...]
    feishu_allowed_user_ids: tuple[str, ...]
    public_base_url: str
    agent_max_steps: int = 40
    feishu_bots_file: Path | None = None
    semantic_enabled: bool = False
    semantic_data_dir: Path | None = None
    semantic_max_index_chars: int = 250000
    semantic_max_sessions: int = 2
    semantic_max_languages: int = 6
    gopls_path: Path | None = None
    go_binary_path: Path | None = None
    model_json_retries: int = 2
    model_thinking: str = "auto"
    model_max_output_tokens: int = 4096

    @classmethod
    def from_env(cls, env_file: str | Path = ".env") -> "Settings":
        load_env_file(env_file)
        cwd = Path.cwd().resolve()
        roots = _paths(os.getenv("ALLOWED_REPO_ROOTS", str(cwd)))
        projects_value = os.getenv("PROJECTS_FILE", "config/projects.json").strip()
        bots_value = os.getenv("FEISHU_BOTS_FILE", "config/feishu-bots.json").strip()
        return cls(
            database_path=Path(os.getenv("DATABASE_PATH", "data/tracker.db")).resolve(),
            output_dir=Path(os.getenv("OUTPUT_DIR", "data/proposals")).resolve(),
            projects_file=Path(projects_value).resolve() if projects_value else None,
            allowed_repo_roots=roots,
            max_file_bytes=int(os.getenv("MAX_FILE_BYTES", "524288")),
            max_files=int(os.getenv("MAX_FILES", "5000")),
            max_evidence=int(os.getenv("MAX_EVIDENCE", "12")),
            model_base_url=os.getenv("MODEL_BASE_URL", "").rstrip("/"),
            model_api_key=os.getenv("MODEL_API_KEY", ""),
            model_name=os.getenv("MODEL_NAME", ""),
            github_api_url=os.getenv("GITHUB_API_URL", "https://api.github.com").rstrip("/"),
            github_api_version=os.getenv("GITHUB_API_VERSION", "2026-03-10"),
            github_token=os.getenv("GITHUB_TOKEN", ""),
            github_webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET", ""),
            github_full_sync_threshold=int(os.getenv("GITHUB_FULL_SYNC_THRESHOLD", "100")),
            feishu_base_url=os.getenv("FEISHU_BASE_URL", "https://open.feishu.cn/open-apis").rstrip("/"),
            feishu_app_id=os.getenv("FEISHU_APP_ID", ""),
            feishu_app_secret=os.getenv("FEISHU_APP_SECRET", ""),
            feishu_verification_token=os.getenv("FEISHU_VERIFICATION_TOKEN", ""),
            feishu_tenant_domain=os.getenv("FEISHU_TENANT_DOMAIN", "").rstrip("/"),
            feishu_allowed_chat_ids=_csv(os.getenv("FEISHU_ALLOWED_CHAT_IDS", "")),
            feishu_allowed_user_ids=_csv(os.getenv("FEISHU_ALLOWED_USER_IDS", "")),
            public_base_url=os.getenv("PUBLIC_BASE_URL", "").rstrip("/"),
            agent_max_steps=max(4, min(int(os.getenv("AGENT_MAX_STEPS", "40")), 100)),
            feishu_bots_file=Path(bots_value).resolve() if bots_value else None,
            semantic_enabled=_boolean(os.getenv("SEMANTIC_ENABLED", "true")),
            semantic_data_dir=Path(os.getenv("SEMANTIC_DATA_DIR", "data/semantic")).resolve(),
            semantic_max_index_chars=max(
                20000,
                min(int(os.getenv("SEMANTIC_MAX_INDEX_CHARS", "250000")), 2000000),
            ),
            semantic_max_sessions=max(
                1,
                min(int(os.getenv("SEMANTIC_MAX_SESSIONS", "2")), 8),
            ),
            semantic_max_languages=max(
                1,
                min(int(os.getenv("SEMANTIC_MAX_LANGUAGES", "6")), 12),
            ),
            gopls_path=Path(os.getenv("GOPLS_PATH", ".tools/bin/gopls")).resolve(),
            go_binary_path=(
                Path(os.environ["GO_BINARY_PATH"]).resolve()
                if os.getenv("GO_BINARY_PATH", "").strip()
                else None
            ),
            model_json_retries=max(0, min(int(os.getenv("MODEL_JSON_RETRIES", "2")), 4)),
            model_thinking=os.getenv("MODEL_THINKING", "auto").strip().casefold() or "auto",
            model_max_output_tokens=max(
                512,
                min(int(os.getenv("MODEL_MAX_OUTPUT_TOKENS", "4096")), 32768),
            ),
        )

    def ensure_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if self.semantic_enabled and self.semantic_data_dir:
            self.semantic_data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def model_enabled(self) -> bool:
        # LiteLLM can load provider-specific credentials such as
        # DASHSCOPE_API_KEY and DEEPSEEK_API_KEY from the environment.
        return bool(self.model_name)

    @property
    def feishu_enabled(self) -> bool:
        return bool(self.feishu_app_id and self.feishu_app_secret)
