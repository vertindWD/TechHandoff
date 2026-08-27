from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from .bots import FeishuBotBinding, load_feishu_bots
from .config import Settings
from .feishu import FeishuClient
from .feishu_ws import FeishuLongConnectionManager
from .github import verify_webhook_signature
from .models import Job, Project
from .service import TrackerService


class TrackerApplication:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.service = TrackerService(settings)
        self.service.bootstrap_projects()
        bots = load_feishu_bots(settings.feishu_bots_file)
        self.feishu_bots = {bot.callback_key: bot for bot in bots}
        self.feishu_clients = {
            bot.bot_id: FeishuClient(
                settings.feishu_base_url,
                bot.app_id,
                bot.app_secret,
                bot.tenant_domain,
            )
            for bot in bots
        }
        for bot in bots:
            if bot.project_id and self.service.store.find_project(bot.project_id) is None:
                raise ValueError(
                    f"飞书机器人 {bot.bot_id} 绑定了未注册项目：{bot.project_id}"
                )
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="proposal")
        self.feishu_long_connections = FeishuLongConnectionManager(
            bots,
            self.handle_bound_feishu_message,
        )

    def submit(
        self,
        project_selector: str,
        notes: str,
        source_label: str,
        publish_to_feishu: bool,
        chat_id: str = "",
        feishu_bot_id: str = "",
    ) -> Job:
        job = Job(
            job_id=uuid4().hex,
            status="queued",
            project_selector=project_selector,
            source_label=source_label,
            metadata={
                "chat_id": chat_id,
                "publish_to_feishu": publish_to_feishu,
                "feishu_bot_id": feishu_bot_id,
            },
        )
        self.service.store.create_job(job)
        self.executor.submit(
            self._run_job,
            job,
            notes,
            publish_to_feishu,
            chat_id,
            feishu_bot_id,
        )
        return job

    def submit_github_sync(
        self,
        project_selector: str,
        target_commit_sha: str = "",
        force_full: bool = False,
        chat_id: str = "",
        feishu_bot_id: str = "",
    ) -> Job:
        job = Job(
            job_id=uuid4().hex,
            status="queued",
            project_selector=project_selector,
            source_label="GitHub sync",
            metadata={
                "job_type": "github_sync",
                "target_commit_sha": target_commit_sha,
                "force_full": force_full,
                "chat_id": chat_id,
                "feishu_bot_id": feishu_bot_id,
            },
        )
        self.service.store.create_job(job)
        self.executor.submit(self._run_github_sync, job, target_commit_sha, force_full)
        return job

    def _run_github_sync(self, job: Job, target_commit_sha: str, force_full: bool) -> None:
        job.status = "running"
        self.service.store.update_job(job)
        chat_id = str(job.metadata.get("chat_id") or "")
        feishu_bot_id = str(job.metadata.get("feishu_bot_id") or "")
        feishu = self.feishu_clients.get(feishu_bot_id) if feishu_bot_id else None
        try:
            result = self.service.sync_github_project(
                job.project_selector,
                target_commit_sha,
                force_full,
            )
            job.status = "completed"
            job.metadata["sync_result"] = result
            print(
                f"[GitHub] 项目 {job.project_selector} 同步完成，"
                f"版本 {result.get('commit_sha', '')}",
                flush=True,
            )
            if chat_id and feishu:
                try:
                    feishu.send_text(
                        chat_id,
                        f"项目代码同步完成：{result.get('github_repository', job.project_selector)}\n"
                        f"代码版本：{result.get('commit_sha', '')}\n"
                        "现在可以直接发送会议纪要生成技术方案。",
                    )
                except Exception as exc:
                    job.metadata["feishu_notify_error"] = str(exc)[:1000]
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)[:2000]
            print(f"[GitHub] 项目 {job.project_selector} 同步失败：{job.error}", flush=True)
            if chat_id and feishu:
                try:
                    feishu.send_text(chat_id, f"项目代码同步失败：{job.error}")
                except Exception:
                    pass
        finally:
            self.service.store.update_job(job)

    def _run_job(
        self,
        job: Job,
        notes: str,
        publish_to_feishu: bool,
        chat_id: str,
        feishu_bot_id: str,
    ) -> None:
        job.status = "running"
        self.service.store.update_job(job)
        print(
            f"[方案] 任务 {job.job_id[:8]} 开始，项目 {job.project_selector}",
            flush=True,
        )
        feishu = self.feishu_clients.get(feishu_bot_id) if feishu_bot_id else self.service.feishu
        try:
            proposal = self.service.generate_proposal(
                job.project_selector,
                notes,
                job.source_label,
                publish_to_feishu=publish_to_feishu,
                feishu_client=feishu,
            )
            job.status = "completed"
            job.proposal_id = proposal.proposal_id
            job.metadata["output_path"] = proposal.output_path
            job.metadata["feishu_document_url"] = proposal.feishu_document_url
            if chat_id and feishu:
                target = proposal.feishu_document_url or proposal.output_path
                unknown_count = len(proposal.requirement.unknowns)
                feishu.send_text(
                    chat_id,
                    f"技术方案已生成：{proposal.project_name}\n{target}\n"
                    f"代码版本：{proposal.repository_version}\n待确认问题：{unknown_count} 项",
                )
            print(
                f"[方案] 任务 {job.job_id[:8]} 完成，方案 {proposal.proposal_id}",
                flush=True,
            )
        except Exception as exc:  # background boundary: persist a safe error instead of losing the job
            job.status = "failed"
            job.error = str(exc)[:2000]
            if chat_id and feishu:
                try:
                    feishu.send_text(chat_id, f"技术方案生成失败：{job.error}")
                except Exception:
                    pass
            print(f"[方案] 任务 {job.job_id[:8]} 失败：{job.error}", flush=True)
        finally:
            self.service.store.update_job(job)

    def handle_feishu_event(
        self,
        payload: dict[str, Any],
        callback_key: str = "",
    ) -> dict[str, Any]:
        bot: FeishuBotBinding | None = None
        feishu = self.service.feishu
        if callback_key:
            bot = self.feishu_bots.get(callback_key)
            if bot is None:
                raise LookupError("未注册的飞书机器人回调")
            feishu = self.feishu_clients[bot.bot_id]
        header = payload.get("header") or {}
        received_app_id = str(header.get("app_id") or "")
        if bot and received_app_id and received_app_id != bot.app_id:
            raise PermissionError("飞书事件 App ID 与机器人绑定不匹配")
        expected = bot.verification_token if bot else self.settings.feishu_verification_token
        received = str(header.get("token") or payload.get("token") or "")
        if expected and received != expected:
            raise PermissionError("飞书事件校验 Token 不匹配")
        if "challenge" in payload:
            return {"challenge": payload["challenge"]}
        if header.get("event_type") != "im.message.receive_v1":
            return {"code": 0, "ignored": True}
        event = payload.get("event") or {}
        message = event.get("message") or {}
        chat_id = str(message.get("chat_id") or "")
        sender_id = str(((event.get("sender") or {}).get("sender_id") or {}).get("open_id") or "")
        allowed_chats = bot.allowed_chat_ids if bot else self.settings.feishu_allowed_chat_ids
        allowed_users = bot.allowed_user_ids if bot else self.settings.feishu_allowed_user_ids
        if not bot and not allowed_chats and not allowed_users:
            raise PermissionError("尚未配置 FEISHU_ALLOWED_CHAT_IDS 或 FEISHU_ALLOWED_USER_IDS")
        if (allowed_chats or allowed_users) and chat_id not in allowed_chats and sender_id not in allowed_users:
            raise PermissionError("当前飞书群或用户不在允许范围内")
        if message.get("message_type") != "text":
            return {"code": 0, "ignored": True}
        try:
            content = json.loads(message.get("content") or "{}")
        except json.JSONDecodeError:
            content = {}
        text = str(content.get("text") or "")
        text = re.sub(r"@_user_\d+", "", text).strip()
        if bot:
            return self.handle_bound_feishu_message(
                bot.callback_key,
                str(header.get("event_id") or ""),
                chat_id,
                sender_id,
                text,
            )

        event_id = str(header.get("event_id") or "")
        if not self.service.store.mark_event_once(f"feishu:legacy:{event_id}"):
            return {"code": 0, "duplicate": True}

        match = re.match(r"^(?:/)?方案\s+([^\s]+)\s+(.+)$", text, flags=re.DOTALL)
        if not match:
            return {"code": 0, "ignored": True, "hint": "使用：方案 项目名 会议纪要或飞书文档链接"}
        project_selector, source = match.group(1), match.group(2).strip()
        notes, source_label = self.service.read_meeting_source(source)
        job = self.submit(
            project_selector,
            notes,
            source_label,
            publish_to_feishu=True,
            chat_id=chat_id,
        )
        return {"code": 0, "job_id": job.job_id}

    def handle_bound_feishu_message(
        self,
        callback_key: str,
        event_id: str,
        chat_id: str,
        sender_id: str,
        text: str,
    ) -> dict[str, Any]:
        bot = self.feishu_bots.get(callback_key)
        if bot is None:
            raise LookupError("未注册的飞书机器人")
        if (bot.allowed_chat_ids or bot.allowed_user_ids) and (
            chat_id not in bot.allowed_chat_ids and sender_id not in bot.allowed_user_ids
        ):
            raise PermissionError("当前飞书群或用户不在允许范围内")
        if not event_id:
            raise ValueError("飞书消息缺少 message_id")
        if not self.service.store.mark_event_once(f"feishu:{bot.bot_id}:{event_id}"):
            return {"code": 0, "duplicate": True}

        clean_text = re.sub(r"@_user_\d+", "", text).strip()
        print(
            f"[飞书] 收到消息 bot={bot.bot_id} chat={chat_id} "
            f"sender={sender_id} chars={len(clean_text)}",
            flush=True,
        )
        command = self._handle_project_command(bot, chat_id, sender_id, clean_text)
        if command is not None:
            return command

        source = re.sub(r"^(?:/)?方案(?:\s+|$)", "", clean_text, count=1).strip()
        if not source:
            return {
                "code": 0,
                "ignored": True,
                "hint": "直接发送会议纪要正文或飞书文档链接",
            }
        feishu = self.feishu_clients[bot.bot_id]
        binding = self.service.store.get_chat_project_binding(bot.bot_id, chat_id)
        project = (
            self.service.store.find_project(str(binding["project_id"]))
            if binding
            else None
        )
        binding_source = "bound"
        if project is None and bot.project_id:
            project = self.service.store.find_project(bot.project_id)
            binding_source = "bot_default"
        if project is None:
            identified = self.service.identify_projects(source)
            if len(identified) > 1:
                names = "、".join(item.name for item in identified[:8])
                feishu.send_text(chat_id, f"识别到多个项目：{names}\n请先发送：绑定项目 项目名")
                return {"code": 0, "ignored": True, "reason": "ambiguous_project"}
            if len(identified) == 1:
                project = identified[0]
                binding_source = "auto_identified"
                self.service.store.bind_chat_project(
                    bot.bot_id,
                    chat_id,
                    project.project_id,
                    sender_id,
                    "auto_identified",
                )
        if project is None:
            projects = self.service.store.list_projects()
            names = "、".join(item.name for item in projects[:10]) or "暂无已注册项目"
            feishu.send_text(
                chat_id,
                "当前会话尚未绑定项目。\n"
                "发送：绑定项目 https://github.com/owner/repo\n"
                f"已注册项目：{names}",
            )
            return {"code": 0, "ignored": True, "reason": "project_not_bound"}
        acknowledgement_sent = False
        try:
            feishu.send_text(
                chat_id,
                f"已收到，正在分析「{project.name}」的代码并生成技术方案。",
            )
            acknowledgement_sent = True
        except Exception as exc:
            print(f"[飞书] 确认消息首次发送失败：{exc}", flush=True)
        notes, source_label = self.service.read_meeting_source(source, feishu)
        job = self.submit(
            project.project_id,
            notes,
            source_label,
            publish_to_feishu=True,
            chat_id=chat_id,
            feishu_bot_id=bot.bot_id,
        )
        if not acknowledgement_sent:
            try:
                feishu.send_text(
                    chat_id,
                    f"已收到，任务 {job.job_id[:8]} 正在分析「{project.name}」。",
                )
            except Exception as exc:
                print(f"[飞书] 确认消息重试失败：{exc}", flush=True)
        return {
            "code": 0,
            "job_id": job.job_id,
            "project_id": project.project_id,
            "bot_id": bot.bot_id,
            "routing": binding_source,
        }

    def _handle_project_command(
        self,
        bot: FeishuBotBinding,
        chat_id: str,
        sender_id: str,
        text: str,
    ) -> dict[str, Any] | None:
        feishu = self.feishu_clients[bot.bot_id]
        normalized = text.strip()
        if normalized in {"项目列表", "/项目列表"}:
            projects = self.service.store.list_projects()
            if projects:
                lines = [
                    f"- {item.name}（{item.github_full_name or item.project_id}）"
                    for item in projects[:30]
                ]
                message = "已注册项目：\n" + "\n".join(lines)
            else:
                message = "还没有注册项目。发送：绑定项目 https://github.com/owner/repo"
            feishu.send_text(chat_id, message)
            return {"code": 0, "command": "list_projects"}

        if normalized in {"当前项目", "/当前项目"}:
            binding = self.service.store.get_chat_project_binding(bot.bot_id, chat_id)
            project = (
                self.service.store.find_project(str(binding["project_id"]))
                if binding
                else None
            )
            if not project:
                feishu.send_text(chat_id, "当前会话尚未绑定项目。")
                return {"code": 0, "command": "current_project", "project_id": ""}
            snapshot = self.service.store.get_repository_snapshot(project.project_id)
            version = str(snapshot["commit_sha"]) if snapshot else "尚未同步"
            feishu.send_text(
                chat_id,
                f"当前项目：{project.name}\n"
                f"代码仓库：{project.github_full_name or project.repo_path}\n"
                f"代码版本：{version}",
            )
            return {
                "code": 0,
                "command": "current_project",
                "project_id": project.project_id,
            }

        if normalized in {"解绑项目", "/解绑项目"}:
            removed = self.service.store.unbind_chat_project(bot.bot_id, chat_id)
            feishu.send_text(chat_id, "项目绑定已解除。" if removed else "当前会话没有项目绑定。")
            return {"code": 0, "command": "unbind_project", "removed": removed}

        match = re.match(r"^(?:/)?绑定项目(?:\s+|$)(.*)$", normalized, flags=re.DOTALL)
        if not match:
            return None
        argument = match.group(1).strip()
        if not argument:
            feishu.send_text(
                chat_id,
                "请发送：绑定项目 https://github.com/owner/repo\n"
                "也可以发送：绑定项目 已注册项目名",
            )
            return {"code": 0, "command": "bind_project", "bound": False}

        parts = argument.split()
        repository = self.service.parse_github_repository(parts[0])
        if repository:
            ref = parts[1] if len(parts) > 1 else ""
            feishu.send_text(chat_id, "正在检查 GitHub 仓库并读取默认分支……")
            try:
                project = self.service.register_github_repository(parts[0], ref)
            except Exception as exc:
                message = str(exc)[:1000]
                feishu.send_text(
                    chat_id,
                    f"项目绑定失败：{message}\n"
                    "如果是私有仓库，请检查 GITHUB_TOKEN 是否具有 Contents: read 权限。",
                )
                return {
                    "code": 0,
                    "command": "bind_project",
                    "bound": False,
                    "error": message,
                }
        else:
            project = self.service.store.find_project(argument)
            if not project:
                feishu.send_text(
                    chat_id,
                    f"没有找到项目“{argument}”。\n"
                    "请发送 GitHub 地址，或先发送“项目列表”。",
                )
                return {"code": 0, "command": "bind_project", "bound": False}

        self.service.store.bind_chat_project(
            bot.bot_id,
            chat_id,
            project.project_id,
            sender_id,
            "command",
        )
        if project.uses_github:
            sync_job = self.submit_github_sync(
                project.project_id,
                chat_id=chat_id,
                feishu_bot_id=bot.bot_id,
            )
            feishu.send_text(
                chat_id,
                f"已绑定项目：{project.name}\n"
                f"GitHub：{project.github_full_name}\n"
                f"分支：{project.github_ref}\n"
                f"正在首次同步代码，任务：{sync_job.job_id[:8]}",
            )
            return {
                "code": 0,
                "command": "bind_project",
                "bound": True,
                "project_id": project.project_id,
                "sync_job_id": sync_job.job_id,
            }
        feishu.send_text(chat_id, f"已绑定项目：{project.name}")
        return {
            "code": 0,
            "command": "bind_project",
            "bound": True,
            "project_id": project.project_id,
        }

    def handle_github_event(
        self,
        raw_body: bytes,
        payload: dict[str, Any],
        event_name: str,
        delivery_id: str,
        signature: str,
    ) -> dict[str, Any]:
        secret = self.settings.github_webhook_secret
        if not secret:
            raise PermissionError("尚未配置 GITHUB_WEBHOOK_SECRET")
        if not verify_webhook_signature(raw_body, signature, secret):
            raise PermissionError("GitHub Webhook 签名无效")
        if not self.service.store.mark_event_once(f"github:{delivery_id}"):
            return {"accepted": True, "duplicate": True}
        if event_name == "ping":
            return {"accepted": True, "message": "pong"}
        if event_name != "push":
            return {"accepted": True, "ignored": True}
        repository = payload.get("repository") or {}
        owner = str(((repository.get("owner") or {}).get("login")) or "")
        repo = str(repository.get("name") or "")
        project = self.service.store.find_github_project(owner, repo)
        if not project:
            return {"accepted": True, "ignored": True, "reason": "repository not registered"}
        pushed_ref = str(payload.get("ref") or "")
        expected_ref = f"refs/heads/{project.github_ref}"
        if pushed_ref != expected_ref:
            return {"accepted": True, "ignored": True, "reason": "non-tracked branch"}
        after = str(payload.get("after") or "")
        if not after or after == "0" * 40:
            return {"accepted": True, "ignored": True, "reason": "deleted branch"}
        job = self.submit_github_sync(project.project_id, target_commit_sha=after)
        return {"accepted": True, "job_id": job.job_id}


def make_handler(application: TrackerApplication):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TechHandoff/0.6"

        def _raw_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 2_000_000:
                raise ValueError("请求体过大")
            return self.rfile.read(length)

        @staticmethod
        def _json_body(raw: bytes) -> dict[str, Any]:
            value = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(value, dict):
                raise ValueError("请求体必须是 JSON 对象")
            return value

        def _send(self, status: int, body: dict[str, Any]) -> None:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/health":
                long_connection = application.feishu_long_connections.snapshot()
                self._send(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "model_enabled": application.settings.model_enabled,
                        "feishu_enabled": bool(application.feishu_bots)
                        or application.settings.feishu_enabled,
                        "feishu_bot_count": len(application.feishu_bots),
                        "feishu_long_connection": long_connection,
                    },
                )
                return
            if path == "/api/projects":
                self._send(
                    HTTPStatus.OK,
                    {"projects": [item.to_dict() for item in application.service.store.list_projects()]},
                )
                return
            match = re.fullmatch(r"/api/jobs/([A-Za-z0-9]+)", path)
            if match:
                job = application.service.store.get_job(match.group(1))
                self._send(HTTPStatus.OK if job else HTTPStatus.NOT_FOUND, job or {"error": "job not found"})
                return
            self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                raw = self._raw_body()
                body = self._json_body(raw)
                if path == "/webhook/github":
                    result = application.handle_github_event(
                        raw,
                        body,
                        self.headers.get("X-GitHub-Event", ""),
                        self.headers.get("X-GitHub-Delivery", ""),
                        self.headers.get("X-Hub-Signature-256", ""),
                    )
                    self._send(HTTPStatus.ACCEPTED, result)
                    return
                if path == "/api/projects":
                    project = Project.from_dict(body)
                    application.service.register_project(project)
                    self._send(HTTPStatus.CREATED, project.to_dict())
                    return
                if path == "/api/proposals":
                    selector = str(body.get("project") or "").strip()
                    notes = str(body.get("meeting_notes") or "").strip()
                    source_label = str(body.get("source_label") or "API 输入")
                    if not selector or not notes:
                        raise ValueError("project 和 meeting_notes 不能为空")
                    job = application.submit(
                        selector,
                        notes,
                        source_label,
                        bool(body.get("publish_to_feishu", False)),
                    )
                    self._send(HTTPStatus.ACCEPTED, job.to_dict())
                    return
                if path == "/api/projects/refresh":
                    selector = str(body.get("project") or "").strip()
                    if not selector:
                        raise ValueError("project 不能为空")
                    self._send(HTTPStatus.OK, application.service.refresh_project(selector))
                    return
                if path == "/api/github/sync":
                    selector = str(body.get("project") or "").strip()
                    if not selector:
                        raise ValueError("project 不能为空")
                    job = application.submit_github_sync(
                        selector,
                        str(body.get("commit_sha") or ""),
                        bool(body.get("force_full", False)),
                    )
                    self._send(HTTPStatus.ACCEPTED, job.to_dict())
                    return
                if path == "/api/context":
                    selector = str(body.get("project") or "").strip()
                    query = str(body.get("query") or "").strip()
                    if not selector or not query:
                        raise ValueError("project 和 query 不能为空")
                    self._send(
                        HTTPStatus.OK,
                        application.service.build_context(
                            selector,
                            query,
                            int(body.get("max_chars") or 24000),
                        ),
                    )
                    return
                if path == "/api/memory":
                    selector = str(body.get("project") or "").strip()
                    kind = str(body.get("kind") or "").strip()
                    content = str(body.get("content") or "").strip()
                    if not selector or not kind or not content:
                        raise ValueError("project、kind 和 content 不能为空")
                    self._send(
                        HTTPStatus.CREATED,
                        application.service.remember(
                            selector,
                            kind,
                            content,
                            str(body.get("source") or "conversation"),
                        ),
                    )
                    return
                if path == "/webhook/feishu":
                    result = application.handle_feishu_event(body)
                    self._send(HTTPStatus.OK, result)
                    return
                match = re.fullmatch(r"/webhook/feishu/([A-Za-z0-9_-]+)", path)
                if match:
                    result = application.handle_feishu_event(body, match.group(1))
                    self._send(HTTPStatus.OK, result)
                    return
                self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
            except PermissionError as exc:
                self._send(HTTPStatus.FORBIDDEN, {"error": str(exc)})
            except (ValueError, LookupError, RuntimeError) as exc:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)[:1000]})

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def run_server(settings: Settings, host: str = "127.0.0.1", port: int = 8787) -> None:
    application = TrackerApplication(settings)
    long_connection = application.feishu_long_connections.start()
    server = ThreadingHTTPServer((host, port), make_handler(application))
    print(f"TechHandoff listening on http://{host}:{port}", flush=True)
    if long_connection["configured_count"]:
        print(
            "Feishu long connections: "
            f"{long_connection['status']} "
            f"({long_connection['connected_count']}/{long_connection['configured_count']})",
            flush=True,
        )
        if long_connection["last_error"]:
            print(f"Feishu connection error: {long_connection['last_error']}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        application.feishu_long_connections.stop()
        application.executor.shutdown(wait=False, cancel_futures=True)
        server.server_close()
