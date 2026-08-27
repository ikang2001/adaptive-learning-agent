# 反馈、Replan 与长期记忆层实施

## 1. 本层目标

让系统不是“一次性计划生成器”，而是真正长期跟踪学生。

---

# 2. 每日反馈输入

至少：

```text
task_id
是否完成
实际耗时
完成数量
正确数量
是否看答案
自评难度
自由复盘
```

可选：

```text
中途暂停
超时原因
临时事务
精神状态
```

后者第一版可以不用于核心算法，避免噪声。

---

# 3. Feedback Processing

流程：

```text
接收反馈
 ↓
校验任务归属
 ↓
写入原始反馈
 ↓
写入 QuestionAttempt
 ↓
计算实时统计
 ↓
判断是否异常
 ↓
轻量更新 or Agent 深度诊断
```

---

# 4. 异常判断

确定性信号：

```text
completion_ratio < threshold
actual_duration >> expected_p75
accuracy < threshold
same_error repeated
true_exam_score dropped
```

例如：

```text
预计 100~130min
实际 210min
正确率 30%
连续 3 次同知识点错误
```

强异常。

---

# 5. Agent 深度诊断触发条件

不要每次反馈都调用大模型。

可以触发：

- 连续同类错误；
- 预计耗时严重偏差；
- 正确率突然下降；
- 计划连续多天未完成；
- 真题表现与普通练习明显冲突；
- 学生文字反馈中出现新问题；
- Replan 前需要解释。

这样可以控制 Token 和延迟。

---

# 6. Replan 决策

输出：

```text
NO_CHANGE
MINOR_ADJUST
MAJOR_REPLAN
STAGE_TRANSITION
```

## NO_CHANGE

正常执行。

## MINOR_ADJUST

只调整：

- 明日题量；
- 某知识点权重；
- 题目难度。

## MAJOR_REPLAN

调整未来数天。

## STAGE_TRANSITION

例如：

```text
STRENGTHEN → TRUE_EXAM
TRUE_EXAM → MOCK_EXAM
```

---

# 7. 长期记忆的正确拆分

不要把所有历史对话塞到 LLM。

建议：

## 7.1 结构化长期状态

数据库中：

- mastery；
- error profile；
- efficiency；
- plans；
- attempts；
- true exam performance；
- mock performance。

这是最重要的“长期记忆”。

## 7.2 语义记忆

存：

- 用户复盘；
- 典型错误解释；
- 学习偏好；
- 重要历史诊断。

用于 RAG。

## 7.3 短期 Agent State

当前一次执行：

```text
current_feedback
candidate_hypotheses
tool_results
decision
loop_count
```

---

# 8. Event Sourcing 思路

推荐至少保存业务事件。

原因：

未来 mastery 算法升级时，可以：

```text
历史 QuestionAttempt
+
历史 Feedback
↓
重新计算 Student Model
```

而不是被旧的 mastery 数值锁死。

---

# 9. 计划版本

每次 Replan：

```text
WeeklyPlan v1
→ WeeklyPlan v2
```

旧计划不删除。

PlanTask 也记录：

```text
superseded_by
adjustment_reason
```

用户可以看到：

> 为什么昨天和今天计划不同？

---

# 10. 防止 Agent 过度干预

规则：

- 单次低质量反馈不触发大改；
- 低置信诊断不直接改长期画像；
- LLM 无证据不能提升/降低 mastery；
- Agent 只能提出 change proposal；
- 关键状态通过确定性 Update Tool 落库。

---

# 11. 后续优化

可逐步加入：

- 遗忘曲线；
- spaced repetition；
- 学习稳定性；
- 周期性回归测试；
- 阶段性目标；
- 长期趋势预测；
- 个性化 Replan 阈值。
