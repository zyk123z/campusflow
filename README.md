# CampusFlow

> 面向高校学生事务的事件驱动多 Agent 智能服务平台。

![CampusFlow 校园智能服务主视觉](app/static/assets/campusflow-campus-companion.png)

CampusFlow 支持校园政策咨询、事务办理、投诉升级和紧急事件处置。系统通过事件驱动 Agent Runtime 完成意图路由、风险分级、上下文聚合、RAG 检索、Skill 加载、候选回复审查与工单工具规划。

**项目状态：** v0.2.0 演示/研究版 · Python 3.11/3.12 · 33 项自动化测试 · Docker 可运行

## 核心能力

- 事件驱动多 Agent：CoordinatorAgent 维护共享任务板，Agent 按能力 claim 任务并发布 artifact。
- Agent Runtime Harness：统一处理输入脱敏、会话解析、Agent 调用、报告落库、Trace 和工具计划。
- 校园事务路由：支持 `CHAT`、`POLICY_QUERY`、`SERVICE_REQUEST`、`COMPLAINT`、`EMERGENCY`。
- 事件分级：输出事务类别、紧急程度、负责部门、风险等级、置信度和摘要。
- 混合 RAG：Chroma 向量召回、BM25 关键词召回、分数融合、本地 rerank 和相邻知识块扩展。
- 分层记忆：Redis 保存短期脱敏消息，MySQL 负责长期回退，Prompt 使用摘要和最近原文窗口。
- 标准 Skill：运行时扫描 `skills/*/SKILL.md`，按意图、风险和关键词加载校园办事流程。
- MCP 工具：暴露台账写入、工单创建、紧急通知、工单确认和备注追加等工具。
- 数据库异步队列：支持任务依赖、限流、失败重试、Dead Letter、重启恢复和工具审计。
- 工单 SLA：根据 `URGENT`、`PRIORITY`、`ROUTINE` 计算 5 分钟、4 小时、24 小时处理时限。
- Engineering Harness：覆盖 Risk Safety、Agent Routing、Skills、RAG、API、Tool Queue 六条链路。

## 技术栈

```text
Python / FastAPI / SQLAlchemy / MySQL / Redis / Chroma
RAG / Skill / MCP / SSE / Docker / Basic Auth / Harness Engineering
```

## 系统流程

```text
学生输入
  -> HTTP / SSE
  -> CampusFlowAgentHarness
       -> 隐私脱敏与会话解析
       -> EventDrivenAgentRuntimeService
            -> UnderstandingAgent 发布 intent artifact
            -> SafetyAgent 发布 risk artifact / SAFETY_OVERRIDE
            -> ContextAgent 聚合记忆、RAG 和 Skill
            -> ResponseAgent 发布 response_proposal
            -> SafetyAgent 审查候选回复方案
            -> CoordinatorAgent 最终采纳
       -> 校园事件报告与 Agent Trace 落库
       -> 返回结构化 AgentHarnessOutcome
  -> AiClient 生成并流式发送学生端回复
  -> 保存助手消息
  -> 队列或 MCP 执行台账、工单和通知工具
```

## Agent 分工

| Agent | 职责 |
|---|---|
| CoordinatorAgent | 维护任务板、轮次预算、优先级、安全门槛和最终采纳 |
| UnderstandingAgent | 识别普通聊天、政策咨询、事务办理、投诉和紧急事件 |
| SafetyAgent | 独立评估风险，紧急事件发布 `SAFETY_OVERRIDE`，审查回复方案 |
| ContextAgent | 加载历史记忆、校园知识、RAG 证据和业务 Skill |
| ResponseAgent | 根据黑板 artifact 组装候选回复 Prompt |

## 校园领域模型

事务类别：

```text
GENERAL / ACADEMIC_AFFAIRS / STUDENT_AFFAIRS / DORMITORY
CAMPUS_CARD / FINANCIAL_AID / FACILITY_HAZARD
CAMPUS_SECURITY / COMPLAINT
```

负责部门：

```text
SERVICE_CENTER / ACADEMIC_AFFAIRS / STUDENT_AFFAIRS
LOGISTICS / SECURITY / FINANCE / IT_CENTER
```

紧急程度与默认 SLA：

| 紧急程度 | SLA |
|---|---:|
| `URGENT` | 5 分钟 |
| `PRIORITY` | 4 小时 |
| `ROUTINE` | 24 小时 |

## 快速开始

需要 Python 3.11 或 3.12、Docker Desktop 以及 [Ollama](https://ollama.com/)。

### 1. 创建本地配置

```powershell
Copy-Item .env.example .env
```

`.env.example` 默认使用本地 Ollama，且关闭需要 Embedding API 的向量检索；系统仍会使用 BM25 和本地 rerank。如需 OpenAI-compatible 服务，请在本地 `.env` 中配置，不要提交真实密钥。

### 2. 启动基础服务

```powershell
docker compose up -d mysql redis
ollama pull qwen2.5:7b
```

默认宿主机端口为 MySQL `127.0.0.1:13306`、Redis `127.0.0.1:16379`。如果 Ollama 尚未作为后台服务运行，请另开终端执行 `ollama serve`。

### 3. 安装并启动 CampusFlow

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

macOS/Linux 将激活命令替换为 `source .venv/bin/activate`。启动后打开 <http://127.0.0.1:8080>。

Windows/macOS 的 Docker Desktop 用户也可以在准备好 `.env` 和 Ollama 后直接执行：

```powershell
docker compose up --build
```

演示账号：

```text
学生：student / student123
管理员：admin / admin123
```

> 上述账号和密码只用于本地演示。部署到共享或公网环境前，请阅读 [SECURITY.md](SECURITY.md) 并替换身份验证方案。

## RAG 知识库

启动时会把 `app/knowledge/*.md` 同步到 `KnowledgeChunk`，当前覆盖校园事务总则、教务、奖助学金、宿舍报修、校园卡、投诉申诉、校园紧急事件和隐私边界。

默认检索配置：

```text
chunk_size = 512
chunk_overlap = 64
candidate_k = 16
top_k = 4
vector_weight = 0.65
bm25_weight = 0.35
```

未配置向量 Embedding 时，系统自动回退到 BM25 与本地 rerank。当前向量路径使用 OpenAI-compatible `/embeddings` 接口；聊天使用 Ollama 不代表向量检索自动使用 Ollama。

## Standard Skills

```text
policy_answer_baseline
dormitory_repair
campus_card_loss
scholarship_application
complaint_handling
campus_emergency_response
staff_handoff_summary
```

Skill 负责规定回答流程和业务边界；RAG 负责提供校园知识，两者由 ContextAgent 分别加载。

## MCP 与异步工具队列

MCP Server：

```powershell
python -m app.mcp_tools.server
```

暴露工具：

```text
campus_ledger_write
campus_ticket_create
campus_emergency_alert
campus_alert_ack
campus_ticket_note_add
campus_staff_notify
```

队列开启：

```text
Harness -> ToolJob(PENDING) -> Dispatcher -> Executor
        -> ToolGovernanceService -> ToolOrchestrationService
```

队列关闭：

```text
Harness -> CampusFlowMcpToolClient -> MCP Server
        -> ToolOrchestrationService
```

两条路径最终复用同一套业务工具实现。队列路径不会经过 MCP Server；MCP 是标准工具暴露入口，队列是可靠后台执行机制。

## 测试与评测

单元测试：

```powershell
$env:AI_PROVIDER = "mock"
$env:DATABASE_URL = "sqlite:///:memory:"
$env:KNOWLEDGE_VECTOR_ENABLED = "false"
python -m unittest discover -s tests -v
```

完整 Engineering Harness：

```powershell
python -m app.harness.runner --suite all
```

四个首页演示问题可以分别用可重复的 mock 回答和本地 Ollama 真实回答验证：

```powershell
python -m app.harness.runner --suite demo
python -m app.harness.runner --suite demo --provider ollama
```

独立 RAG 评测：

```powershell
python -m app.rag_eval.runner
```

当前校园 RAG Harness 使用 64 条 case，`topK=4`。最近一次本地验证结果：

```text
Recall@4 = 1.0000
HitRate = 1.0000
MRR = 0.9922
NDCG@4 = 0.9715
Precision@4 = 0.5273
```

这些指标只表示检索效果，不代表最终回答准确率。修改知识库、切块、权重或 rerank 后需要重新运行。

## 目录结构

```text
app/main.py          FastAPI应用入口
app/api/             HTTP路由和SSE入口
app/agents/          事件驱动多Agent Runtime与Harness
app/services/        AI、记忆、RAG、Skill、工具队列等服务
app/models/          SQLAlchemy实体
app/schemas/         请求、响应和内部DTO
app/knowledge/       校园事务RAG文档
app/mcp_tools/       FastMCP工具服务
app/rag_eval/        校园RAG评测集和Runner
app/harness/         Engineering Harness
app/static/          学生端和管理员端
skills/              CampusFlow标准Skill
tests/               unittest回归测试
scripts/             启动与发布脚本
```

## 说明

- `models/` 下的本地 GGUF 文件不参与 CampusFlow 默认运行，已被 Git 和发布打包脚本排除。
- 当前 SafetyAgent 审查的是 `response_proposal` 中的候选 Prompt；如果要宣称“最终输出审查”，还需要在模型生成完整文本后增加一次合规审查。
- 生产环境应替换演示账号、配置 HTTPS、使用正式身份系统，并为多实例队列增加数据库级任务抢占机制。

## 参与与安全

- 参与开发请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。
- 漏洞报告与生产部署注意事项请阅读 [SECURITY.md](SECURITY.md)。
- 仓库尚未附带开源许可证；除非仓库所有者后续明确添加，否则不默认授予复制、修改或分发权利。
