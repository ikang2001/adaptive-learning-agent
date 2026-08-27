# Agent 决策层实施

## 1. 本层定位

Agent 不是“整个系统”。

Agent 负责：

> 在不确定、需要语义判断、需要多工具组合时做动态决策。

确定性规则能完成的事情，不要强行调用 Agent。

---

# 2. Agent 适用场景

## 2.1 Feedback Diagnosis

判断：

- 本次反馈是否异常？
- 可能是什么知识缺陷？
- 需要查什么证据？
- 是否涉及前置知识？

## 2.2 Replan Decision

判断：

- 只是微调；
- 还是重排；
- 是否切换阶段。

## 2.3 Practice Strategy

复杂场景中判断：

- 继续基础题；
- 提升难度；
- 切换题型；
- 先补前置知识。

## 2.4 Mock Strategy

判断：

- 专项模拟；
- 全真模拟；
- 继续真题；
- 回退补弱点。

---

# 3. 不适合 Agent 的事情

- 正确率计算；
- 时间统计；
- 数据库查询；
- 最大题量；
- 题目去重；
- 计划时间硬约束；
- 模拟卷总分校验；
- 状态合法性；
- 权限判断。

---

# 4. 单 Agent 优先

第一版不要直接多 Agent。

推荐一个：

```text
LearningDecisionAgent
```

通过不同 tool 完成：

```text
diagnose
search_history
retrieve_evidence
query_school_profile
query_questions
propose_replan
propose_stage_transition
propose_mock_strategy
```

后续复杂后再拆：

```text
DiagnosisAgent
PlanningAgent
MockExamAgent
```

---

# 5. Agent 输入上下文

只给当前决策真正需要的信息：

```text
student_summary
current_plan
current_feedback
recent_attempt_summary
candidate_knowledge_states
school_focus
available_tools
```

不要把所有历史对话塞进去。

---

# 6. Tool 设计

Tool 必须：

- 输入 Schema 明确；
- 输出结构化；
- 幂等性清晰；
- 读写权限清楚；
- 风险等级清楚。

例如：

```text
search_recent_attempts
retrieve_knowledge_evidence
get_student_knowledge_state
get_school_knowledge_stats
search_questions
propose_plan_adjustment
commit_plan_adjustment
update_error_profile
```

推荐把：

```text
propose_xxx
commit_xxx
```

分开。

Agent 先提出建议，再由确定性逻辑校验后提交。

---

# 7. 动态路径示例

## 正常反馈

```text
Feedback
↓
Agent
↓
状态正常
↓
update_recent_stats
↓
END
```

## 异常反馈

```text
Feedback
↓
Agent
↓
search_recent_attempts
↓
发现连续同类错误
↓
retrieve_knowledge_evidence
↓
检查前置知识
↓
propose_error_profile_update
↓
propose_replan
↓
END
```

不同学生执行路径不同。

这才体现 Agent。

---

# 8. Zero-Token 分支

很多情况不需要 LLM。

例如：

```text
完成率 100%
耗时处于正常范围
正确率 > 80%
无连续错误
```

直接：

```text
deterministic update
```

只有复杂异常才调用 LLM。

价值：

- 降低成本；
- 降低延迟；
- 提升稳定性。

---

# 9. Agent 输出

Agent 不直接输出自然语言长文。

应该结构化：

```json
{
  "decision": "MAJOR_REPLAN",
  "reason_codes": ["REPEATED_ERROR", "TIME_OVERRUN"],
  "candidate_weaknesses": [],
  "required_actions": [],
  "confidence": 0.82
}
```

自然语言解释最后单独生成。

---

# 10. Agent 评测

至少：

- 路由正确率；
- 是否调用了不必要工具；
- 工具参数正确率；
- 无证据诊断率；
- Replan 决策准确率；
- 平均 Agent 轮次；
- Token；
- 延迟；
- 终止成功率；
- 工具失败恢复率。

---

# 11. 可持续优化

Agent 策略必须版本化：

```text
agent_policy_version
system_prompt_version
tool_schema_version
model_version
```

每次改 Prompt 或模型都跑 regression benchmark。
