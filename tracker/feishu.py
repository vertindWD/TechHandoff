from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


class FeishuAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class FeishuDocument:
    document_id: str
    url: str


class FeishuClient:
    def __init__(
        self,
        base_url: str,
        app_id: str,
        app_secret: str,
        tenant_domain: str = "",
        timeout: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.app_id = app_id
        self.app_secret = app_secret
        self.tenant_domain = tenant_domain.rstrip("/")
        self.timeout = timeout
        self._token = ""
        self._token_expires_at = 0.0

    @staticmethod
    def extract_document_id(value: str) -> str:
        match = re.search(r"/docx/([A-Za-z0-9_-]+)", value)
        if match:
            return match.group(1)
        if re.fullmatch(r"[A-Za-z0-9_-]{8,}", value.strip()):
            return value.strip()
        raise ValueError("没有找到有效的飞书新版文档 ID")

    @staticmethod
    def extract_minute_token(value: str) -> str:
        match = re.search(
            r"/minutes/(obcn[A-Za-z0-9_-]{8,})(?:[/?#]|$)",
            value,
            flags=re.IGNORECASE,
        )
        if match:
            return match.group(1)
        stripped = value.strip()
        if re.fullmatch(r"obcn[A-Za-z0-9_-]{8,}", stripped, flags=re.IGNORECASE):
            return stripped
        raise ValueError("没有找到有效的飞书妙记 minute_token")

    def _tenant_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        data = self._request(
            "POST",
            "/auth/v3/tenant_access_token/internal",
            {"app_id": self.app_id, "app_secret": self.app_secret},
            authenticated=False,
        )
        token = str(data.get("tenant_access_token") or "")
        if not token:
            raise FeishuAPIError("飞书未返回 tenant_access_token")
        self._token = token
        self._token_expires_at = time.time() + int(data.get("expire") or 7200)
        return token

    def _request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        authenticated: bool = True,
    ) -> dict:
        raw = self._request_bytes(method, path, payload, authenticated)
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FeishuAPIError("飞书返回了无法解析的 JSON") from exc
        if not isinstance(result, dict):
            raise FeishuAPIError("飞书返回了无效的响应结构")
        if int(result.get("code", 0)) != 0:
            raise FeishuAPIError(f"飞书 API 错误 {result.get('code')}: {result.get('msg')}")
        return result.get("data") or result

    def _request_bytes(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        authenticated: bool = True,
    ) -> bytes:
        headers = {}
        if payload is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        if authenticated:
            headers["Authorization"] = f"Bearer {self._tenant_token()}"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise FeishuAPIError(f"飞书 HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise FeishuAPIError(f"飞书请求失败：{exc}") from exc

    def read_document_text(self, document_id_or_url: str) -> str:
        document_id = self.extract_document_id(document_id_or_url)
        data = self._request("GET", f"/docx/v1/documents/{document_id}/raw_content")
        content = str(data.get("content") or "").strip()
        if not content:
            raise FeishuAPIError(
                "飞书文档没有可读取的文字内容；如果需求只在截图或白板中，请补充文字说明"
            )
        return content

    def read_minute_transcript(self, minute_token_or_url: str) -> str:
        minute_token = self.extract_minute_token(minute_token_or_url)
        query = urllib.parse.urlencode(
            {
                "need_speaker": "true",
                "need_timestamp": "true",
                "file_format": "txt",
            }
        )
        raw = self._request_bytes(
            "GET",
            f"/minutes/v1/minutes/{minute_token}/transcript?{query}",
        )
        try:
            transcript = raw.decode("utf-8-sig").strip()
        except UnicodeDecodeError as exc:
            raise FeishuAPIError("飞书妙记返回的逐字稿不是 UTF-8 文本") from exc
        if not transcript:
            raise FeishuAPIError("飞书妙记没有可读取的逐字稿")

        # The export endpoint normally returns text/plain. Keep compatibility
        # with gateways that wrap successful or failed responses in JSON.
        try:
            result = json.loads(transcript)
        except json.JSONDecodeError:
            return transcript
        if not isinstance(result, dict):
            return transcript
        if int(result.get("code", 0)) != 0:
            raise FeishuAPIError(f"飞书 API 错误 {result.get('code')}: {result.get('msg')}")
        data = result.get("data") or result
        if isinstance(data, dict):
            for key in ("content", "transcript", "text"):
                content = str(data.get(key) or "").strip()
                if content:
                    return content
        raise FeishuAPIError("飞书妙记响应中没有可读取的逐字稿")

    def create_document(self, title: str, folder_token: str = "") -> FeishuDocument:
        payload = {"title": title[:200]}
        if folder_token:
            payload["folder_token"] = folder_token
        data = self._request("POST", "/docx/v1/documents", payload)
        document = data.get("document") or data
        document_id = str(document.get("document_id") or "")
        if not document_id:
            raise FeishuAPIError("飞书创建文档成功但未返回 document_id")
        base = self.tenant_domain or "https://feishu.cn"
        return FeishuDocument(document_id=document_id, url=f"{base}/docx/{document_id}")

    def append_markdown(self, document_id: str, markdown: str) -> None:
        blocks = markdown_to_blocks(markdown)
        for index in range(0, len(blocks), 40):
            payload = {"children": blocks[index : index + 40], "index": -1}
            self._request(
                "POST",
                f"/docx/v1/documents/{document_id}/blocks/{document_id}/children?document_revision_id=-1",
                payload,
            )

    def send_text(self, receive_id: str, text: str, receive_id_type: str = "chat_id") -> None:
        query = urllib.parse.urlencode({"receive_id_type": receive_id_type})
        self._request(
            "POST",
            f"/im/v1/messages?{query}",
            {
                "receive_id": receive_id,
                "msg_type": "text",
                "content": json.dumps({"text": text[:6000]}, ensure_ascii=False),
            },
        )


def _text_block(block_type: int, field: str, content: str) -> dict:
    return {
        "block_type": block_type,
        field: {"elements": [{"text_run": {"content": content[:1900]}}]},
    }


def markdown_to_blocks(markdown: str) -> list[dict]:
    blocks: list[dict] = []
    in_code = False
    code_lines: list[str] = []
    for raw_line in markdown.splitlines():
        if raw_line.startswith("```"):
            if in_code:
                code = "\n".join(code_lines)
                for start in range(0, len(code), 1800):
                    blocks.append(_text_block(2, "text", code[start : start + 1800]))
                code_lines = []
            in_code = not in_code
            continue
        if in_code:
            code_lines.append(raw_line)
            continue
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("### "):
            blocks.append(_text_block(5, "heading3", line[4:]))
        elif line.startswith("## "):
            blocks.append(_text_block(4, "heading2", line[3:]))
        elif line.startswith("# "):
            blocks.append(_text_block(3, "heading1", line[2:]))
        elif line.startswith("- "):
            blocks.append(_text_block(12, "bullet", line[2:]))
        elif re.match(r"^\d+\.\s+", line):
            blocks.append(_text_block(13, "ordered", re.sub(r"^\d+\.\s+", "", line)))
        elif line.startswith("> "):
            blocks.append(_text_block(2, "text", line[2:]))
        else:
            for start in range(0, len(line), 1800):
                blocks.append(_text_block(2, "text", line[start : start + 1800]))
    if code_lines:
        blocks.append(_text_block(2, "text", "\n".join(code_lines)[:1900]))
    return blocks
