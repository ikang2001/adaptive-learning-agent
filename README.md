# 千人千案 Agent Harness

面向考研专业课复习的智能学习计划与 Agent Harness 项目。当前演示学科为“自动控制原理”，系统围绕目标院校考纲，把课程学习、外部讲义练习、学习反馈、章节真题、专项强化和全真模拟串成可追踪的个人学习闭环。

项目同时提供 FastAPI 后端与 React 体验前端。业务规则负责可确定的计算、状态和约束；Agent 只处理异常诊断、复杂调整建议和需要多工具组合的任务，不能绕过 Guardrail 直接修改核心业务数据。

当前版本：`v0.1.0`（首个可体验版本）。

## 核心能力

### 学习计划本

- 首次创建一个长期活动计划，页面滚动展示未来 7 天。
- 基础阶段只安排课程章节、外部讲义、错题复盘和知识总结，不把站内刷题当成新人主线。
- 支持周一至周日可用时间模板、特殊日期覆盖、历史任务查询和逾期保留。
- 学生可编辑、移动、新增或跳过未完成任务；已完成任务不可修改。
- 学习反馈会按“任务类型 × 知识点”校准个人 P50/P75 学习效率。
- Agent 只能自动微调同知识点、同任务类型的未来任务时长或工作量；跨日、换知识点、删除任务和阶段切换必须等待学生确认。

### 真题与专项强化

- 章节真题、整卷真题、专项强化和全真模拟的数据分别记录，不互相污染。
- 章节按目标院校考纲顺序展示，专项强化选择的是“第一章、第二章……”等一级章节。
- 专项卷 60% 题量集中在所选章节，章内题目根据细知识点的真题错误、正确率和证据量加权推荐。
- 真题得分低于 60% 会记录错误标记并更新细知识点真题画像，成为后续专项推荐证据。
- 完成章节要求的学习任务并确认强化后，解锁相关历年真题；完成当前题库版本的全部章节真题后，解锁该章专项强化。
- 全部考纲章节确认强化完成，并由学生批准阶段建议后，才解锁院校全真模拟。

### Agent Harness

- 单 Agent 架构，具备模型路由、工具注册、策略保护、Checkpoint、Retry、Trace 和终止策略。
- 默认最多 8 轮、12 次工具调用，单次模型读取超时 120 秒，Run 总时限 10 分钟。
- 连续重复相同动作或两轮没有新增证据时，以 `LOOP_STALLED` 终止。
- 读工具只对瞬时错误有限重试；写工具只有携带幂等键时才允许重试。
- Flash 输出不合法或置信度低于 0.75 时最多升级一次 Plus；确定性规则走 Zero-Token 分支。
- Worker 采用至少一次投递，通过数据库唯一约束、Run lease 和幂等记录避免重复提交。

### 内容与审核

- 支持 PDF、DOCX、Markdown、JPG 和 PNG 学习资源上传。
- 文本文档优先本地解析，扫描页或图片可使用千问视觉能力兜底。
- 资源章节和知识点映射必须经 Reviewer/Admin 审核发布后，才能进入学习计划。
- 题库不足时可生成 AI 候选题，但试卷会停留在 `WAITING_FOR_REVIEW`，任何 AI 题都不会自动发布。

### 身份与安全

- 手机号验证码登录，Access Token 15 分钟，Refresh Token 30 天并执行轮换与复用检测。
- 手机号按 E.164 规范化，密文保存并使用 HMAC blind index 查询。
- 角色包括 `STUDENT`、`REVIEWER` 和 `ADMIN`，学生资源按所有者隔离。
- 创建类接口使用 `Idempotency-Key`；异步任务返回 `202 + job_id`。
- 账号删除先冻结，保留 30 天撤销期，到期任务清除个人数据、令牌、向量与可识别 Trace。
- API 错误遵循 RFC 9457 Problem Details。

## 技术栈

| 范围 | 主要技术 |
|---|---|
| 后端 | Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2 Async、Alembic |
| 数据 | PostgreSQL 17、pgvector、Redis 7 |
| 异步任务 | ARQ、数据库 Job/Outbox 事实源 |
| Agent/模型 | 自研 Harness、`httpx.AsyncClient`、千问兼容接口、FakeModelGateway |
| 前端 | React 19、TypeScript、Vite、TanStack Query |
| 可观测性 | Prometheus、Grafana、OpenTelemetry、JSON 结构化日志 |
| 质量门禁 | pytest、pytest-asyncio、Hypothesis、ruff、mypy、Vitest、ESLint |
| 部署 | Docker Compose、4 个 API 进程、4 个异步 Worker |

## 业务流程

```text
目标院校考纲与知识图谱
    → 课程章节学习
    → 外部讲义练习
    → 错题复盘 / 知识总结
    → 分类学习反馈
    → 校准个人学习效率
    → 确认章节强化完成
    → 章节历年真题
    → 专项强化
    → 全真模拟
```

Agent 只在异常反馈、复杂诊断、重大计划调整或题库不足时介入。普通反馈、计划容量、掌握度和解锁条件优先使用确定性规则。

## 项目结构

```text
.
├─ app/
│  ├─ api/                 # FastAPI 路由、依赖与请求/响应 Schema
│  ├─ application/         # 用例编排、学习计划、真题、组卷与审核服务
│  ├─ domain/              # 不依赖框架的领域规则与算法
│  ├─ harness/             # Agent Runner、Guardrail、Trace、Checkpoint 等
│  ├─ infrastructure/      # 数据库、Redis、短信、模型与工具 Adapter
│  └─ workers/             # ARQ Worker 与 Outbox Dispatcher
├─ alembic/                # 静态数据库迁移
├─ frontend/               # React + TypeScript 体验前端
├─ monitoring/             # Prometheus、Grafana、OTel Collector 配置
├─ benchmarks/             # Agent 固定轨迹 benchmark 数据
├─ scripts/                # 构建、压测、benchmark 与模型 smoke 脚本
├─ tests/                  # 单元测试与 PostgreSQL/Redis 集成测试
├─ compose.yaml
└─ pyproject.toml
```

后端依赖方向：

```text
API / Worker
    → Application UseCase
        → Domain / Planning Strategy
        → Agent Facade / Harness
            → Tool Port
    → Repository / Model / SMS / Queue Port
        ← Infrastructure Adapter
```

## 快速体验

### 环境要求

- Windows 10/11 或 Linux/macOS。
- Docker Desktop，支持 Docker Compose v2。
- 推荐至少 8 GB 可用内存。
- Windows C 盘空间不足时，可按示例把构建缓存、数据库和资源文件放到 D 盘。

### 1. 准备环境变量

PowerShell：

```powershell
Copy-Item .env.example .env
```

首次启动前请修改 `.env` 中的以下本地密钥：

- `JWT_SECRET`
- `OTP_HMAC_SECRET`
- `PII_HMAC_SECRET`
- `PII_ENCRYPTION_KEY`

生成 Fernet 格式的手机号加密密钥：

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

将输出填入 `PII_ENCRYPTION_KEY`。本地体验可保留：

```dotenv
SMS_PROVIDER=fixed
FIXED_SMS_CODE=246810
USE_FAKE_MODEL=true
```

### 2. 构建镜像

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_image.ps1
```

构建脚本默认在 `D:\CodexTemp\qianrenqianan` 创建临时上下文，减少 C 盘占用。

### 3. 初始化数据库与演示数据

```powershell
docker compose up -d postgres redis
docker compose run --rm api alembic upgrade head
docker compose run --rm api learning-agent seed-demo
```

Seed 命令可重复执行，不会重复导入。演示数据包括：

- 2 个虚拟目标院校：`DEMO-801`、`DEMO-802`。
- 18 个知识节点和前置知识关系。
- 72 道原创练习题。
- 3 套各 10 题的虚拟真题。
- 虚拟课程与讲义目录。
- 150 分、180 分钟的院校试卷画像。

### 4. 启动完整服务

```powershell
docker compose up -d
```

| 服务 | 地址 |
|---|---|
| React 体验前端 | <http://localhost:18002> |
| FastAPI Swagger | <http://localhost:18001/docs> |
| OpenAPI JSON | <http://localhost:18001/openapi.json> |
| Prometheus | <http://localhost:9090> |
| Grafana | <http://localhost:3000> |

本地固定验证码为 `246810`。前端默认手机号可直接用于体验。

### 5. 健康检查

```powershell
Invoke-RestMethod http://localhost:18001/health/live
Invoke-RestMethod http://localhost:18001/health/ready
```

Prometheus 指标位于 <http://localhost:18001/metrics>。

## 推荐体验路径

1. 使用手机号和固定验证码登录。
2. 选择 `DEMO-801`，填写考试日期和每周可用时间。
3. 创建学习计划本，查看未来 7 天的课程、讲义和总结任务。
4. 在“今日执行”提交章节进度、实际时长、外部题量和正确率。
5. 完成知识点学习任务并确认强化完成。
6. 在“真题”中完成章节真题，观察错误画像变化。
7. 在“模拟卷”中按考纲章节选择专项强化范围。
8. 查看章内细知识点推荐依据；满足解锁条件后发起组卷。
9. 使用 Reviewer/Admin 审核资源映射或 AI 候选题。
10. 通过 Agent 页面查看 Run、Step、Tool Trace、Checkpoint 和 Proposal。

## 角色授权

用户首次登录后，可在本地通过 CLI 授予 Reviewer 或 Admin：

```powershell
uv run learning-agent grant-role --phone +8613800138000 --role REVIEWER
uv run learning-agent grant-role --phone +8613800138000 --role ADMIN
```

生产环境应改为受审计的管理流程，不应暴露此类本地运维命令给普通用户。

## 主要 API

所有业务接口统一使用 `/api/v1` 前缀。

| 领域 | 代表接口 |
|---|---|
| 身份 | `/auth/sms-codes`、`/auth/sessions`、刷新令牌、登出、账号删除 |
| 学生 | `/me/student-profile`、`/me/availability-template`、可用时间覆盖 |
| 计划 | `/me/plans`、`/me/plans/current`、`/plans/{id}/tasks`、计划变更记录 |
| 今日执行 | `/me/tasks/today`、`/tasks/{id}/feedback` |
| 解锁 | `/me/learning-unlocks`、`/me/specialized-scopes`、章节强化确认 |
| 真题 | `/me/true-exams`、章节 Session、整卷提交、真题画像 |
| 模拟卷 | `/me/mock-exams`、组卷任务查询、模考提交 |
| 资源 | `/resources/uploads`、导入状态、资源审核与发布 |
| Agent | Run、Step、Tool Trace、Proposal 查询与确认 |
| 运维 | `/health/live`、`/health/ready`、`/metrics` |

创建、批量编辑和阶段确认类接口要求 `Idempotency-Key`。异步操作先返回 `202`，客户端通过 `/api/v1/jobs/{job_id}` 查询状态：

- `QUEUED`、`RUNNING`：任务处理中。
- `SUCCEEDED`：完成，可读取结果。
- `WAITING_FOR_REVIEW`：等待人工审核候选内容。
- `FAILED`：失败，可根据错误码决定是否重试。

## 本地开发

### 后端

推荐把 uv 环境和临时目录放到 D 盘：

```powershell
$env:UV_CACHE_DIR='D:\CodexTemp\qianrenqianan\uv-cache'
$env:UV_PROJECT_ENVIRONMENT='D:\CodexTemp\qianrenqianan\.venv'
$env:TMP='D:\CodexTemp\qianrenqianan\tmp'
$env:TEMP=$env:TMP

uv sync --dev
uv run uvicorn app.main:app --reload --port 8000
```

### 前端

```powershell
Set-Location frontend
npm ci
npm run dev
```

开发模式下请根据本地代理配置访问后端；Docker 体验环境已配置 Nginx 反向代理。

## 测试与质量门禁

### 后端

```powershell
uv run ruff format --check .
uv run ruff check .
uv run mypy app scripts
$env:RUN_INTEGRATION='1'
uv run pytest --disable-warnings
uv run python scripts/run_benchmark.py
```

### 前端

```powershell
Set-Location frontend
npm run lint
npm run typecheck
npm run test
npm run build
```

`v0.1.0` 发布前的验证基线：

- 后端 24 项自动化测试通过。
- 前端 6 项组件/工具测试通过，ESLint、TypeScript 和生产构建通过。
- Agent benchmark 20 条固定轨迹全部通过，决策准确率和终止成功率均为 100%。
- Alembic 支持空库全量升级、降级到 base、再次升级到 head。
- Docker 运行态已验证 API ready、4 个 Worker、Dispatcher、前端资源和章节专项接口。

CI 只使用 `FakeModelGateway`，不会消耗真实模型额度。

## 千问模型

默认模型配置：

```dotenv
QWEN_PLUS_MODEL=qwen3.7-plus-2026-05-26
QWEN_FLASH_MODEL=qwen3.7-flash-2026-07-15
QWEN_EMBEDDING_MODEL=qwen3.7-text-embedding
```

需要验证真实千问接口时：

```powershell
uv run python scripts/live_model_smoke.py
```

脚本可以读取被 Git 忽略的 `实施文档/env.txt`，不会打印 API Key。不得提交 `.env`、`实施文档/env.txt`、Token、手机号明文或模型密钥。

## 生产环境注意事项

- 禁止使用 `SMS_PROVIDER=fixed`。
- 禁止使用 `USE_FAKE_MODEL=true`。
- 必须通过密钥管理系统注入 JWT、HMAC、PII 加密和模型密钥。
- 应将 PostgreSQL、Redis 和对象文件存储替换为具备备份、监控和恢复能力的生产服务。
- 根据模型供应商限额调整 Worker 并发，避免重试放大流量。
- 上线前配置真实短信 Adapter、允许来源、TLS、反向代理、告警接收人和备份恢复演练。
- Grafana 示例密码只用于本地环境，生产必须修改。

## 常见问题

### 页面仍显示旧版训练任务

先确认已升级到最新迁移并重建 API/前端镜像：

```powershell
docker compose run --rm api alembic upgrade head
powershell -ExecutionPolicy Bypass -File .\scripts\build_image.ps1
docker compose up -d --force-recreate api worker dispatcher frontend
```

然后在浏览器中按 `Ctrl+F5` 强制刷新。旧题目型任务只保留在数据库审计记录中，不会进入活动计划、容量计算或 Agent 自动调整链路。

### 专项强化按钮仍然锁定

专项强化以目标院校一级章节为范围。需要完成本章相关知识点的学习任务、确认强化，并完成当前版本的全部章节真题。章节卡片会显示缺少的真题进度。

### 组卷一直显示“正在求解约束”

只有 `QUEUED` 和 `RUNNING` 会显示进度。`SUCCEEDED` 会展示新试卷，`WAITING_FOR_REVIEW` 会提示等待审核，`FAILED` 会显示错误和重试入口。若页面仍保留旧状态，请强制刷新并检查 `/api/v1/jobs/{job_id}`。

### C 盘空间不足

在 `.env` 中设置：

```dotenv
DATA_ROOT=D:/CodexTemp/qianrenqianan/docker
RESOURCE_STORAGE_ROOT=D:/CodexTemp/qianrenqianan/resources
```

构建脚本和 README 中的 uv 配置也会把缓存与临时文件放到 D 盘。

## 当前范围与限制

当前版本用于产品体验、架构验证和后端联调，暂不包含：

- 真实院校资料和受版权保护的题库。
- 真实短信供应商 Adapter。
- 机构多租户和复杂组织权限。
- Kubernetes、自动扩缩容和跨地域容灾。
- PDF 试卷 OCR 批量导入流水线。
- 多 Agent 协作、强化学习或 AI 题目自动发布。

演示内容均为原创虚拟数据或参数化模板，不应当作真实院校备考资料。

## 版本说明

### v0.1.0

- 完成手机号登录、权限隔离、账号删除与会话轮换。
- 完成滚动学习计划本、学生编辑、分类反馈和效率校准。
- 完成知识点强化、章节真题、专项强化与全真模拟解锁链路。
- 完成基于真题错误画像的章内细知识点推荐。
- 完成 Agent Harness、Checkpoint、Trace、Retry、Guardrail 和终止策略。
- 完成资源上传解析、知识映射审核和 AI 候选题审核。
- 完成 React 体验前端、Docker Compose、迁移、测试、benchmark 与可观测性配置。

## 关键架构边界

- Domain 不依赖 FastAPI、SQLAlchemy、Redis、模型 SDK 或 Harness。
- Agent 只调用读工具和 `propose_*` 工具，不能直接修改掌握度、计划、题库或学生阶段。
- Redis 只负责投递，`background_jobs` 和 `domain_events` 是任务事实源。
- 真题、普通练习和模拟卷分别存储，只在 Student Model 聚合。
- AI 生成内容必须经过 Reviewer/Admin 审核，不存在自动发布后门。
