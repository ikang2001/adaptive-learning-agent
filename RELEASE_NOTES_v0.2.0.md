# v0.2.0：企业级 Agent Harness 与 Evaluation 闭环

发布日期：2026-08-31

`v0.2.0` 在 `v0.1.0` 的业务闭环基础上，重点补齐 Agent 的可恢复性、并发安全、幂等执行、模型/Tool 可靠性、可观测性，以及可重复的 Evaluation/Bad Case 优化工程。

## 与 v0.1.0 相比的主要完善

### 1. Checkpoint / Resume

- Checkpoint 从“只保存”升级为 `save + load_latest + strict deserialize + hash verify`；
- RuntimeState 明确区分 `READY / TOOL_PENDING / FINAL_PENDING / TERMINATED`；
- Worker 中断后从最近安全阶段恢复；
- 已成功 Proposal 通过 Tool Ledger 重放结果，不重复业务副作用；
- 已完成 Run 重投时直接复用终态，不重新调用模型。

### 2. Lease / Fence

- AgentRun 增加 Lease Owner、Heartbeat、Lease Expiry 和 Fencing Token；
- 新 Worker 接管时单调递增 Token；
- 旧 Worker 无法继续写 Run、Checkpoint、Trace、Proposal 和 Tool Ledger；
- 增加 PostgreSQL 双 Worker/Fence 集成测试。

### 3. Tool 可靠性与安全

- 建立 Tool Validation、Permission、Business、Timeout、Rate Limit、Upstream、Unknown Outcome 错误分类；
- Retry 使用指数退避和 Jitter；
- 副作用 Tool 必须使用稳定幂等键；
- 新增 `tool_execution_records` Ledger，支持 STARTED/SUCCEEDED/FAILED/UNKNOWN；
- Tool 参数由 Pydantic 在服务端严格验证；
- Evidence 改由 Harness 从可信 Observation 自动绑定，避免模型复制或伪造 UUID；
- Application Service 再校验 Evidence 归属和版本。

### 4. 模型可靠性与成本控制

- Tool Call 与 Final Decision 严格互斥；
- 非法输出最多 Repair 一次；
- 每轮只允许一个 Tool Call；
- 显式传递 `completed_tools`，防止重复调用；
- Flash/Plus 支持低置信度升级、Timeout/429/5xx Fallback 和轻量 Circuit Breaker；
- Run 增加 Step、Model Call、Tool Call、Input/Output Token、Repair 和 Runtime Budget；
- Proposal Tool 成功后由 Runner 确定性完成 Run，避免冗余 Final 模型调用。

### 5. Job 与 Proposal 状态机

- BackgroundJob 增加 `RETRY_WAIT / DEAD_LETTER`、Attempt、Retry Time 和 Reconciliation；
- Proposal 增加 `APPLYING / APPLIED / APPLY_FAILED`；
- APPROVED 不再等同于业务应用成功；
- 支持 Agent Run 协作式取消和 Proposal 审批过期。

### 6. Trace、Replay 与可观测性

- 新增 ModelInvocation、GuardrailEvent 和增强 ToolInvocation；
- Agent 页面增加只读 Replay、取消和 Shadow Evaluation；
- API 多进程指标使用 Prometheus multiprocess；
- Worker 暴露内网 metrics 端点并由 Docker DNS 自动发现；
- 增加 Agent/Model/Tool/Retry/Budget/Resume/Dead Letter 指标、Grafana 面板和告警；
- 增加 `agent.run / model.decide / policy.validate / tool.execute / checkpoint.save` OTel Span。

### 7. Agent Evaluation 与 Bad Case 闭环

- 构建 1000 条 Evaluation v2 业务 Case；
- 覆盖正常分支、单/多异常、Evidence 不足、Prompt Injection、Tool 故障、Guardrail、Loop 和结构化输出；
- 输出逐 Case Trace、聚合指标、Wilson 95% CI、Bad Case JSONL/Markdown；
- 支持失败指纹去重、回归晋升和 Baseline/Candidate 对比；
- CI 自动生成 1000 Case 并执行 Fake Evaluation 门禁；
- 104 条分层真实 `qwen3.7-plus` 样本实测：
  - Decision Accuracy：96.875%；
  - Tool Selection Accuracy：98.4375%；
  - Task Success Rate：98.0769%；
  - 观测高风险状态修改违规率：0%；
- 两条真实模型 Bad Case 经 Evidence 自动绑定和 Tool Schema 优化后单独重放通过，v2 回归集增至 14 条。

## 数据库迁移

新增迁移：`c91f4e2a7b10_enterprise_harness_reliability.py`。

迁移包含：

- AgentRun Lease/Fence/Budget/Cancel 字段；
- BackgroundJob Retry/Dead Letter 字段；
- AgentStep、ModelInvocation、ToolInvocation、Checkpoint 增强；
- ToolExecutionRecord、GuardrailEvent、ShadowEvaluation 新表；
- Proposal Evidence Snapshot、审批与应用状态字段；
- JobStatus、AgentRunStatus、ProposalStatus 枚举升级。

已在 PostgreSQL 17 上验证：

```text
upgrade v0.1.0 schema → v0.2.0
↓
可靠性集成测试
↓
downgrade v0.1.0 schema
↓
upgrade v0.2.0
↓
再次执行可靠性集成测试
```

## 质量验证

- 60 项后端 Unit/Health 测试通过；
- 4 项 Integration 测试进入 CI，其中 2 项 Harness PostgreSQL 可靠性测试已本地通过；
- 20 条 Smoke Benchmark 继续 100% 通过；
- 1000 Case Fake Evaluation 与 14 条回归 Case 门禁通过；
- 前端 6 项测试、ESLint、TypeScript 和生产构建通过；
- Ruff、Mypy、Compose、Prometheus、Grafana 配置检查通过。

## 已知限制

- 1000 条评测数据为业务真实分布的合成数据，不包含真实学生 PII；
- 真实模型分层样本为 104 条，并非 1000 条全部调用 Qwen；
- Tool Call 平均降低 20%、Token 平均降低 35% 尚无全量同口径证据，不在本版本中声明；
- 完整 Docker Compose 运行态仍要求本机先将 Docker Desktop 数据盘迁移到 D 盘；
- 当前仍是面向学习诊断业务的单 Agent Harness，不是通用多 Agent 平台。
