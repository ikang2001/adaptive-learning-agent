# Agent-Harness 运行时实施

## 1. 本层目标

Harness 负责把“LLM 想做什么”变成：

> 可控、可靠、可恢复、可审计的 Agent 执行过程。

---

# 2. 核心组件

```text
AgentRunner
ToolRegistry
StateManager
CheckpointStore
PolicyGuard
ToolExecutor
RetryManager
TraceRecorder
TerminationPolicy
ModelGateway
```

---

# 3. AgentRunner

负责循环：

```text
load state
 ↓
build context
 ↓
call model
 ↓
parse action
 ↓
validate action
 ↓
execute tool
 ↓
record observation
 ↓
update runtime state
 ↓
check termination
 ↓
next loop
```

---

# 4. ToolRegistry

ToolDefinition：

```text
name
version
description
input_schema
output_schema
risk_level
timeout_ms
retry_policy
idempotent
permission_tags
```

所有工具统一注册。

---

# 5. StateManager

区分：

## Runtime State

本次 Agent 执行临时状态：

```text
run_id
student_id
current_goal
loop_count
observations
candidate_hypotheses
last_action
```

## Business State

来自数据库：

```text
StudentProfile
Plan
Feedback
...
```

Harness 不应该把业务状态永久存在内存里。

---

# 6. Checkpoint

每轮重要动作后：

```text
checkpoint
```

保存：

- 当前 loop；
- 已调用工具；
- observations；
- 模型输出；
- pending action。

失败后从最近 checkpoint 恢复。

---

# 7. Tool Execution

流程：

```text
Action
↓
schema validate
↓
permission validate
↓
state precondition
↓
execute
↓
timeout
↓
retry if allowed
↓
normalize error
↓
record
```

错误分类：

```text
PARAMETER_ERROR
BUSINESS_ERROR
TIMEOUT
TEMPORARY_INFRA_ERROR
PERMISSION_ERROR
MODEL_ERROR
UNKNOWN_ERROR
```

---

# 8. Retry

读工具：

可以有限重试。

写工具：

必须判断幂等。

例如：

```text
commit_plan_adjustment
```

必须有：

```text
idempotency_key
```

否则 Agent 重试可能重复改计划。

---

# 9. Guardrail

至少包括：

## 9.1 状态修改约束

LLM 不能直接写 mastery。

## 9.2 计划约束

不能生成：

```text
预计 5 小时
```

但学生今天只有 2 小时。

## 9.3 证据约束

关键薄弱点诊断没有 evidence 时：

```text
UNCERTAIN
```

## 9.4 模拟卷约束

必须经过 Exam Validator。

---

# 10. Termination

业务终止：

- 已得到足够证据；
- 无需进一步诊断；
- 已产生合法 proposal；
- 信息不足，需要用户补充。

工程终止：

- max_steps；
- max_token_budget；
- max_tool_calls；
- wall-clock timeout。

---

# 11. Trace

每次 run 保存：

```text
run_id
student_id
goal
model_version
prompt_version
loop
action
tool
tool_args_digest
observation_digest
latency
token_usage
decision
termination_reason
```

用于：

- Debug；
- 面试演示；
- Benchmark；
- 成本分析；
- 回放。

---

# 12. ModelGateway

业务代码不要直接调用具体 OpenAI/其他厂商 SDK。

统一：

```text
ModelGateway
```

支持：

- provider 切换；
- timeout；
- retry；
- structured output；
- token 统计；
- fallback；
- model version。

---

# 13. Prompt 管理

Prompt 不写死在 Python 大字符串中。

推荐：

```text
prompts/
  diagnosis/
    v1.yaml
    v2.yaml
  replanning/
  mock_exam/
```

每次执行记录 prompt_version。

---

# 14. 防止死循环

除 max_steps 外，还需要：

```text
repeated_action_detector
no_new_information_detector
```

如果 Agent 连续：

```text
search_recent_attempts
search_recent_attempts
search_recent_attempts
```

直接终止并返回：

```text
LOOP_STALLED
```

---

# 15. 后续演进

V1：
单进程 Harness

V2：
异步 Worker + Checkpoint

V3：
分布式 Lease / Fence

V4：
多模型路由 / 成本控制

V5：
多 Agent 协作

第一版不要一步上多 Agent 和分布式。
