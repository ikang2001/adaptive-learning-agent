# adaptive-learning-agent 企业级 Agent Harness 完善方案

> 目标仓库：`ikang2001/adaptive-learning-agent`
> 目标定位：将当前项目从“工程化 Agent Harness 雏形”完善为**可用于真实业务长期运行的生产型单 Agent Harness**。
> 适用对象：Codex / Claude Code / Cursor / 其他代码 Agent。
> 重要原则：**优先补可靠性、可恢复性、安全性和可观测性，不为了“显得复杂”而引入无业务价值的中间件。**

---

# 0. 项目背景与当前定位

本项目是面向考研专业课复习场景的“千人千案”智能学习系统。

核心业务闭环：

```text
目标院校考纲 / 知识点权重
        ↓
个性化学习计划
        ↓
课程 / 讲义 / 个性化刷题 / 专项强化
        ↓
学习反馈 / 错题 / 掌握度更新
        ↓
确定性异常检测
        ↓
复杂异常触发学习诊断 Agent
        ↓
Evidence 收集
        ↓
Minor Adjustment / Major Replan Proposal
        ↓
规则二次校验 / 用户确认
        ↓
动态调整后续计划
```

当前系统采用正确的总体设计：

```text
确定性业务规则
+
受控 Agent Harness
```

稳定、可计算、可验证的逻辑，例如：

- 学习计划容量；
- 掌握度更新；
- 解锁条件；
- 学习任务状态；
- 小幅调整是否合法；

由业务层确定性执行。

Agent 主要处理：

- 学习超时；
- 低正确率；
- 重复错误；
- 连续低完成度；
- 多信号组合异常；
- 需要多个 Tool 收集证据的复杂诊断；
- 计划调整 Proposal。

这一原则必须保留，**禁止重构为“所有业务逻辑都交给 LLM”**。

---

# 1. 当前已有能力

当前源码已经具备较好的 Harness 基础，不应推倒重写。

主要模块：

```text
app/harness/contracts.py
app/harness/runner.py
app/harness/tools.py

app/application/agent_runs.py

app/infrastructure/adapters/model_gateway.py
app/infrastructure/adapters/learning_tools.py
app/infrastructure/adapters/harness_store.py

app/workers/tasks.py
app/workers/dispatcher.py

app/observability/
```

目前已经存在：

- AgentRunner；
- RuntimeState；
- Tool Registry；
- Tool Executor；
- Policy Guard；
- READ / PROPOSAL Tool 风险分级；
- Tool Timeout；
- READ Tool Retry；
- Checkpoint 持久化；
- Agent Step Trace；
- Tool Invocation Trace；
- Token / Latency 记录；
- Model Gateway；
- Flash / Plus 模型路由；
- 低置信度模型升级；
- max_steps；
- max_tool_calls；
- max_runtime_seconds；
- 重复动作检测；
- LOOP_STALLED；
- Proposal；
- Proposal 幂等键；
- Agent Job 异步执行；
- PostgreSQL Job 事实源；
- Redis + ARQ Worker；
- `FOR UPDATE SKIP LOCKED` Dispatcher；
- Prometheus；
- OpenTelemetry；
- Harness 单元测试；
- 固定轨迹 Benchmark。

因此本次改造原则为：

> **补齐生产可靠性闭环，而不是重新造一个 Harness。**

---

# 2. 总体目标架构

完善后的 Harness 应形成如下结构：

```text
                       API / Worker
                            │
                            ▼
                    AgentRunCoordinator
                            │
                  ┌─────────┴─────────┐
                  │                   │
             Lease Manager      Checkpoint Manager
                  │                   │
                  └─────────┬─────────┘
                            ▼
                       AgentRunner
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
   Model Router        Policy Engine       Budget Manager
        │                   │                   │
        ▼                   ▼                   ▼
 Structured Output      Tool Guard       Step / Tool / Token
 Validation                 │              / Runtime Budget
        │                    ▼
        │              Tool Executor
        │                    │
        │          ┌─────────┼──────────┐
        │          ▼         ▼          ▼
        │      Validator   Retry     Idempotency
        │                    │
        │               Backoff/Jitter
        │
        ▼
 Evidence / Observation
        │
        ▼
 Checkpoint + Trace + Metrics
        │
        ▼
 Continue / Finish / Pause / Fail
```

必须达到以下工程目标：

1. **可控**：Agent 不可无限循环、无限调用 Tool、无限消耗 Token。
2. **可恢复**：Worker 崩溃后可以从 Checkpoint 恢复。
3. **不重复副作用**：重复消费不能重复提交 Proposal 或其他写操作。
4. **可审计**：每个 Step、Tool、模型调用、策略拦截都有 Trace。
5. **安全**：模型没有绕过业务边界直接修改核心状态的能力。
6. **可降级**：模型失败、Tool 失败、外部依赖异常时有明确终止或降级策略。
7. **可观测**：能够回答“为什么慢、为什么贵、为什么失败、在哪里循环”。
8. **可测试**：核心可靠性能力有单元测试、集成测试和故障注入测试。

---

# 3. P0：必须完成的生产级改造

以下内容是本项目从“工程化 Harness”升级到“生产型 Harness”的核心。

---

# 3.1 Checkpoint → Resume 真正闭环

## 当前问题

目前已经可以保存 `RuntimeState`，但 Checkpoint 更偏向：

```text
Checkpoint Persistence
```

而不是完整：

```text
Checkpoint
→ Load
→ Resume
→ Replay Protection
```

Worker 再次执行 Agent Job 时，如果重新创建：

```python
RuntimeState(...)
```

就没有真正利用之前保存的状态。

## 改造目标

新增：

```python
CheckpointStore.save(...)
CheckpointStore.load_latest(...)
```

建议接口：

```python
class CheckpointStore(Protocol):
    async def save(self, state: RuntimeState) -> None: ...
    async def load_latest(self) -> RuntimeState | None: ...
```

数据库实现：

```text
DatabaseCheckpointStore
├── save()
└── load_latest()
```

`load_latest()` 应：

1. 按 `run_id` 查询最大 `step_number`；
2. 读取 state JSON；
3. 严格反序列化为 `RuntimeState`；
4. 校验 checkpoint version；
5. 返回最后一次可恢复状态。

## AgentDiagnosisService 改造

启动 Run 时：

```text
查询 AgentRun
    ↓
获取 Run Lease
    ↓
load_latest checkpoint
    ↓
有 checkpoint
    ├─ 恢复 RuntimeState
    └─ 记录 resumed=true
无 checkpoint
    └─ 创建新 RuntimeState
```

## Checkpoint Schema 增强

建议增加：

```text
checkpoint_version
created_at
state_hash
resume_safe
```

可选：

```text
previous_checkpoint_id
```

## Replay Protection

Checkpoint 恢复时必须保证：

> 已经成功执行过的有副作用动作不会再次执行。

Proposal 使用现有 `idempotency_key` 去重。

后续所有带副作用 Tool 必须拥有：

```text
run_id
tool_name
idempotency_key
execution_status
result_digest
```

恢复时先查询已完成调用。

## 验收标准

必须新增测试：

### Test 1：Worker 中断恢复

```text
Step 1: read tool 成功
Step 2: checkpoint 保存
模拟 Worker crash
重新执行同一个 Agent Run
从 Step 2 状态恢复
继续执行
最终 COMPLETED
```

断言：

- 前面的 READ Tool 不需要强制重放；
- 已提交 Proposal 不重复创建；
- `loop_count` 连续；
- `tool_call_count` 连续；
- Trace 不出现错误覆盖。

### Test 2：重复消费同一 Agent Job

连续执行同一个 job 两次：

```text
最终只有一个有效 Proposal
```

---

# 3.2 Run Lease + Fencing Token

## 当前风险

单纯依赖 Job 状态与 Queue 去重不足以彻底避免：

```text
Worker A 执行 Run
↓
A 卡死 / 网络分区
↓
Worker B 接管
↓
A 恢复
↓
A 和 B 同时写状态
```

这是典型 stale worker 问题。

## 改造目标

给 `AgentRun` 增加：

```text
lease_owner
lease_expires_at
fencing_token
heartbeat_at
```

建议：

```python
lease_owner: str | None
lease_expires_at: datetime | None
fencing_token: int
heartbeat_at: datetime | None
```

每次 Worker 接管：

```text
SELECT ... FOR UPDATE
    ↓
lease 已过期 / 尚未持有
    ↓
fencing_token += 1
    ↓
设置 lease_owner
设置 lease_expires_at
```

默认 Lease：

```text
30~60 秒
```

长 Agent Run 需要周期 Heartbeat。

## Fence 规则

任何关键写入：

- Run 状态；
- Checkpoint；
- Proposal；
- Tool Invocation 最终状态；

都必须验证当前 Worker 的：

```text
fencing_token == AgentRun.fencing_token
```

旧 Worker 恢复后：

```text
token=12
数据库当前 token=13

→ 拒绝写入
→ STALE_WORKER
```

## 新增模块建议

```text
app/harness/lease.py
```

提供：

```python
RunLeaseManager.acquire()
RunLeaseManager.renew()
RunLeaseManager.release()
RunLeaseManager.assert_fence()
```

## 验收标准

模拟两个 Worker：

```text
Worker A token=1
Worker A lease 过期
Worker B token=2
Worker A 再提交 checkpoint
```

必须被拒绝。

---

# 3.3 Tool Error Taxonomy + Retry Policy

## 当前问题

目前 Tool Retry 主要围绕 Timeout。

生产环境 Tool 错误至少需要区分：

```text
输入错误
权限错误
业务错误
外部限流
网络错误
服务端错误
超时
数据不一致
```

不同错误不能使用同一种 Retry。

## 新增错误体系

建议创建：

```text
app/harness/errors.py
```

包含：

```python
class AgentHarnessError(Exception): ...
class ToolError(AgentHarnessError): ...

class ToolValidationError(ToolError): ...
class ToolPermissionError(ToolError): ...
class ToolBusinessError(ToolError): ...

class ToolTransientError(ToolError): ...
class ToolRateLimitError(ToolTransientError): ...
class ToolTimeoutError(ToolTransientError): ...
class ToolUpstreamError(ToolTransientError): ...

class StructuredOutputError(AgentHarnessError): ...
class BudgetExceededError(AgentHarnessError): ...
class StaleWorkerError(AgentHarnessError): ...
```

## Retry Matrix

必须显式定义：

| 错误 | READ Tool | PROPOSAL / WRITE |
|---|---:|---:|
| 参数错误 | 不重试 | 不重试 |
| 权限错误 | 不重试 | 不重试 |
| 业务错误 | 不重试 | 不重试 |
| Timeout | 可有限重试 | 仅幂等时重试 |
| 429 | 可重试 | 仅幂等时重试 |
| 5xx | 可重试 | 仅幂等时重试 |
| 网络连接错误 | 可重试 | 仅幂等时重试 |

## Backoff

禁止：

```text
失败
马上重试
马上重试
```

新增：

```text
Exponential Backoff
+
Jitter
```

例如：

```text
base_delay = 0.25s
attempt 1 ≈ 0.25s
attempt 2 ≈ 0.50s
attempt 3 ≈ 1.00s
+ random jitter
```

设置：

```text
max_retry_attempts
max_retry_delay
retry_budget_seconds
```

## ToolDefinition 增强

建议从：

```python
retry_count
```

升级为：

```python
retry_policy
idempotency_required
side_effect_level
```

例如：

```python
class ToolSideEffect(StrEnum):
    NONE = "NONE"
    IDEMPOTENT_WRITE = "IDEMPOTENT_WRITE"
    NON_IDEMPOTENT_WRITE = "NON_IDEMPOTENT_WRITE"
```

## 验收标准

必须覆盖：

- timeout 后成功；
- 429 后成功；
- ValidationError 不重试；
- PermissionError 不重试；
- 非幂等写 Tool 即使 timeout 也不能盲目重试；
- retry 次数、耗时写入 Trace。

---

# 3.4 Model Structured Output Validation

## 当前风险

模型响应不能只依赖：

```python
json.loads()
```

需要解决：

- JSON 非法；
- 字段缺失；
- 类型错误；
- confidence 超范围；
- finish 与 tool_call 同时出现；
- Tool 参数不符合 Schema；
- 模型输出未知 Tool；
- 模型伪造高风险动作。

## 改造目标

使用 Pydantic v2 定义严格模型。

建议：

```text
app/harness/schemas.py
```

例如：

```python
class ToolCallPayload(BaseModel):
    name: str
    arguments: dict[str, Any]
    idempotency_key: str | None = None

class FinalDecisionPayload(BaseModel):
    decision: str
    confidence: float = Field(ge=0, le=1)
    reason_codes: list[str]
    finish: Literal[True]
```

对 Model Action 做互斥约束：

```text
一次模型响应只能：
1. Tool Call
或
2. Final Decision
```

不能二者同时存在。

## Tool 参数验证

`ToolDefinition.input_schema` 不能只传给模型看。

**Executor 执行前必须再次服务端验证。**

推荐两种方式任选其一：

1. 每个 Tool 绑定 Pydantic `args_model`；
2. 统一使用 JSON Schema Validator。

推荐 Pydantic。

```python
ToolDefinition(
    ...,
    args_model=SearchRecentAttemptsArgs,
)
```

执行：

```text
LLM arguments
↓
Pydantic Validation
↓
Validated Arguments
↓
Policy Guard
↓
Tool Handler
```

## Output Repair

允许最多一次受控 Repair：

```text
JSON Parse / Schema Validation 失败
        ↓
Structured Output Repair
        ↓
仍失败
        ↓
STRUCTURED_OUTPUT_ERROR
```

禁止无限 Repair Loop。

## 验收标准

模拟：

- malformed JSON；
- confidence="high"；
- confidence=1.5；
- unknown tool；
- 缺 required argument；
- 多余危险字段；
- finish=false 无 tool_call。

都必须安全失败，而不是进入不可预测状态。

---

# 3.5 Tool Idempotency Ledger

Proposal 已有幂等能力，但生产型 Harness 应统一抽象。

## 新增 Tool Execution Ledger

数据库建议新增：

```text
tool_execution_records
```

字段：

```text
id
run_id
tool_name
tool_version
idempotency_key
args_digest
status

STARTED
SUCCEEDED
FAILED
UNKNOWN

result_digest
error_code
started_at
finished_at
```

唯一约束：

```text
(run_id, tool_name, idempotency_key)
```

## 执行逻辑

```text
收到 Tool Call
↓
如果是带副作用 Tool
↓
要求 idempotency_key
↓
查询 Ledger
├─ SUCCEEDED → 返回历史结果 / result ref
├─ STARTED → 判断是否仍在执行
├─ FAILED → 按 retry policy 判断
└─ 无记录 → 创建 STARTED
```

## UNKNOWN 状态

重点处理：

```text
请求已发送给外部系统
↓
客户端 timeout
↓
不知道服务端到底成功没成功
```

不能简单判定 FAILED 并重试。

需要：

```text
UNKNOWN
```

然后：

```text
query status / reconcile
```

本项目目前写操作主要是 Proposal，但这个能力应在 Harness 层准备好。

---

# 4. P1：强烈建议完成

---

# 4.1 Budget Manager

目前已有：

```text
max_steps
max_tool_calls
max_runtime_seconds
```

建议统一成：

```text
RunBudget
```

增加：

```text
max_input_tokens
max_output_tokens
max_total_tokens
max_model_calls
max_tool_calls
max_runtime_seconds
max_repair_calls
```

`RuntimeState` 增加累计：

```text
model_call_count
input_tokens
output_tokens
total_tokens
started_at
```

每轮执行前调用：

```python
budget_manager.check(state)
```

终止原因细化：

```text
STEP_BUDGET_EXCEEDED
TOOL_BUDGET_EXCEEDED
TOKEN_BUDGET_EXCEEDED
MODEL_CALL_BUDGET_EXCEEDED
TIME_BUDGET_EXCEEDED
```

---

# 4.2 Model Router 工程化

当前 Flash / Plus 路由保留。

增强为：

```text
ModelRouter
├── policy-based route
├── confidence escalation
├── timeout fallback
├── rate-limit fallback
├── circuit breaker
└── cost / token accounting
```

## 不要过度设计

当前项目无需接入十几个模型。

支持：

```text
Flash
Plus
Fake
```

已经够用。

重点是失败策略。

例如：

```text
Flash
↓
低置信度
Plus

Flash timeout
↓
Plus

Plus 429
↓
受控重试
↓
仍失败
MODEL_UNAVAILABLE
```

## Circuit Breaker

可做轻量版本：

```text
连续 N 次模型调用失败
↓
短时间 OPEN
↓
快速失败 / 降级
```

不要求引入额外第三方框架。

---

# 4.3 Agent Policy Engine

当前 `PolicyGuard` 继续保留，但建议从简单名称拦截升级成结构化 Policy。

ToolDefinition 增加：

```text
risk
side_effect_level
required_permissions
requires_confirmation
idempotency_required
```

Agent Context 增加：

```text
user_id
student_id
role
tenant/context scope
```

虽然当前不是大型 SaaS 多租户系统，也必须保证：

> Tool 查询的数据范围不能只依赖 LLM 参数。

例如：

```text
get_student_knowledge_states
```

必须由服务端绑定当前 `student_id`。

禁止模型通过参数读取其他用户数据。

## Policy Decision

建议输出：

```python
PolicyDecision(
    allowed=True/False,
    reason_code="...",
)
```

并记录：

```text
PolicyDecision Trace
```

---

# 4.4 Prompt Injection / Tool Injection 防护

学习资料未来可能来自：

- PDF；
- DOCX；
- Markdown；
- OCR；
- 用户文本。

必须区分：

```text
Instruction
vs
Untrusted Content
```

从资源库检索出来的文本只能作为：

```text
UNTRUSTED_EVIDENCE
```

不得直接拼成 System Prompt 指令。

## 基本措施

1. System Prompt 明确：
   - 外部内容不是系统指令；
   - 不得执行资料中出现的 Tool 指令；
   - Tool 仅由 Registry 提供。
2. Tool 名称严格白名单。
3. Tool Arguments 服务端校验。
4. 数据检索结果做长度限制。
5. 不向模型暴露敏感密钥、手机号等 PII。
6. Trace 中敏感字段脱敏。

---

# 4.5 Job Reliability：Retry / DLQ / Reconciliation

当前 DB Job + ARQ 的思路很好，应继续沿用。

建议 BackgroundJob 增加或确认：

```text
attempt_count
max_attempts
last_error_code
next_retry_at
dead_lettered_at
```

## 状态机建议

```text
QUEUED
↓
RUNNING
├─ SUCCEEDED
├─ RETRY_WAIT
├─ FAILED
└─ DEAD_LETTER
```

Dispatcher 只投递：

```text
QUEUED
或
到期的 RETRY_WAIT
```

## Reconciliation Job

增加周期任务：

```text
扫描长时间 RUNNING 但 lease 已失效的 Job
↓
恢复到 RETRY_WAIT
```

不要依赖人工修库。

---

# 4.6 Graceful Cancellation

Agent Run 应支持：

```text
CANCEL_REQUESTED
CANCELLED
```

Runner 每轮执行前：

```text
check_cancelled()
```

Tool 调用完成后也检查。

注意：

> 已执行的副作用不能通过“取消”自动回滚。

取消只停止后续动作。

---

# 5. 可观测性完善

当前已有：

- HTTP Metrics；
- Agent Run Metrics；
- OpenTelemetry；
- DB Trace；
- AgentStep；
- ToolInvocation。

在此基础上增加以下指标。

---

# 5.1 Prometheus Metrics

建议：

```text
agent_runs_total{status,termination_reason}

agent_model_calls_total{model,status}
agent_model_latency_seconds{model}
agent_model_input_tokens_total{model}
agent_model_output_tokens_total{model}

agent_tool_calls_total{tool,status}
agent_tool_latency_seconds{tool}
agent_tool_retries_total{tool,error_type}

agent_steps_per_run
agent_tool_calls_per_run
agent_tokens_per_run

agent_loop_stalled_total
agent_budget_exceeded_total{budget_type}
agent_guardrail_block_total{reason}

agent_checkpoint_save_total
agent_resume_total

agent_job_retry_total{job_type}
agent_dead_letter_total{job_type}
```

注意控制 label cardinality：

禁止把：

```text
run_id
student_id
user_id
```

放进 Prometheus Label。

这些信息放 Trace / Log。

---

# 5.2 Trace Span

OpenTelemetry 建议形成：

```text
agent.run
    ├── model.decide
    ├── policy.validate
    ├── tool.execute
    │      ├── tool.retry
    │      └── tool.handler
    ├── checkpoint.save
    └── proposal.commit
```

Span Attribute：

```text
agent.goal
agent.step
agent.model
agent.termination_reason

tool.name
tool.version
tool.risk
tool.retry_count

model.input_tokens
model.output_tokens
```

禁止记录完整敏感 Prompt。

---

# 5.3 Structured Logging

统一结构：

```json
{
  "event": "agent_tool_failed",
  "run_id": "...",
  "step": 3,
  "tool_name": "...",
  "error_type": "ToolTimeoutError",
  "retry_attempt": 2
}
```

至少支持：

- run_id；
- job_id；
- trace_id；
- step；
- model；
- tool；
- termination_reason；
- error_code。

---

# 6. Trace / Audit 数据模型完善

当前 AgentStep / ToolInvocation / Checkpoint 是正确方向。

建议进一步明确：

## AgentStep

保存：

```text
step_number
model_name
prompt_version
policy_version
input_tokens
output_tokens
latency_ms
action_type
decision
confidence
reason_codes
created_at
```

## ToolInvocation

保存：

```text
tool_name
tool_version
risk
args_digest
status
latency_ms
retry_count
error_code
idempotency_key
observation_digest
```

## GuardrailEvent

建议新增：

```text
guardrail_events
```

字段：

```text
run_id
step_id
tool_name
policy_version
decision
reason_code
created_at
```

这样可以真实统计：

```text
Unsafe Action Attempt Rate
Unauthorized Mutation Success Rate
```

而不是笼统写“违规率”。

---

# 7. Loop Termination 升级

目前已经有重复动作 fingerprint，是正确的。

建议扩展为三类 Stall：

## 7.1 Repeated Action

```text
连续相同 Tool + 相同 Args
```

→ `REPEATED_ACTION`

## 7.2 No New Evidence

定义 observation fingerprint。

连续两轮：

```text
没有新增 evidence
```

→ `NO_NEW_EVIDENCE`

## 7.3 Oscillation

例如：

```text
Tool A
Tool B
Tool A
Tool B
```

连续循环。

→ `ACTION_OSCILLATION`

最终统一：

```text
LOOP_STALLED
```

Trace 记录：

```text
stall_reason
```

---

# 8. Evidence Contract

Agent 不能只返回“我觉得应该重排”。

每个 Proposal 必须绑定 Evidence。

建议定义：

```python
class EvidenceRef(BaseModel):
    evidence_type: str
    source_id: str
    version: str | None
    summary: str | None
```

Proposal 至少包含：

```text
reason_codes
confidence
evidence_refs
```

已有方向继续保留。

增强：

```text
evidence_count >= minimum
evidence source ownership valid
evidence not stale
```

如果证据不足：

```text
UNCERTAIN
```

而不是硬给 MAJOR_REPLAN。

---

# 9. Proposal 状态机完善

建议明确：

```text
PENDING
↓
AUTO_COMMITTED
```

或：

```text
PENDING
↓
AWAITING_CONFIRMATION
├── APPROVED
└── REJECTED
```

如果执行失败：

```text
APPROVED
↓
APPLYING
├── APPLIED
└── APPLY_FAILED
```

重大 Replan：

```text
APPROVED
↓
生成新的 GENERATE_PLAN Job
↓
新 Plan 成功
↓
Proposal APPLIED
```

不要在 Proposal APPROVED 后就认为业务已完成。

---

# 10. P2：可选增强

这些不是当前面试和生产可靠性的第一优先级。

---

# 10.1 Dynamic Tool Registry

当前 Tool 代码注册完全可以继续使用。

只有未来 Tool 很多、需要按业务动态开关时，再增加：

```text
enabled
environment
minimum_role
feature_flag
```

不要为了“企业级”马上做数据库动态注册。

---

# 10.2 Human-in-the-loop

现有 Proposal + Confirmation 已经是 HITL。

可增强：

```text
approval_expires_at
review_reason
reviewer_id
```

但不是 P0。

---

# 10.3 Replay Debugger

基于保存的：

```text
AgentStep
ToolInvocation
Checkpoint
```

实现只读 Debug Replay：

```text
读取历史 Run
↓
重新构造状态变化过程
↓
展示每一步：
模型决策
Tool
Observation
Policy
Checkpoint
```

注意：

> Debug Replay 默认不得重新执行副作用 Tool。

---

# 10.4 Shadow Evaluation

新 Prompt / 新模型发布前：

```text
生产流量复制一份
↓
不执行 Proposal
↓
Shadow Agent
↓
比较旧版 / 新版决策
```

适合后续正式模型优化。

---

# 11. 测试体系要求

此次完善不能只“代码能运行”。

至少需要以下四层测试。

---

# 11.1 Unit Test

覆盖：

```text
AgentRunner
BudgetManager
ToolRegistry
PolicyGuard
RetryPolicy
StructuredOutputValidator
Checkpoint serialize/deserialize
Lease/Fence
Loop Termination
```

---

# 11.2 Integration Test

使用真实 PostgreSQL + Redis：

```text
DB Job
→ Dispatcher
→ ARQ Worker
→ AgentRunner
→ Checkpoint
→ Proposal
```

至少验证：

- 正常 Run；
- Retry；
- Resume；
- 重复消费；
- Fence；
- Proposal 幂等。

---

# 11.3 Fault Injection Test

必须人为制造：

```text
模型 Timeout
Tool Timeout
Redis 暂时不可用
Worker 中途崩溃
DB 短暂失败
重复 Job
Lease 过期
Malformed Model JSON
Unknown Tool
```

要求系统：

```text
不会重复副作用
不会无限循环
不会 silent failure
可以明确终止或恢复
```

---

# 11.4 Property / Invariant Test

建议继续使用 Hypothesis。

核心不变量：

```text
tool_call_count <= max_tool_calls
loop_count <= max_steps

未经授权的 Tool 永远不能执行

同一 idempotency_key
最多产生一次业务副作用

stale fencing_token
永远不能更新当前 Run

AUTO_COMMITTED Proposal
必须 confidence >= threshold
必须 evidence 非空
必须不突破每日容量
```

---

# 12. CI Quality Gate

GitHub Actions 建议至少：

```text
ruff
mypy
pytest unit
pytest integration
benchmark regression
```

可以增加：

```text
migration check
gitleaks
dependency vulnerability scan
```

Harness 核心代码不得通过：

```text
try:
    ...
except Exception:
    pass
```

吞异常。

所有 Harness 失败必须：

```text
明确 error type
+
Trace
+
termination reason
```

---

# 13. Database Migration 要求

任何数据库修改必须：

```text
SQLAlchemy Model
+
Alembic Migration
+
Migration Test
```

禁止 AI：

- 直接改数据库；
- 删除现有表重建；
- 为了方便修改历史 migration；
- 破坏现有演示数据。

需要新增字段时优先：

```text
nullable / default
↓
backfill
↓
再增加严格约束
```

避免破坏已有部署。

---

# 14. 建议新增目录结构

不强制完全一致，但职责需清晰。

```text
app/
├─ harness/
│  ├─ contracts.py
│  ├─ runner.py
│  ├─ tools.py
│  ├─ errors.py               # 新增
│  ├─ retry.py                # 新增
│  ├─ schemas.py              # 新增
│  ├─ budget.py               # 新增
│  ├─ lease.py                # 新增
│  ├─ policy.py               # 可从 tools.py 拆分
│  ├─ termination.py          # 可选
│  └─ evidence.py             # 可选
│
├─ infrastructure/
│  ├─ adapters/
│  │  ├─ model_gateway.py
│  │  ├─ learning_tools.py
│  │  ├─ harness_store.py
│  │  └─ tool_ledger.py       # 新增
│
├─ application/
│  └─ agent_runs.py
│
└─ workers/
   ├─ dispatcher.py
   └─ tasks.py
```

原则：

> 不要为了目录漂亮，把 30 行代码拆成十几个文件。

按复杂度合理拆分。

---

# 15. RuntimeState 建议升级

建议变为：

```python
RuntimeState
├── identity
│   ├── run_id
│   ├── student_id
│   └── goal
│
├── progress
│   ├── loop_count
│   ├── model_call_count
│   └── tool_call_count
│
├── budget
│   ├── input_tokens
│   ├── output_tokens
│   └── started_at
│
├── evidence
│   └── observations
│
├── termination
│   └── last_action_fingerprints
│
└── recovery
    ├── checkpoint_version
    ├── fencing_token
    └── resumed
```

但注意：

> RuntimeState 不要塞入完整 ORM Model。

保持可序列化、可版本化。

---

# 16. 终止原因统一规范

不要使用任意字符串。

建议 Enum：

```text
COMPLETED

INVALID_ACTION
STRUCTURED_OUTPUT_ERROR
UNKNOWN_TOOL
TOOL_VALIDATION_FAILED
TOOL_PERMISSION_DENIED
TOOL_FAILED

MAX_STEPS
TOOL_BUDGET_EXCEEDED
TOKEN_BUDGET_EXCEEDED
TIME_BUDGET_EXCEEDED

LOOP_STALLED
NO_NEW_EVIDENCE

MODEL_UNAVAILABLE

CANCELLED
STALE_WORKER
INTERNAL_ERROR
```

所有退出必须：

```text
AgentRun.status
+
termination_reason
+
Trace
+
Metric
```

一致。

---

# 17. 安全要求

必须保持：

```text
LLM ≠ Database Admin
```

LLM 只能：

```text
读 Evidence
+
提出 Proposal
```

不能直接：

```text
update mastery
delete task
publish content
change stage
change plan
```

所有状态变更都必须经过：

```text
Application Service
+
Domain Rule
+
Authorization
+
Transaction
```

---

# 18. 不要做的错误改造

请代码 Agent 严格避免以下行为。

## 18.1 不要换成完全自由 ReAct

错误：

```text
LLM 想干什么就干什么
```

保留：

```text
Tool whitelist
Policy
Budget
Guardrail
Proposal
```

---

## 18.2 不要因为“企业级”强行上 Kafka

当前：

```text
PostgreSQL Job
+
Redis
+
ARQ
```

对本项目足够。

除非已经明确出现：

```text
跨服务事件流
超高吞吐
多消费组
```

否则不引 Kafka。

---

## 18.3 不要强行改成多 Agent

当前问题：

```text
学习诊断
```

单 Agent + Tool 已经足够。

多 Agent 会增加：

- Token；
- 调试成本；
- 状态复杂度；
- Eval 难度。

---

## 18.4 不要让 LLM 负责确定性计算

例如：

```text
每日学习容量
掌握度公式
阶段解锁
时间预算
Proposal 是否越界
```

必须继续走代码规则。

---

## 18.5 不要伪造测评指标

代码和 README 中禁止写：

```text
Decision Accuracy 93%
Tool Accuracy 96%
Token 降低 35%
```

除非已经存在真实评测结果文件和可重复脚本。

允许保留：

```text
target
quality gate
benchmark threshold
```

但必须明确标注：

```text
目标值 / 门禁值 ≠ 实测结果
```

---

# 19. 推荐实施顺序

不要一次性大改。

---

## Phase 0：建立基线

先运行并记录：

```text
pytest
ruff
mypy
现有 benchmark
```

确认当前主分支基线。

---

## Phase 1：Checkpoint Resume

完成：

```text
Checkpoint load_latest
RuntimeState 恢复
AgentDiagnosisService Resume
幂等恢复测试
```

验收后再继续。

---

## Phase 2：Lease + Fence

完成：

```text
AgentRun Lease
Heartbeat
Fencing Token
Stale Worker Test
```

---

## Phase 3：Tool Reliability

完成：

```text
Tool Error Taxonomy
Retry Matrix
Backoff
Jitter
Idempotency Ledger
```

---

## Phase 4：Model Reliability

完成：

```text
Pydantic Structured Output
Tool Arg Validation
Repair Once
Model Failure Taxonomy
```

---

## Phase 5：Budget / Loop / Cancellation

完成：

```text
Token Budget
Model Call Budget
No-new-evidence
Oscillation
Cancellation
```

---

## Phase 6：Observability

完成：

```text
Agent Metrics
Tool Metrics
Model Metrics
Guardrail Metrics
Resume Metrics
OTel Agent Spans
```

---

## Phase 7：Fault Injection + CI

完成：

```text
Crash Resume
Duplicate Delivery
Timeout
429
Malformed JSON
Fence Conflict
```

所有测试通过后才能宣称：

> 生产型 Agent Harness。

---

# 20. P0 最终 Definition of Done

只有下面全部满足，P0 才算完成：

- [ ] Checkpoint 可以 `save + load_latest`
- [ ] Agent Job 可从最近 Checkpoint Resume
- [ ] Resume 不重复 Proposal 副作用
- [ ] AgentRun 存在 Lease
- [ ] 存在 Fencing Token
- [ ] Stale Worker 无法写当前 Run
- [ ] Tool 错误有明确分类
- [ ] Retry 只针对可恢复错误
- [ ] Retry 使用 Backoff + Jitter
- [ ] 写 Tool Retry 必须满足幂等条件
- [ ] 模型输出使用严格 Schema 校验
- [ ] Tool Args 在服务端再次校验
- [ ] Structured Output 最多 Repair 一次
- [ ] 所有失败有 termination_reason
- [ ] 所有核心失败进入 Trace
- [ ] 新功能具有单元测试
- [ ] Resume / Fence 有真实 PostgreSQL 集成测试
- [ ] Alembic Migration 完整
- [ ] 原有业务测试不回归

---

# 21. P1 最终 Definition of Done

- [ ] Token Budget
- [ ] Model Call Budget
- [ ] Model Router Fallback
- [ ] Guardrail Event 持久化
- [ ] Agent/Tool/Model Prometheus Metrics
- [ ] Agent OTel Span
- [ ] Job Retry / Dead Letter
- [ ] Job Reconciliation
- [ ] Graceful Cancellation
- [ ] No-new-evidence termination
- [ ] Oscillation detection
- [ ] Fault Injection Tests
- [ ] CI Quality Gate

---

# 22. 完成后的项目面试定位

完善完成后，这个项目可以准确描述为：

> **基于 FastAPI、PostgreSQL、Redis/ARQ 和 Qwen 实现的生产型单 Agent Harness。系统将确定性学习业务规则与 LLM 推理解耦，通过 Tool Registry、Policy Guard、Proposal、Checkpoint/Resume、Lease/Fence、幂等执行、Retry、Budget、Loop Termination、Trace 和 Evaluation 等机制解决 Agent 在真实业务中的可控性、可靠性、安全性和可观测性问题。**

不要描述为：

```text
通用 AI Agent 平台
```

因为当前系统仍然是：

```text
面向学习诊断业务的单 Agent Harness
```

这个定位反而更真实、更专业。

---

# 23. 对代码 Agent 的最终执行要求

请基于当前仓库源码直接实施，不要仅输出建议。

执行时必须遵守：

1. **先阅读现有实现，不推倒重构。**
2. 每个 Phase 开始前确认当前相关代码。
3. 每个 Phase 结束后运行对应测试。
4. 不要一次提交全部改动。
5. 任何数据库 Schema 修改必须带 Alembic Migration。
6. 不删除原有 API 和业务功能。
7. 不改变现有“确定性规则 + Agent 异常诊断”总体架构。
8. 不将 Proposal 改为 LLM 直接写业务状态。
9. 不引入没有必要的新基础设施。
10. 不伪造 Evaluation 数字。
11. 对所有新增机制补测试。
12. 对关键设计在 README 增加简短说明。
13. 最终输出：
    - 修改文件清单；
    - 新增架构说明；
    - Migration 清单；
    - 测试结果；
    - 尚未完成的风险；
    - 后续真实 Evaluation 接入点。

---

# 24. 最终优先级总结

如果开发时间有限，严格按下面顺序做：

```text
第一优先级
Checkpoint Resume
        ↓
Lease + Fence
        ↓
Tool Error / Retry / Idempotency
        ↓
Structured Output Validation

第二优先级
Budget
Loop Termination
Model Fallback
Cancellation

第三优先级
Metrics / Trace
Job DLQ / Reconciliation
Fault Injection

第四优先级
Replay Debugger
Shadow Evaluation
Dynamic Tool Registry
```

其中真正决定项目能不能称为“生产型 Agent Harness”的四项是：

```text
Checkpoint / Resume
+
Lease / Fence
+
Tool Reliability / Idempotency
+
Structured Output Validation
```

优先把这四项做扎实，再继续扩功能。

---

# 25. 期望最终状态

最终系统应能够回答以下生产问题：

```text
Agent 为什么执行了这个 Tool？
→ Trace + Evidence

为什么 Agent 停了？
→ termination_reason

为什么没有无限循环？
→ Budget + Stall Detection

Worker 崩溃怎么办？
→ Checkpoint + Resume

两个 Worker 同时执行怎么办？
→ Lease + Fence

Tool timeout 能不能重试？
→ Error Taxonomy + Retry Policy

写操作重复执行怎么办？
→ Idempotency Ledger

模型 JSON 错了怎么办？
→ Structured Output Validation + Repair Once

模型想直接改计划怎么办？
→ Policy Guard + Proposal

为什么这次 Agent Token 特别高？
→ Token Metrics + Step Trace

外部模型挂了怎么办？
→ Retry / Fallback / Circuit Breaker

出了 Bad Case 怎么定位？
→ Run → Step → Model → Tool → Observation → Policy 全链路 Trace
```

做到这一层，这个项目就不再只是“能调用工具的 Agent”，而是一套真正围绕：

```text
Control
Reliability
Safety
Recovery
Observability
Evaluation
```

建设的工程级 Agent Harness。
