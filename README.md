# Project Tracker Agent

Project Tracker Agent 是一个给产品、项目经理和研发团队使用的飞书机器人。它连接 GitHub 仓库，把会议纪要或一句需求翻译成一份基于当前代码的技术改动建议。

它不替研发写代码，也不创建分支和 PR。它做的是技术经理通常要做的前半段工作：先理解需求，再去仓库里找相关模块、接口和调用关系，最后告诉团队这次改动大概要落在哪里、会牵涉什么、哪些地方需要确认。

## 它有什么用

### 开完会，直接得到能交给研发的技术说明

产品会议里经常只有这样的描述：

> 用户资料页增加修改头像，上传后立即显示新头像，失败时给出提示。

机器人会结合当前绑定项目的真实代码继续调查，输出类似下面的内容：

```text
需求理解
- 用户可以在资料页上传并替换头像
- 上传成功后刷新当前用户信息
- 文件类型、大小限制和旧头像清理策略尚待确认

建议改动位置
- backend/.../user_controller.go：现有用户资料接口入口，可在这里增加头像上传接口
- backend/.../user_service.go：用户资料更新逻辑，适合处理头像地址持久化
- frontend/.../ProfilePage.tsx：资料页入口，可增加上传按钮和进度状态
- frontend/.../userApi.ts：现有用户接口封装，应在这里增加上传请求

影响和风险
- 需要确认对象存储的上传方式和访问权限
- 需要校验 MIME 类型、文件大小以及登录用户身份
- 用户信息存在缓存时需要同步失效

建议验证
- 上传合法图片成功
- 非图片和超大文件被拒绝
- 未登录用户不能修改头像
- 上传失败时页面状态可以恢复

代码版本
- commit: 具体 SHA
```

文件、符号和版本必须来自机器人实际读到的仓库。找不到可靠证据时，它会标成“推断”或“待确认”，而不是编一个位置。

### 不用每次重新向模型介绍项目

项目第一次接入后，程序会建立文件和符号索引，并保存一份按代码版本区分的项目地图。以后再讨论同一个项目，机器人先读取已有索引，只打开与当前需求有关的源码，不会每次把整个仓库重新发给模型。

长期信息也会保存，例如：

- 已确认的产品规则和技术约束；
- 上次会议留下的待确认问题；
- 某个功能对应过哪些代码位置；
- 历史方案基于哪个 commit 生成。

代码更新后，旧的代码事实会被标记为过期；产品决定仍然保留。这样能减少重复上下文，也避免拿旧代码位置回答新问题。

### 一个机器人可以管多个项目

机器人按飞书会话保存项目绑定：

```text
订单项目群  -> order-system 仓库、记忆和方案
CRM 项目群   -> crm 仓库、记忆和方案
机器人单聊  -> 当前单聊绑定的项目
```

项目之间的代码索引、长期记忆和方案历史分开存储。群里绑定一次后，后面的纪要不需要反复写项目名；需要时也可以解绑或切换。

### GitHub 有变化，项目理解也会更新

公开仓库可以直接读取，私有仓库使用只读 Token。首次接入会下载当前版本并建立索引，后续同步只处理发生变化的文件。

可以手动同步，也可以配置 GitHub Push Webhook。目标分支有新提交后，机器人会记录新的 commit、更新索引，并把依赖旧版本的方案标成过期。

### 适合这些场景

- 产品会议结束后，把纪要整理成研发能继续细化的技术方案；
- 需求评审前，先确认一个功能可能涉及哪些前后端模块；
- 新成员接手项目时，快速找到入口、服务、数据模型和测试位置；
- 多个项目共用一个飞书机器人，但不混用各自的上下文；
- 在不授予代码写权限的前提下，让模型调查私有仓库。

## 平时怎么用

先在需要使用的飞书群里绑定仓库：

```text
@机器人 绑定项目 https://github.com/owner/repository
```

指定分支时：

```text
@机器人 绑定项目 https://github.com/owner/repository develop
```

绑定成功后，机器人会回复项目名称和分支，并开始首次同步。以后直接发需求或会议纪要：

```text
@机器人 用户资料页增加修改头像，上传成功后马上显示新头像。
```

也可以发飞书文档链接：

```text
@机器人 https://your-company.feishu.cn/docx/文档ID
```

机器人先回复“已收到，正在分析”，完成后发送技术方案或飞书文档链接。常用命令还有：

```text
@机器人 当前项目
@机器人 项目列表
@机器人 解绑项目
@机器人 绑定项目 已注册项目名
```

如果会话没有绑定项目，机器人只会在消息里出现唯一项目名、别名或已注册 GitHub 地址时自动识别。匹配不到或同时匹配多个项目时，它会要求先绑定，不会让模型猜。

## 它不会做什么

这是一个只读的方案工具，当前版本不会：

- 修改业务代码；
- 创建分支、Commit 或 PR；
- 合并代码或执行部署；
- 执行会议纪要、Issue 或代码注释里的命令；
- 把未经验证的文件位置当作确定事实。

模型能调用的仓库工具只有列文件、读文件、查看符号、查定义、查引用和只读搜索。最终方案里的文件和行号还会由程序再检查一次。

---

## 本地配置

下面是从空环境到飞书里可用的一套完整步骤。需要 Python 3.11 或更高版本；Go 项目建议同时安装 Go 和 `gopls`，其他受 Serena 支持的语言会使用相应语言服务器。

### 1. 安装

```bash
git clone https://github.com/vertindWD/project-tracker-agent.git
cd project-tracker-agent

python3 -m venv .venv
.venv/bin/pip install -e .

cp .env.example .env
cp config/feishu-bots.example.json config/feishu-bots.json
```

如果要分析 Go 项目，再安装 `gopls`：

```bash
mkdir -p .tools/bin
GOBIN="$PWD/.tools/bin" go install golang.org/x/tools/gopls@v0.23.0
```

Windows PowerShell 可以把 `.venv/bin/python` 换成 `.venv\Scripts\python.exe`，把复制命令换成 `Copy-Item`。

### 2. 配置模型

编辑 `.env`。模型接口需要兼容 OpenAI Chat Completions：

```dotenv
MODEL_BASE_URL=https://api.deepseek.com
MODEL_API_KEY=你的模型APIKey
MODEL_NAME=deepseek-v4-pro

# 模型返回空内容或非法 JSON 时重试几次，范围 0～4
MODEL_JSON_RETRIES=2

# 一次方案最多进行多少轮“决定下一步 + 读取代码”，范围 4～100
AGENT_MAX_STEPS=40
```

复杂项目可以提高 `AGENT_MAX_STEPS`，代价是分析时间和 Token 消耗增加。不配置模型时只能使用离线兼容结果，不能当作完整的代码调查。

私有代码会把项目地图和机器人主动读取的相关源码发送给这里配置的模型服务，接入前应确认公司的数据使用要求。

### 3. 创建飞书机器人

在飞书开放平台创建企业自建应用，然后：

1. 开启机器人能力；
2. 给应用开通接收消息、以机器人身份发送消息的权限；
3. 如果要生成飞书文档，再开通新版文档读取、创建和编辑权限；
4. 在“事件与回调”中选择“使用长连接接收事件”；
5. 添加事件 `im.message.receive_v1`；
6. 创建并发布应用版本，把机器人加入需要使用的群。

使用长连接时不需要公网地址、内网穿透或 Verification Token。

把飞书应用的凭证写入 `.env`：

```dotenv
FEISHU_PROJECT_TRACKER_APP_ID=cli_xxx
FEISHU_PROJECT_TRACKER_APP_SECRET=你的AppSecret
```

不要把真实 App Secret 或模型 Key 写入 README、JSON 或提交到 Git。

`config/feishu-bots.json` 默认可以保持为：

```json
{
  "bots": [
    {
      "bot_id": "project-tracker-bot",
      "callback_key": "project-tracker",
      "transport": "websocket",
      "app_id_env": "FEISHU_PROJECT_TRACKER_APP_ID",
      "app_secret_env": "FEISHU_PROJECT_TRACKER_APP_SECRET",
      "tenant_domain": "https://your-company.feishu.cn"
    }
  ]
}
```

这里保存的是环境变量名，不是真实凭证。一个后台也可以配置多个机器人，每个 `callback_key` 必须唯一。

### 4. 配置 GitHub

公开仓库不需要 Token。读取私有仓库时，在 `.env` 配置只有目标仓库读取权限的 fine-grained token：

```dotenv
GITHUB_TOKEN=你的只读Token
```

权限尽量只给 `Contents: read`。项目运行时不会向 GitHub 写代码。

如果不想在飞书里绑定，也可以提前注册：

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

然后执行首次同步：

```bash
.venv/bin/python -m tracker sync-github --project 订单系统
```

也可以参考 [`config/projects.github.example.json`](config/projects.github.example.json) 编写 `config/projects.json`。

### 5. 初始化并启动

```bash
.venv/bin/python -m tracker init
.venv/bin/python -m tracker serve --host 127.0.0.1 --port 8787
```

看到飞书长连接建立成功后，就可以在飞书里 @ 机器人绑定项目。服务需要一直保持运行；按 `Ctrl+C` 停止。

健康检查：

```bash
curl --noproxy '*' -fsS http://127.0.0.1:8787/health
```

终端会显示每次任务的进度，例如读取项目地图、查看符号、追踪引用、读取源码和生成建议。日志只输出调查摘要，不打印模型密钥或整段源码。

## GitHub 自动同步

本地试用可以手动运行 `sync-github`。部署到可被 GitHub 访问的服务器后，可以在仓库的 Webhook 设置中填写：

```text
Payload URL: https://你的服务域名/webhook/github
Content type: application/json
Secret: 与 GITHUB_WEBHOOK_SECRET 相同
Events: Push events
```

同时在 `.env` 配置一个随机密钥：

```dotenv
GITHUB_WEBHOOK_SECRET=随机生成的Webhook密钥
```

服务会验证 `X-Hub-Signature-256`、按 delivery ID 去重，只处理已注册仓库的目标分支。GitHub Tree 响应不完整时同步会直接失败，不会拿残缺索引继续生成方案。

当前使用静态 `GITHUB_TOKEN`。正式环境若使用会过期的 GitHub App installation token，需要在外部刷新 Token 并重启服务，或补充服务内的自动刷新逻辑。

## 项目理解和记忆怎么存

首次同步后，GitHub 内容会缓存在本地，代码快照位于 `data/semantic/snapshots/`。Serena 和语言服务器从快照建立文件、类、函数、方法等符号索引，不会把缓存写回用户仓库。

主要数据保存在 SQLite：

- `chat_project_bindings`：机器人和飞书会话当前绑定的项目；
- `project_maps`：按 `project_id + repository_version` 保存的项目地图；
- `repository_files`、`repository_snapshots`：文件索引与代码版本；
- `memory_entries`：产品决定、约束和经过确认的长期信息；
- `proposals`：历史方案及其对应 commit。

方案生成时，模型先看当前版本的项目地图，再通过只读工具继续调查真实源码。文本搜索只是语义服务不可用时的降级手段。每份方案都记录 commit SHA；GitHub 更新后会创建新快照，不会让旧索引冒充当前代码。

运行产生的数据库、代码缓存和方案都在 `data/` 下，已被 `.gitignore` 排除。

## 不接飞书也可以测试

项目自带一个小型示例仓库和会议纪要：

```bash
.venv/bin/python -m tracker init
.venv/bin/python -m tracker generate \
  --project 订单系统 \
  --notes-file examples/meeting-notes.txt
```

生成结果位于 `data/proposals/`。

也可以通过本地 HTTP 接口提交：

```bash
curl --noproxy '*' -sS http://127.0.0.1:8787/api/proposals \
  -H 'Content-Type: application/json' \
  -d '{
    "project": "订单系统",
    "meeting_notes": "订单详情页增加重新发送通知按钮，点击后显示成功或失败提示。"
  }'
```

接口返回 `job_id`，可继续查询：

```bash
curl --noproxy '*' -sS http://127.0.0.1:8787/api/jobs/替换为job_id
curl --noproxy '*' -sS http://127.0.0.1:8787/api/proposals/替换为proposal_id
```

## 本地仓库模式

除了 GitHub，也可以直接读取本机已经存在的仓库。先把允许读取的仓库根目录写入 `.env`：

```dotenv
ALLOWED_REPO_ROOTS=/company/repos
```

再注册仓库：

```bash
.venv/bin/python -m tracker register \
  --id order-system \
  --name 订单系统 \
  --repo /company/repos/order-system \
  --alias 订单 \
  --allowed-path frontend \
  --allowed-path backend
```

程序会拒绝白名单之外的路径，并跳过 `.git`、依赖目录、构建产物、`.env`、`secrets/` 和 `production/`。

## Docker

```bash
docker compose up --build -d
curl --noproxy '*' -fsS http://127.0.0.1:18787/health
```

镜像以非 root 用户运行，根文件系统只读，并删除 Linux capabilities。GitHub 模式不需要挂载代码仓库；本地仓库模式要把目标仓库以只读卷挂载，并加入 `ALLOWED_REPO_ROOTS`。

## 测试

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## 当前状态

已经实现：飞书长连接、会话级项目绑定、一个机器人管理多个项目、GitHub 直接读取和增量同步、签名 Webhook、按项目隔离的长期记忆、按 commit 隔离的 Serena/语言服务器索引，以及只读技术方案生成。

后续更值得做的是提高大型仓库的调查质量，例如接入 Sourcegraph/SCIP、增加 GitHub App Token 自动刷新，以及在飞书里增加方案确认卡片。产品边界仍然是只读分析，不增加代码修改、PR、合并或部署能力。

相关项目和接口文档：

- [Serena](https://github.com/oraios/serena)
- [Codex open agent harness](https://developers.openai.com/blog/codex-as-a-platform)
- [GitHub Tree API](https://docs.github.com/en/rest/git/trees)
- [GitHub Webhook 签名校验](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)
- [飞书新版文档接口](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/raw_content)
