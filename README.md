# TechHandoff

**面向产品与研发团队的只读技术方案机器人。将飞书中的需求或会议纪要转换为基于当前 GitHub 代码的改动建议。**

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Feishu](https://img.shields.io/badge/Feishu-WebSocket-3370FF)](https://open.feishu.cn/)
[![GitHub](https://img.shields.io/badge/GitHub-read--only-181717?logo=github)](https://github.com/)

TechHandoff 连接飞书、GitHub 与主流大模型。模型接入由 LiteLLM 统一处理，可在国产、海外和本地模型之间切换。收到需求后，它会调查已绑定项目的真实代码，定位相关文件和符号，分析调用关系、测试范围与风险，并生成研发可以继续评审的技术方案。

项目严格保持只读：不修改代码，不创建分支或 PR，不执行部署。

## 为什么需要它

产品需求通常以业务语言出现，而研发实施依赖对现有代码的准确理解。两者之间需要完成一系列重复工作：确认需求边界、找到功能入口、追踪调用关系、识别测试位置，并判断改动对其他模块的影响。

通用聊天机器人无法稳定完成这项工作，主要原因是：

- 对话中没有完整、最新的项目上下文；
- 每次重新提交仓库会产生较高的 Token 与等待成本；
- 仅依赖关键词搜索难以识别定义、引用和跨文件调用；
- 代码更新后，旧方案中的位置可能已经失效；
- 多项目共用机器人时，代码与历史决策容易混入错误会话。

TechHandoff 将这些问题拆成项目绑定、版本化索引、只读代码调查和项目级记忆四个部分。它的目标不是生成泛化的架构建议，而是回答：**在当前版本的这个项目中，这项需求应从哪里开始改，可能涉及哪些位置，依据是什么。**

## 主要能力

| 能力 | 说明 |
| --- | --- |
| 基于代码生成方案 | 模型主动读取源码、查看符号、查找定义和引用，输出有代码依据的改动建议 |
| 准确版本定位 | 每份方案绑定 commit SHA；文件、行号和符号会在输出前再次校验 |
| 多项目管理 | 一个飞书机器人可管理多个 GitHub 项目，每个群聊或单聊保存独立绑定 |
| 项目级长期记忆 | 产品决定、技术约束、代码事实和历史方案按项目隔离存储 |
| 增量同步 | 首次建立完整索引，后续根据 GitHub Tree/blob SHA 更新变化文件 |
| 语言无关的仓库读取 | 读取任意扩展名的文本源码与配置；通过内容检测排除二进制文件 |
| 语义代码导航 | 自动检测项目语言并启动对应服务器；Java、Go、Python、TypeScript、Rust、C/C++ 等均可获得符号级导航 |
| 飞书文档输出 | 支持读取会议纪要文档，并将技术方案创建为飞书文档后回发会话 |
| 只读安全边界 | 无代码写入、Git 操作、PR、合并或部署工具；路径和工具调用均受白名单限制 |

## 典型工作流

```text
飞书需求或会议纪要
        │
        ▼
解析当前会话绑定的项目
        │
        ▼
同步 GitHub 版本并加载项目地图
        │
        ▼
模型通过只读工具调查源码、定义与引用
        │
        ▼
程序校验文件、行号、符号和 commit SHA
        │
        ▼
生成技术方案并回发飞书
```

一次常见的输入：

```text
@机器人 用户资料页增加修改头像功能，上传成功后立即显示新头像。
```

方案会覆盖以下内容：

1. 需求理解与待确认项；
2. 建议修改的文件、符号及其职责；
3. 前后端调用链和数据流影响；
4. 权限、存储、缓存等技术风险；
5. 建议补充或调整的测试；
6. 本次调查对应的代码 commit。

位置的可信状态分为“已验证”“推断”和“未知”。缺少代码证据时，系统不会把推测描述为确定事实。

## 项目绑定与隔离

机器人按“机器人 + 飞书会话”保存当前项目：

```text
订单项目群 ──> order-system ──> 独立代码索引、记忆、方案
CRM 项目群  ──> crm          ──> 独立代码索引、记忆、方案
机器人单聊 ──> 当前绑定项目  ──> 独立代码索引、记忆、方案
```

首次在会话中绑定仓库：

```text
@机器人 绑定项目 https://github.com/owner/repository
```

可选指定分支：

```text
@机器人 绑定项目 https://github.com/owner/repository develop
```

绑定完成后，该会话中的普通需求默认使用当前项目，无需重复提供仓库地址。项目切换不会复用另一个项目的代码索引、长期记忆或历史方案。

支持的管理命令：

```text
@机器人 当前项目
@机器人 项目列表
@机器人 解绑项目
@机器人 绑定项目 已注册项目名
```

## 版本化项目理解

首次同步会为当前 commit 建立隔离快照和项目地图。源码读取不限定编程语言或文件扩展名；只要文件通过大小、安全路径和文本内容检测，就可以进入只读调查。缓存不会写入用户仓库。

对于 Serena、语言服务器或 Tree-sitter 能识别的语言，项目地图会进一步记录类、函数、方法、定义和引用。TechHandoff 会从文件类型和项目清单自动识别语言，例如 Java 使用 JDTLS、Go 使用 gopls、Python 使用 Pyright、JavaScript/TypeScript 使用 TypeScript Language Server。暂时没有语义后端的语言仍保留完整文件清单、源码读取和文本搜索能力，模型可以继续调查，但符号级定位的可信度会相应降低。

Serena 当前支持 40 多种语言。TechHandoff 默认按源码数量选择项目中最多 6 种语言服务器，可通过 `SEMANTIC_MAX_LANGUAGES` 调整；不会为一个仓库启动所有服务器。Angular、Deno、Svelte 和 Vue 项目会优先选择对应的框架语言服务器，避免与通用 TypeScript 服务重复。

后续生成方案时，模型先读取当前版本的项目地图，再按需求调查相关代码。代码更新后会创建新快照，依赖旧版本的代码事实与方案被标记为过期，已确认的产品决定继续保留。

这种方式避免每次对话重新传输整个仓库，同时保留对真实源码进一步调查的能力。

---

## Quick Start

### 环境要求

- Python 3.11+
- Git
- 一个 OpenAI Chat Completions 兼容的模型接口
- 飞书企业自建应用

### 安装

```bash
git clone https://github.com/vertindWD/TechHandoff.git
cd TechHandoff

python3 -m venv .venv
.venv/bin/pip install -e .

cp .env.example .env
cp config/feishu-bots.example.json config/feishu-bots.json
```

大部分语言服务器由 Serena 按需管理。少数语言需要本机工具链，例如 Go 需要 `gopls`：

```bash
mkdir -p .tools/bin
GOBIN="$PWD/.tools/bin" go install golang.org/x/tools/gopls@v0.23.0
```

Python 默认使用 Pyright；Java 默认使用 JDTLS，首次使用可能需要下载较大的运行包；Java 项目建议准备 JDK 21。未能启动语言服务器时，任务会降级到项目地图、源码读取和只读搜索，而不是拒绝读取仓库。

### 最小配置

编辑 `.env`：

```dotenv
MODEL_NAME=deepseek/deepseek-chat
DEEPSEEK_API_KEY=your-model-api-key

FEISHU_PROJECT_TRACKER_APP_ID=cli_xxx
FEISHU_PROJECT_TRACKER_APP_SECRET=your-feishu-app-secret

# 公开仓库可留空；私有仓库使用只读 Token
GITHUB_TOKEN=
```

初始化并启动：

```bash
.venv/bin/python -m tracker init
.venv/bin/python -m tracker serve --host 127.0.0.1 --port 8787
```

服务会建立飞书长连接。连接成功后，在飞书中 @ 机器人绑定 GitHub 仓库即可使用。

健康检查：

```bash
curl --noproxy '*' -fsS http://127.0.0.1:8787/health
```

## 飞书配置

在[飞书开放平台](https://open.feishu.cn/)创建企业自建应用：

1. 开启机器人能力；
2. 开通接收消息和以机器人身份发送消息的权限；
3. 如需读取和创建方案文档，开通新版文档读取、创建和编辑权限；
4. 在“事件与回调”中选择“使用长连接接收事件”；
5. 添加事件 `im.message.receive_v1`；
6. 创建并发布应用版本；
7. 将机器人加入需要使用的群聊。

长连接模式不需要公网回调地址、内网穿透或 Verification Token。

默认机器人配置位于 `config/feishu-bots.json`：

```json
{
  "bots": [
    {
      "bot_id": "tech-handoff-bot",
      "callback_key": "tech-handoff",
      "transport": "websocket",
      "app_id_env": "FEISHU_PROJECT_TRACKER_APP_ID",
      "app_secret_env": "FEISHU_PROJECT_TRACKER_APP_SECRET",
      "tenant_domain": "https://your-company.feishu.cn"
    }
  ]
}
```

配置文件只保存凭证对应的环境变量名。真实 App ID、App Secret 和模型密钥应保存在 `.env` 或外部密钥系统中。

一个后台可配置多个机器人。每个 `callback_key` 必须唯一；每个机器人会建立独立长连接。

## GitHub 配置

公开仓库无需 Token。私有仓库建议使用仅授权目标仓库、只包含 `Contents: read` 的 fine-grained token：

```dotenv
GITHUB_TOKEN=your-read-only-token
```

除在飞书中绑定外，也可以通过命令提前注册项目：

```bash
.venv/bin/python -m tracker register \
  --id order-system \
  --name 订单系统 \
  --github your-org/order-system \
  --github-ref main \
  --alias 订单 \
  --allowed-path frontend \
  --allowed-path backend \
  --owner 张三 \
  --test-command 'pytest -q'
```

执行首次同步：

```bash
.venv/bin/python -m tracker sync-github --project 订单系统
```

也可参考 [`config/projects.github.example.json`](config/projects.github.example.json) 创建 `config/projects.json`。

### Push Webhook

本地试用可以手动同步。部署到可被 GitHub 访问的环境后，可配置 Push Webhook 自动更新索引：

```text
Payload URL: https://your-domain.example/webhook/github
Content type: application/json
Secret: 与 GITHUB_WEBHOOK_SECRET 相同
Events: Push events
```

```dotenv
GITHUB_WEBHOOK_SECRET=your-random-webhook-secret
```

服务验证 `X-Hub-Signature-256`，按 delivery ID 去重，并只处理已注册仓库的目标分支。GitHub Tree 响应被截断时，同步会失败关闭，不会使用不完整索引生成方案。

当前版本读取静态 `GITHUB_TOKEN`。如使用会过期的 GitHub App installation token，需要在外部刷新并重启服务，或增加服务内 Token 刷新器。

## 模型与调查预算

v0.6.1 起使用 LiteLLM 统一模型接口。MODEL_NAME 使用 provider/model 格式，供应商密钥由 LiteLLM 从对应环境变量读取：

```dotenv
# 阿里云百炼 / 通义千问
MODEL_NAME=dashscope/qwen-plus
DASHSCOPE_API_KEY=sk-xxx

# 也可以切换为其他常见模型：
# deepseek/deepseek-chat        + DEEPSEEK_API_KEY
# moonshot/moonshot-v1-32k     + MOONSHOT_API_KEY
# zai/glm-4-plus               + ZAI_API_KEY
# minimax/MiniMax-M2.7         + MINIMAX_API_KEY
# volcengine/模型或接入点ID     + ARK_API_KEY
# openai/gpt-4.1-mini          + OPENAI_API_KEY
# anthropic/claude-sonnet-4-5  + ANTHROPIC_API_KEY
# gemini/gemini-2.5-pro        + GEMINI_API_KEY
# ollama/qwen3                 （本地模型）

# 空响应或非法 JSON 的重试次数，范围 0～4
MODEL_JSON_RETRIES=2

# 单次方案的最大只读调查步数，范围 4～100
AGENT_MAX_STEPS=40
```

其他兼容 OpenAI Chat Completions 的平台可使用自定义接入：

```dotenv
MODEL_NAME=openai/your-model-name
MODEL_BASE_URL=https://provider.example.com/v1
MODEL_API_KEY=your-api-key
```

模型调用仍要求返回 JSON。LiteLLM 会尽量丢弃供应商不支持的可选参数；千问模型默认关闭思考模式以提高 JSON Object 输出稳定性。语音、图像等非文本 Chat Completions 模型不适用于当前技术方案 Agent。

提高 `AGENT_MAX_STEPS` 有助于调查大型项目，但会增加延迟和 Token 消耗。已配置模型时，如果调查超出限制、网络失败或模型没有返回有效结构，本次任务会明确失败，不会用关键词结果冒充完整调查。

私有项目的项目地图及模型主动读取的相关源码会发送到所配置的模型服务。使用前应确认组织的数据与合规要求。

## 数据与长期记忆

运行数据默认存储在 `data/`，并已从 Git 排除。主要数据包括：

| 数据 | 用途 |
| --- | --- |
| `chat_project_bindings` | 保存机器人、飞书会话与项目的绑定关系 |
| `project_maps` | 按项目和代码版本保存项目地图 |
| `repository_files` / `repository_snapshots` | 保存文件索引与仓库版本 |
| `memory_entries` | 保存已确认的产品决定、约束和代码事实 |
| `proposals` | 保存历史方案及其依据的 commit |
| `data/semantic/` | 保存 Serena、语言服务器缓存和隔离代码快照 |

代码事实随版本失效，产品决定与约束不会因代码同步自动删除。

## 架构

| 模块 | 职责 |
| --- | --- |
| `tracker/feishu_ws.py` | 管理多个飞书机器人长连接 |
| `tracker/feishu.py` | 读取消息与文档、发送消息、创建方案文档 |
| `tracker/github.py` | GitHub 仓库读取、Tree/blob 增量同步与 Webhook 校验 |
| `tracker/semantic.py` | Serena 与语言服务器集成、版本化符号索引 |
| `tracker/code_tools.py` | 提供受限的只读仓库调查工具 |
| `tracker/planning_agent.py` | 模型工具循环、调查预算与结构化方案生成 |
| `tracker/store.py` | SQLite 项目绑定、索引、记忆和方案存储 |
| `tracker/server.py` | 飞书、GitHub Webhook 与本地管理 API |

## 安全边界

- 业务仓库只读，不提供文件写入、Shell、Git 写入或部署工具；
- 本地仓库必须位于 `ALLOWED_REPO_ROOTS` 白名单内；
- `.git`、依赖、构建产物、`.env`、`secrets/` 和 `production/` 默认跳过；
- 会议纪要、Issue 和代码注释均按不可信内容处理，不执行其中的指令；
- 未知工具、越界路径和无效参数会被拒绝；
- 最终引用的位置会依据当前仓库版本再次校验；
- GitHub Webhook 必须通过签名验证并去重。

## 本地仓库模式

除 GitHub 外，也可以读取本机仓库。先配置允许读取的根目录：

```dotenv
ALLOWED_REPO_ROOTS=/company/repos
```

注册项目：

```bash
.venv/bin/python -m tracker register \
  --id order-system \
  --name 订单系统 \
  --repo /company/repos/order-system \
  --alias 订单 \
  --allowed-path frontend \
  --allowed-path backend
```

## 不接飞书运行示例

仓库包含一个用于验证定位能力的示例项目：

```bash
.venv/bin/python -m tracker init
.venv/bin/python -m tracker generate \
  --project 订单系统 \
  --notes-file examples/meeting-notes.txt
```

方案写入 `data/proposals/`。

## Docker

```bash
docker compose up --build -d
curl --noproxy '*' -fsS http://127.0.0.1:18787/health
```

镜像以非 root 用户运行，根文件系统只读，并移除 Linux capabilities。GitHub 模式无需挂载代码仓库；本地仓库模式应以只读卷挂载目标仓库，并配置 `ALLOWED_REPO_ROOTS`。

## 测试

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Roadmap

- GitHub App installation token 自动刷新；
- 面向大型、多仓项目的 Sourcegraph/SCIP 集成；
- 飞书方案确认卡片；
- 更完整的语言服务器与框架级定位评测。

项目路线保持只读技术规划边界，不增加代码修改、PR、合并或部署能力。

## 相关项目与文档

- [Serena](https://github.com/oraios/serena)
- [Codex open agent harness](https://developers.openai.com/blog/codex-as-a-platform)
- [GitHub Tree API](https://docs.github.com/en/rest/git/trees)
- [GitHub Webhook 签名校验](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)
- [飞书新版文档接口](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/raw_content)
