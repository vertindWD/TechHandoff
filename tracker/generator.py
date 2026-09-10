from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .models import (
    ChangeRecommendation,
    Evidence,
    Project,
    Proposal,
    RepositorySnapshot,
    Requirement,
)


def _category(path: str) -> str:
    lower = path.lower()
    if any(part in lower for part in ("test", "spec")):
        return "测试"
    if lower.startswith("frontend/") or "/frontend/" in lower:
        return "前端"
    if Path(lower).suffix in {".tsx", ".jsx", ".vue", ".svelte", ".css", ".scss", ".html"}:
        return "前端"
    if lower.startswith("backend/") or "/backend/" in lower:
        return "后端"
    if any(part in lower for part in ("api", "route", "controller", "service")):
        return "后端"
    if Path(lower).suffix in {".sql"} or "migration" in lower or "model" in lower:
        return "数据"
    if Path(lower).name in {"dockerfile", "compose.yaml", "docker-compose.yml"}:
        return "部署"
    return "其他"


def _implementation_steps(evidence: tuple[Evidence, ...]) -> list[str]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for item in evidence:
        grouped[_category(item.path)].append(item.path)
    steps: list[str] = []
    if grouped.get("前端"):
        steps.append(f"在前端相关组件中完成交互与状态反馈，优先检查 `{grouped['前端'][0]}`。")
    if grouped.get("后端"):
        steps.append(f"核对并复用现有接口或服务层逻辑，优先检查 `{grouped['后端'][0]}`。")
    if grouped.get("数据"):
        steps.append(f"确认是否需要数据结构变化；当前相关证据包括 `{grouped['数据'][0]}`。")
    if grouped.get("测试"):
        steps.append(f"在现有测试附近补充验收场景，优先检查 `{grouped['测试'][0]}`。")
    if not steps:
        steps.append("当前代码证据不足，先由研发确认模块位置，再决定具体改动文件。")
    return steps


def build_proposal(
    project: Project,
    snapshot: RepositorySnapshot,
    requirement: Requirement,
    evidence: tuple[Evidence, ...],
    source_label: str,
    memory: tuple[dict, ...] = (),
) -> Proposal:
    now = datetime.now(UTC).replace(microsecond=0)
    proposal_id = uuid4().hex[:16]
    lines: list[str] = [
        f"# {project.name}技术实施方案",
        "",
        "> 状态：AI 初稿，必须经项目研发负责人确认后实施。",
        "",
        "## 1. 非技术需求摘要",
        "",
        requirement.business_goal,
        "",
        "## 2. 会议已确认内容",
        "",
    ]
    lines.extend(f"- {item}" for item in requirement.requested_changes)
    lines.extend(["", "## 3. 当前代码定位", ""])
    if evidence:
        lines.append("以下位置已在本次分析的代码快照中验证存在：")
        lines.append("")
        for item in evidence:
            symbol_text = f"；相关符号：{', '.join(item.symbols)}" if item.symbols else ""
            lines.append(
                f"- `{item.path}:{item.line_start}`：匹配 {', '.join(item.matched_terms[:6]) or '需求语义'}{symbol_text}"
            )
    else:
        lines.append("- 未定位到足够可信的代码位置。不得据此直接修改代码，需研发补充模块名称或页面入口。")

    if memory:
        lines.extend(["", "### 关联项目记忆", ""])
        for item in memory[:8]:
            lines.append(
                f"- [{item.get('kind', 'memory')}] {item.get('content', '')}（来源：{item.get('source', 'unknown')}）"
            )

    lines.extend(["", "## 4. 建议实现步骤", ""])
    for index, step in enumerate(_implementation_steps(evidence), start=1):
        lines.append(f"{index}. {step}")

    lines.extend(["", "## 5. 测试与验收标准", ""])
    lines.extend(f"- {item}" for item in requirement.acceptance_criteria)
    if not any(_category(item.path) == "测试" for item in evidence):
        lines.append("- 当前未定位到直接相关测试文件，需要研发确认测试放置位置。")

    lines.extend(["", "## 6. 风险与影响", ""])
    lines.extend(
        [
            "- 会议纪要可能缺少权限、失败反馈或重复操作约束。",
            "- 方案仅基于当前代码快照；相关文件变化后需要重新生成或复核。",
            "- 文件定位表示相关性证据，不代表所有文件都必须修改。",
        ]
    )

    lines.extend(["", "## 7. 待产品确认问题", ""])
    if requirement.unknowns:
        lines.extend(f"- {item}" for item in requirement.unknowns)
    else:
        lines.append("- 当前未自动发现明显缺口，仍需研发负责人复核边界条件。")

    lines.extend(["", "## 8. 代码证据", ""])
    if evidence:
        for item in evidence:
            lines.extend(
                [
                    f"### `{item.path}:{item.line_start}-{item.line_end}`",
                    "",
                    "```text",
                    item.excerpt,
                    "```",
                    "",
                ]
            )
    else:
        lines.append("无。")

    lines.extend(
        [
            "",
            "## 9. 生成依据",
            "",
            f"- 项目：{project.name} (`{project.project_id}`)",
            f"- 代码目录：`{snapshot.repo_path}`",
            f"- 代码版本：`{snapshot.version}`",
            f"- 已扫描文本文件：{snapshot.file_count}",
            f"- 已跳过文件：{snapshot.skipped_file_count}",
            f"- 会议来源：{source_label}",
            f"- 生成时间：{now.isoformat()}",
            "",
        ]
    )
    return Proposal(
        proposal_id=proposal_id,
        project_id=project.project_id,
        project_name=project.name,
        repository_version=snapshot.version,
        generated_at=now.isoformat(),
        source_label=source_label,
        requirement=requirement,
        evidence=evidence,
        markdown="\n".join(lines),
    )


def build_manager_proposal(
    project: Project,
    snapshot: RepositorySnapshot,
    requirement: Requirement,
    recommendations: tuple[ChangeRecommendation, ...],
    evidence: tuple[Evidence, ...],
    suggested_tests: tuple[str, ...],
    risks: tuple[str, ...],
    analysis_steps: tuple[str, ...],
    source_label: str,
    complete: bool = True,
    termination_reason: str = "",
    covered_requirements: tuple[str, ...] = (),
    uncovered_requirements: tuple[str, ...] = (),
    investigation_metrics: dict[str, object] | None = None,
) -> Proposal:
    """Render the short handoff a technical manager would give an engineer."""
    now = datetime.now(UTC).replace(microsecond=0)
    proposal_id = uuid4().hex[:16]
    metrics = investigation_metrics or {}
    status_line = (
        "> 状态：未完成方案。调查预算已耗尽，仅保留经过校验的阶段性结果；不能视为完整实施范围。"
        if not complete
        else "> 只读调查结果：未修改代码、未运行测试、未创建分支或 PR。研发实施前请复核。"
    )
    lines = [
        f"# {project.name}技术改动建议",
        "",
        status_line,
        "",
        "## 需求",
        "",
        requirement.business_goal,
        "",
    ]
    if requirement.requested_changes:
        lines.extend(f"- {item}" for item in requirement.requested_changes)
        lines.append("")

    if not complete:
        lines.extend(["## 调查覆盖情况", ""])
        lines.append("### 已覆盖")
        if covered_requirements:
            lines.extend(f"- {item}" for item in covered_requirements)
        else:
            lines.append("- 尚无可安全声明为完整覆盖的需求；下方仅列出已确认的局部改动位置。")
        lines.extend(["", "### 未覆盖或仍待调查"])
        if uncovered_requirements:
            lines.extend(f"- {item}" for item in uncovered_requirements)
        else:
            lines.append("- 需要继续核对剩余业务链路和影响范围。")
        if termination_reason:
            lines.extend(["", f"- 结束原因：{termination_reason}"])
        lines.append("")

    lines.extend(["## 建议改动位置", ""])
    if recommendations:
        confidence_names = {"verified": "已核对", "inferred": "推断"}
        for index, item in enumerate(recommendations, start=1):
            location = f"`{item.path}:{item.line_start}`"
            if item.symbol:
                location += f" / `{item.symbol}`"
            confidence = confidence_names.get(item.confidence, item.confidence)
            lines.extend(
                [
                    f"{index}. {location}（{confidence}）",
                    f"   - {item.instruction}",
                ]
            )
    else:
        lines.append("- 没有得到足够可信的位置；本次结果不能直接交给研发实施。")

    if not complete and evidence:
        lines.extend(["", "### 已确认依据", ""])
        for item in evidence:
            symbol = f"，符号 `{item.symbols[0]}`" if item.symbols else ""
            lines.append(
                f"- `{item.path}:{item.line_start}-{item.line_end}`{symbol}：本次调查已读取并通过位置校验。"
            )

    lines.extend(["", "## 测试与验收", ""])
    tests = tuple(dict.fromkeys((*suggested_tests, *requirement.acceptance_criteria)))
    if tests:
        lines.extend(f"- {item}" for item in tests)
    else:
        lines.append("- 需要研发根据现有测试体系补充覆盖。")

    lines.extend(["", "## 风险与待确认", ""])
    combined_risks = tuple(dict.fromkeys((*risks, *requirement.unknowns)))
    if combined_risks:
        lines.extend(f"- {item}" for item in combined_risks)
    else:
        lines.append("- 当前未发现明确阻塞项，仍需项目研发负责人复核影响范围。")

    lines.extend(
        [
            "",
            "## 调查依据",
            "",
            f"- 代码版本：`{snapshot.version}`",
            f"- 已索引文本文件：{snapshot.file_count}，跳过：{snapshot.skipped_file_count}",
            f"- 会议来源：{source_label}",
            f"- 生成时间：{now.isoformat()}",
        ]
    )
    if analysis_steps:
        lines.append(f"- 只读调查步骤：{' → '.join(analysis_steps)}")
    if metrics:
        lines.extend(
            [
                f"- 调查耗时：{metrics.get('elapsed_seconds', 0)} 秒",
                f"- 模型调用：{metrics.get('model_calls', 0)} 次；只读查询：{metrics.get('tool_calls', 0)} 次",
                f"- 重复查询：{metrics.get('duplicate_queries', 0)} 次",
                f"- 需求覆盖：{metrics.get('covered_requirement_count', 0)}/{metrics.get('requirement_count', 0)}",
            ]
        )
    lines.append("")

    return Proposal(
        proposal_id=proposal_id,
        project_id=project.project_id,
        project_name=project.name,
        repository_version=snapshot.version,
        generated_at=now.isoformat(),
        source_label=source_label,
        requirement=requirement,
        evidence=evidence,
        recommendations=recommendations,
        suggested_tests=suggested_tests,
        risks=risks,
        analysis_steps=analysis_steps,
        covered_requirements=covered_requirements,
        uncovered_requirements=uncovered_requirements,
        investigation_metrics=metrics,
        status="draft" if complete else "partial",
        markdown="\n".join(lines),
    )
