from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import Settings
from .models import Project
from .server import run_server
from .service import TrackerService


def _settings() -> Settings:
    return Settings.from_env()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TechHandoff")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="初始化数据库并导入 PROJECTS_FILE")
    sub.add_parser("list", help="列出已注册项目")

    register = sub.add_parser("register", help="注册或更新项目")
    register.add_argument("--id", required=True, dest="project_id")
    register.add_argument("--name", required=True)
    source = register.add_mutually_exclusive_group(required=True)
    source.add_argument("--repo", dest="repo_path", help="本地只读仓库路径")
    source.add_argument("--github", help="GitHub owner/repo")
    register.add_argument("--github-ref", default="main")
    register.add_argument("--alias", action="append", default=[])
    register.add_argument("--allowed-path", action="append", default=[])
    register.add_argument("--owner", action="append", default=[])
    register.add_argument("--test-command", action="append", default=[])
    register.add_argument("--document-folder-token", default="")

    generate = sub.add_parser("generate", help="从会议纪要生成技术方案")
    generate.add_argument("--project", required=True)
    generate.add_argument("--notes-file", required=True)
    generate.add_argument("--publish-to-feishu", action="store_true")

    sync = sub.add_parser("sync-github", help="同步 GitHub 仓库到本地增量索引")
    sync.add_argument("--project", required=True)
    sync.add_argument("--commit-sha", default="")
    sync.add_argument("--force-full", action="store_true")

    context = sub.add_parser("context", help="从缓存索引和长期记忆检索受限上下文")
    context.add_argument("--project", required=True)
    context.add_argument("--query", required=True)
    context.add_argument("--max-chars", type=int, default=24000)

    remember = sub.add_parser("remember", help="保存经过确认的长期项目记忆")
    remember.add_argument("--project", required=True)
    remember.add_argument("--kind", required=True)
    remember.add_argument("--content", required=True)
    remember.add_argument("--source", default="CLI conversation")

    serve = sub.add_parser("serve", help="启动服务（含飞书长连接和本地 HTTP 管理接口）")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = _settings()
    if args.command == "serve":
        run_server(settings, args.host, args.port)
        return 0

    service = TrackerService(settings)
    if args.command == "init":
        count = service.bootstrap_projects()
        print(json.dumps({"status": "ok", "imported_projects": count}, ensure_ascii=False))
        return 0
    if args.command == "list":
        print(json.dumps([item.to_dict() for item in service.store.list_projects()], ensure_ascii=False, indent=2))
        return 0
    if args.command == "sync-github":
        result = service.sync_github_project(args.project, args.commit_sha, args.force_full)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "context":
        result = service.build_context(args.project, args.query, args.max_chars)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "remember":
        result = service.remember(args.project, args.kind, args.content, args.source)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "register":
        github_owner = ""
        github_repo = ""
        if args.github:
            if "/" not in args.github:
                raise SystemExit("--github 必须使用 owner/repo 格式")
            github_owner, github_repo = args.github.split("/", 1)
        project = Project(
            project_id=args.project_id,
            name=args.name,
            repo_path=str(Path(args.repo_path).expanduser().resolve()) if args.repo_path else "",
            github_owner=github_owner,
            github_repo=github_repo.removesuffix(".git"),
            github_ref=args.github_ref,
            aliases=tuple(args.alias),
            document_folder_token=args.document_folder_token,
            owners=tuple(args.owner),
            allowed_paths=tuple(args.allowed_path),
            test_commands=tuple(args.test_command),
        )
        service.register_project(project)
        print(json.dumps(project.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "generate":
        notes = Path(args.notes_file).read_text(encoding="utf-8")
        proposal = service.generate_proposal(
            args.project,
            notes,
            f"文件 {Path(args.notes_file).name}",
            publish_to_feishu=args.publish_to_feishu,
        )
        print(json.dumps(proposal.to_dict(include_markdown=False), ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
