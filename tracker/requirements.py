from __future__ import annotations

import re

from .models import Requirement


CHANGE_MARKERS = ("新增", "增加", "添加", "修改", "优化", "修复", "支持", "删除", "调整", "改成", "改为")
ACCEPTANCE_MARKERS = ("点击", "成功", "失败", "能够", "可以", "必须", "需要", "显示", "提示", "不允许")


def _sentences(text: str) -> list[str]:
    cleaned = re.sub(r"https?://\S+", "", text)
    parts = re.split(r"[\n。！？!?；;]+", cleaned)
    result: list[str] = []
    for part in parts:
        line = re.sub(r"^[\s>*#\-\d.、]+", "", part).strip()
        if len(line) >= 2 and line not in result:
            result.append(line)
    return result


def extract_requirement(meeting_notes: str) -> Requirement:
    sentences = _sentences(meeting_notes)
    if not sentences:
        raise ValueError("会议纪要不能为空")
    requested = [line for line in sentences if any(marker in line for marker in CHANGE_MARKERS)]
    if not requested:
        requested = sentences[:3]
    acceptance = [line for line in sentences if any(marker in line for marker in ACCEPTANCE_MARKERS)]
    if not acceptance:
        acceptance = [f"完成并验证：{line}" for line in requested[:3]]

    combined = "\n".join(sentences)
    unknowns: list[str] = []
    if "按钮" in combined and not any(word in combined for word in ("权限", "角色", "管理员", "客服", "用户可见")):
        unknowns.append("哪些角色可以看到并使用该按钮？")
    if any(word in combined for word in ("通知", "提醒", "发送")) and not any(
        word in combined for word in ("短信", "邮件", "站内信", "微信", "全部渠道")
    ):
        unknowns.append("需要使用哪些通知渠道？")
    if any(word in combined for word in ("按钮", "提交", "发送", "删除")) and not any(
        word in combined for word in ("重复", "幂等", "多次", "频率", "限流")
    ):
        unknowns.append("是否需要防止重复点击或限制操作频率？")
    if not any(word in combined for word in ("失败", "异常", "错误提示")):
        unknowns.append("操作失败时，用户应该看到什么反馈？")

    return Requirement(
        business_goal=sentences[0],
        requested_changes=tuple(requested),
        acceptance_criteria=tuple(acceptance),
        unknowns=tuple(unknowns),
    )
