# 后端、存储、API 与可观测实施

## 1. 推荐技术栈

MVP：

```text
FastAPI
PostgreSQL
SQLAlchemy Async
Alembic
Redis（可后加）
LanceDB / pgvector（二选一）
对象存储 / 本地文件
异步任务队列（V2）
```

---

# 2. 分层

```text
api/
application/
domain/
agent/
harness/
tools/
repositories/
infrastructure/
observability/
```

---

# 3. API 设计

## 学生

```text
POST /students
GET  /students/{id}
PUT  /students/{id}/availability
```

## 资料

```text
POST /schools/{school_id}/materials
POST /students/{id}/wrong-questions
```

## 计划

```text
POST /students/{id}/plans/generate
GET  /students/{id}/plans/current
POST /plans/{plan_id}/replan
```

## 反馈

```text
POST /tasks/{task_id}/feedback
```

## 题目

```text
GET /students/{id}/practice/today
POST /questions/{question_id}/attempt
```

## 真题

```text
POST /students/{id}/true-exams/{exam_id}/submit
GET  /students/{id}/true-exam-profile
```

## 模拟卷

```text
POST /students/{id}/mock-exams/generate
GET  /mock-exams/{id}
POST /mock-exams/{id}/submit
```

## Agent Runs

```text
GET /agent-runs/{run_id}
```

---

# 4. 事务边界

例如反馈提交：

```text
create attempt
create feedback
update lightweight stats
commit
```

深度 Agent 诊断：

建议异步：

```text
feedback committed
↓
enqueue diagnosis job
↓
Agent run
↓
proposal
↓
commit state changes
```

避免请求一直挂着。

---

# 5. 数据库核心表

建议至少：

```text
students
knowledge_nodes
student_knowledge_states
school_profiles
school_knowledge_stats
questions
question_attempts
learning_efficiency_profiles
error_profiles
weekly_plans
plan_tasks
feedback
true_exam_profiles
exam_profiles
mock_exams
mock_exam_questions
agent_runs
agent_steps
tool_invocations
domain_events
```

---

# 6. Repository 原则

领域层不直接依赖 SQLAlchemy。

定义 Port：

```text
StudentRepository
QuestionRepository
PlanRepository
AttemptRepository
SchoolProfileRepository
AgentRunRepository
```

便于测试和以后替换存储。

---

# 7. Outbox

如果后续使用 MQ，涉及：

```text
feedback committed
→ async diagnosis
```

建议加入 Outbox，避免：

```text
DB 已提交
但消息发送失败
```

MVP 可以先同步/简单任务队列，V2 再补。

---

# 8. 可观测

每个用户请求：

```text
trace_id
```

每次 Agent：

```text
run_id
```

每次 Tool：

```text
invocation_id
```

链路：

```text
trace_id
  ↓
run_id
  ↓
step_id
  ↓
tool_invocation_id
```

---

# 9. Metrics

业务指标：

```text
plan_completion_rate
estimated_vs_actual_duration_error
knowledge_accuracy_trend
true_exam_score
mock_exam_score
replan_frequency
```

Agent 指标：

```text
agent_run_success_rate
avg_steps
avg_tool_calls
token_usage
latency
fallback_rate
loop_stall_rate
```

工具指标：

```text
tool_success_rate
timeout_rate
retry_rate
```

---

# 10. 日志

结构化日志：

```json
{
  "trace_id": "...",
  "run_id": "...",
  "student_id": "...",
  "event": "tool_invocation",
  "tool": "search_questions",
  "latency_ms": 120
}
```

不要把完整用户私有讲义正文直接打进日志。

---

# 11. 数据安全

学生资料属于私有数据。

至少：

- user/student scope；
- 文件访问权限；
- 删除接口；
- 日志脱敏；
- Agent Tool 权限；
- generated content provenance；
- 管理员操作审计。

---

# 12. 后续扩展

V2：

- Redis；
- Worker；
- Outbox；
- job retry；
- scheduled weekly review。

V3：

- 多租户；
- 对象存储；
- 权限体系；
- 分布式任务；
- 管理后台。
