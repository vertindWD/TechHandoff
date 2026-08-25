# Project Tracker Agent

把非技术会议纪要转换成**技术经理式、基于当前真实代码的改动建议**。一个飞书机器人可以管理多个 GitHub 项目；每个飞书群或单聊保存自己的当前项目绑定，代码索引、项目地图、长期记忆和方案历史始终按项目隔离。系统只读业务代码，不提供修改代码、分支、PR、合并或部署能力。

## 已实现的 MVP 闭环

```text
飞书机器人 + 当前会话项目绑定 + 会议纪要/飞书文档
  -> 识别已确认需求与缺失信息
  -> 会话绑定优先，未绑定时按明确项目名、别名或 GitHub 地址识别项目
  -> 首次同步代码索引，Push Webhook 只更新变化文件
  -> 为每个 commit 建立隔离代码快照，由 Serena + 语言服务器生成完整文件/符号索引
  -> 模型通过只读工具循环自主读源码、查定义、查引用，文本搜索只作降级
  -> 程序再次验证引用文件、行号和符号，删除虚构位置或降级为推断
  -> 生成精简 Markdown：需求、2～5 个改动位置、测试、风险、commit SHA
  -> 可选：创建飞书文档并回发群聊
```

核心安全边界：

- GitHub 使用 Tree/blob SHA 增量索引；本地仓库必须位于 `ALLOWED_REPO_ROOTS` 白名单内。
- 跳过 `.git`、依赖、构建产物、`.env`、`secrets/` 和 `production/`。
- 不执行会议纪要或代码注释中的命令。
- 当前版本和产品边界都不包含业务代码写权限、分支、PR、自动合并或部署功能。
- Agent 工具白名单只有项目理解、列文件、读文件、符号概览、定义、引用和只读搜索；未知工具和越界路径会被拒绝。
- 每份方案绑定代码提交 SHA；非 Git 目录使用内容快照指纹。
- GitHub Webhook 必须通过 `X-Hub-Signature-256` 校验并按 delivery ID 去重。
- GitHub Tree 返回截断标记时同步失败关闭，不会把不完整索引当成成功。

## 直接运行样例

使用项目内虚拟环境安装依赖：

```bash
cd /home/zhao/rag-projects/project-tracker-agent
cp .env.example .env
cp config/feishu-bots.example.json config/feishu-bots.json
python3 -m venv .venv
.venv/bin/pip install -e .
mkdir -p .tools/bin
GOBIN="$PWD/.tools/bin" go install golang.org/x/tools/gopls@v0.23.0
.venv/bin/python -m tracker init
.venv/bin/python -m tracker generate \
  --project 订单系统 \
  --notes-file examples/meeting-notes.txt
```

生成结果位于 `data/proposals/`。样例仓库中包含订单详情页、通知 API、后端服务和测试，便于验证文件定位是否真实。

运行测试：

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## 启动服务

```bash
.venv/bin/python -m tracker serve --host 127.0.0.1 --port 8787
```

健康检查：

```bash
curl --noproxy '*' -fsS http://127.0.0.1:8787/health
```

提交会议纪要：

```bash
curl --noproxy '*' -sS http://127.0.0.1:8787/api/proposals \
  -H 'Content-Type: application/json' \
  -d '{
    "project": "订单系统",
    "meeting_notes": "订单详情页增加重新发送通知按钮，点击后显示成功或失败提示。"
  }'
```

接口返回 `job_id`。随后读取：

```bash
curl --noproxy '*' -sS http://127.0.0.1:8787/api/jobs/替换为job_id
curl --noproxy '*' -sS http://127.0.0.1:8787/api/proposals/替换为proposal_id
```

## 直接读取 GitHub（推荐）

公开仓库可以不配置 Token。私有仓库快速内测可使用只有仓库读取权限的 fine-grained token；生产环境推荐 GitHub App installation token，并只授予 `Contents: read`：

```dotenv
GITHUB_TOKEN=从密钥管理系统注入
GITHUB_WEBHOOK_SECRET=随机生成的Webhook密钥
```

注册仓库，不需要在运行机器上 `git clone`：

```bash
python3 -m tracker register \
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

首次同步：

```bash
python3 -m tracker sync-github --project 订单系统
```

首次同步下载一个 GitHub tarball 建立缓存，同时保存 commit SHA、tree SHA 和每个文件的 blob SHA。后续同步先比较 Tree，只通过 blob API读取变化文件；变化文件过多时自动退回一次完整 tarball 同步。

也可以参照 [GitHub 项目配置样例](config/projects.github.example.json) 编辑 `config/projects.json`，再运行 `python3 -m tracker init`。

### GitHub Push Webhook

在 GitHub 仓库或 GitHub App 中配置：

```text
Payload URL: https://你的服务域名/webhook/github
Content type: application/json
Secret: 与 GITHUB_WEBHOOK_SECRET 相同
Events: Push events
```

Push 到项目配置的 `github_ref` 后，服务验证 HMAC-SHA256 签名，异步同步目标 commit，只更新变化文件，并把依赖旧代码版本的技术方案和代码事实记忆标记为 `stale`。其他分支和未注册仓库会被忽略。

需要人工触发时也可以调用：

```bash
curl --noproxy '*' -sS http://127.0.0.1:8787/api/github/sync \
  -H 'Content-Type: application/json' \
  -d '{"project":"订单系统"}'
```

当前程序接受静态 `GITHUB_TOKEN`。GitHub App installation token 会过期，生产部署需要由密钥代理定时刷新环境中的 Token 并重启服务，或者在下一阶段把 GitHub App JWT/installation-token 刷新器直接加入服务。

GitHub 接口依据：[Git Tree API](https://docs.github.com/en/rest/git/trees)、[Git Blob API](https://docs.github.com/en/rest/git/blobs)、[Webhook 签名校验](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)、[GitHub App 鉴权](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app)。

## 长期记忆与低 Token 上下文

每份技术方案会持久化：

- 已确认需求和验收标准；
- 待确认问题；
- 与具体 commit 绑定的代码文件和符号；
- 飞书或 API 明确保存的产品决定、约束和偏好。

另外，首次接入或代码 commit 变化后会生成一份版本化项目理解索引：GitHub 缓存先被物化到 `data/semantic/snapshots/` 的隔离快照，再由 [Serena](https://github.com/oraios/serena) 和语言服务器遍历受支持源码，记录完整文件清单以及真实类、函数、方法等符号。索引仍存放在 `project_maps` 表并按 `project_id + repository_version` 隔离；Serena 自身缓存位于 `data/semantic/`，不会写回用户仓库。

后续会议不会重新把整个仓库塞给模型：模型先读这一版的项目理解索引，再对相关符号调用定义、引用和源码读取。GitHub 更新到新 commit 后创建新快照和新索引，旧索引不会跨版本冒充当前事实。建立索引只运行本地语言服务器，不调用大模型，因此没有模型 Token 消耗。

保存一条经过确认的长期记忆：

```bash
curl --noproxy '*' -sS http://127.0.0.1:8787/api/memory \
  -H 'Content-Type: application/json' \
  -d '{
    "project":"订单系统",
    "kind":"confirmed_decision",
    "content":"只有客服角色可以重新发送订单通知。",
    "source":"2026-08-21 产品确认"
  }'
```

每次机器人对话先请求受限上下文：

```bash
curl --noproxy '*' -sS http://127.0.0.1:8787/api/context \
  -H 'Content-Type: application/json' \
  -d '{
    "project":"订单系统",
    "query":"重新发送订单通知按钮怎么实现",
    "max_chars":24000
  }'
```

该兼容接口仍返回受限代码证据和长期记忆，并给出字符数及保守 Token 上限。正式的方案生成不再依赖这条关键词检索链路，而是使用上面的只读 Agent 调查。它不会在每次对话中重新下载 GitHub。代码更新后，代码事实会过期；产品决定、约束和偏好会继续保留。

## 本地仓库兼容模式

先把真实仓库根目录加入 `.env`：

```dotenv
ALLOWED_REPO_ROOTS=/company/repos
```

注册一个仓库：

```bash
python3 -m tracker register \
  --id order-system \
  --name 订单系统 \
  --repo /company/repos/order-system \
  --alias 订单 \
  --allowed-path frontend \
  --allowed-path backend \
  --owner 张三 \
  --test-command 'pytest -q'
```

也可以编辑 `config/projects.json` 后重新执行 `python3 -m tracker init`。此模式用于离线测试；正式使用优先配置 GitHub。

## 启用只读技术经理 Agent

不配置模型时，仅保留可重复测试的离线兼容方案，不能视为 Agent 调查结果。配置 OpenAI-compatible 服务后，模型按 `AGENT_MAX_STEPS` 自主调用只读仓库工具；工具单次输出和累计对话上下文都有字符预算，较早调查结果会压缩为步骤与已读文件列表，需要时由模型重新读取。程序对最终位置做二次校验。已配置模型发生错误或超过调查步数时会让本次生成失败，不会悄悄拿关键词结果冒充高置信方案。

```dotenv
MODEL_BASE_URL=https://your-approved-model.example/v1
MODEL_API_KEY=从密钥管理系统注入
MODEL_NAME=your-model
# 模型偶发返回空内容或非法 JSON 时的自动重试次数，范围 0～4
MODEL_JSON_RETRIES=2
# 单次方案最多允许多少次“模型决定 + 只读工具”调查，范围 4～100
AGENT_MAX_STEPS=40

# 本地语义理解；Go 项目还需要 GOPLS_PATH 指向上面安装的 gopls
SEMANTIC_ENABLED=true
SEMANTIC_DATA_DIR=data/semantic
SEMANTIC_MAX_INDEX_CHARS=250000
SEMANTIC_MAX_SESSIONS=2
GOPLS_PATH=.tools/bin/gopls
```

模型调查会把会议内容、项目地图和它主动读取的代码片段发送给你配置的模型服务。私有项目必须先确认公司允许把这些数据发送给该服务。`AGENT_MAX_STEPS` 越大，复杂需求越不容易提前中断，但延迟和模型 Token 成本也会增加。不要把真实 Key 写入 `config/projects.json`、代码仓库或会议纪要。

DeepSeek V4 可配置 `MODEL_BASE_URL=https://api.deepseek.com` 和
`MODEL_NAME=deepseek-v4-pro`。程序会请求 JSON Output，并在空内容或非法
JSON 时按 `MODEL_JSON_RETRIES` 自动重试；HTTP 拒绝、网络超时和格式错误会
显示为不同日志，方便区分模型问题与本地断网。

Agent 循环参考 OpenAI 官方公开的 [Codex open agent harness](https://developers.openai.com/blog/codex-as-a-platform)：复用其“上下文 + 工具循环 + 明确边界 + 结构化结果”模式，但没有直接嵌入 Codex SDK，因为本项目当前使用 DeepSeek。精确代码导航使用 Serena 和对应语言服务器；`grep-ast` 仅在语义服务无法启用时提供低可信降级，不再把语法地图当作编译器级引用关系。

## 接入飞书

创建一个飞书企业自建应用并开启机器人能力。一个机器人通过不同会话绑定管理多个项目：

```text
订单项目群 -> project-tracker 机器人 -> order-system 代码、记忆与方案
CRM 项目群  -> project-tracker 机器人 -> crm 代码、记忆与方案
```

每个机器人按实际需要授权：

- 接收群聊中 @ 机器人的消息；
- 读取用户明确提供的新版文档；
- 创建及编辑新版文档；
- 发送消息。

先复制机器人绑定样例：

```bash
cp config/feishu-bots.example.json config/feishu-bots.json
```

每条绑定只保存环境变量名，不把真实凭证写进 JSON：

```json
{
  "bot_id": "project-tracker-bot",
  "callback_key": "project-tracker",
  "transport": "websocket",
  "app_id_env": "FEISHU_PROJECT_TRACKER_APP_ID",
  "app_secret_env": "FEISHU_PROJECT_TRACKER_APP_SECRET",
  "tenant_domain": "https://your-company.feishu.cn"
}
```

对应环境变量：

```dotenv
FEISHU_BOTS_FILE=config/feishu-bots.json
FEISHU_PROJECT_TRACKER_APP_ID=cli_xxx
FEISHU_PROJECT_TRACKER_APP_SECRET=从密钥管理系统注入
```

在每个飞书应用的开发者后台完成：

1. 开启机器人能力并添加所需权限；
2. 在“事件与回调/事件配置”中选择“使用长连接接收事件”；
3. 添加事件 `im.message.receive_v1`（接收消息）；
4. 创建并发布应用版本，然后把机器人加入项目群。

长连接模式不填写请求地址，不需要公网域名、内网穿透或 Verification Token。App ID 和 App Secret 仍只放在 `.env`。程序启动后会为所有 `transport=websocket` 的机器人分别建立连接。

`callback_key` 必须唯一。机器人配置不再包含固定 `project_id`；会话绑定保存在 SQLite 中，重启后仍然有效。

在机器人单聊或项目群中绑定真实 GitHub 项目：

```text
@机器人 绑定项目 https://github.com/owner/repository
```

也可以指定分支：

```text
@机器人 绑定项目 https://github.com/owner/repository develop
```

机器人会读取 GitHub 仓库元数据，自动注册项目、识别默认分支、保存当前会话绑定并开始首次代码索引。私有仓库需要在 `.env` 中配置只有读取权限的 `GITHUB_TOKEN`。

管理命令：

```text
@机器人 当前项目
@机器人 项目列表
@机器人 解绑项目
@机器人 绑定项目 已注册项目名
```

在项目群里直接发送纪要正文或文档链接，不再包含项目名：

```text
@订单系统技术助手 https://your-company.feishu.cn/docx/文档ID
```

也兼容可选的“方案”前缀：

```text
@订单系统技术助手 方案 订单详情增加重新发送通知按钮
```

普通纪要优先使用当前会话的项目绑定。未绑定时，只有消息中明确出现唯一项目名、别名或已注册 GitHub 地址才会自动识别并保存绑定；识别不到或同时匹配多个项目时会停止并提示绑定，不会让模型猜项目。

`chat_project_bindings` 保存“机器人 + 会话 -> 项目”关系；`repository_files`、`repository_snapshots`、`memory_entries` 和 `proposals` 都以 `project_id` 分区，因此切换项目不会混用代码索引、长期记忆或历史方案。

同一个飞书消息 ID 会去重。收到普通纪要后，机器人会先回复“已收到，正在分析”，完成后再发送技术方案文档链接。

本地终端会同步显示只读调查进度，例如“读取版本化项目理解索引”“语义读取 controller/user.go 的符号概览”“语义追踪某个函数的引用”“读取真实源码”和最终建议数量；日志只显示工具摘要，不打印模型密钥或整段源码。

绑定机器人默认允许它所在群聊或与它单聊的用户触发，不需要额外用户登录或角色系统。如需限制某个机器人，可在绑定中配置 `allowed_chat_ids`、`allowed_user_ids`，或使用 `allowed_chat_ids_env`、`allowed_user_ids_env` 引用环境变量。

旧的 `transport=webhook`、`POST /webhook/feishu/{callback_key}` 和全局 `FEISHU_APP_ID` 等配置继续保留，仅用于兼容已有服务器部署；只有 webhook 模式需要 `verification_token_env`。

## 本地仓库变化与旧方案

仓库同步完成后，由现有 Git/CI Webhook 调用：

```bash
curl --noproxy '*' -sS http://127.0.0.1:8787/api/projects/refresh \
  -H 'Content-Type: application/json' \
  -d '{"project":"订单系统"}'
```

服务重新计算只读代码快照，并把基于旧版本生成的方案标记为 `stale`。GitHub 项目应使用上面的原生签名 Webhook。

飞书接口依据：[读取新版文档纯文本](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/raw_content)、[创建新版文档](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/create)、[创建文档块](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document-block/create)、[发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create)。

## Docker

```bash
docker compose up --build -d
curl --noproxy '*' -fsS http://127.0.0.1:18787/health
```

镜像以非 root 用户运行、只读根文件系统、删除全部 Linux capabilities。GitHub 模式不需要挂载代码仓库；只有使用本地兼容模式时才需要把仓库以 `:ro` 挂载。

## 当前边界和下一阶段

已经实现一个飞书机器人管理多个项目、会话级项目绑定、GitHub 直接读取、增量索引、签名 Webhook、项目隔离的长期记忆、按 commit 隔离的 Serena/语言服务器索引，以及“只读调查到技术经理改动建议”。下一阶段只提高只读分析质量：

- GitHub App installation token 在服务内部自动刷新；
- 大型多仓环境可选接入 Sourcegraph/SCIP，把本地语言服务器导航升级为集中式索引；
- 飞书卡片审批；

本产品不修改业务代码，不创建分支、Commit 或 PR，也不执行合并和部署。技术方案中的“修改建议”和“测试建议”都是只读分析结果，必须与已经验证的当前代码事实明确区分。
