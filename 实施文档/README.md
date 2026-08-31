# 自动控制原理「千人千案 + 千人千卷」Agent 文档包

本目录用于指导 AI/Codex 分阶段实现项目。

## 阅读顺序

1. `00_项目总体设计.md`
2. `01_领域模型与状态设计.md`
3. `02_数据与知识库层实施.md`
4. `03_学生画像与诊断层实施.md`
5. `04_规划与针对性刷题层实施.md`
6. `05_反馈_Replan_长期记忆层实施.md`
7. `06_真题与千人千卷层实施.md`
8. `07_Agent决策层实施.md`
9. `08_Agent_Harness运行时实施.md`
10. `09_后端存储_API_可观测实施.md`
11. `10_评测_优化_版本治理_实施路线.md`
12. `11_企业级Agent_Harness完善方案.md`

## 项目核心

```text
Student Model
    ×
School Model
    ×
Question Model
    ×
Exam Model
        ↓
Adaptive Planning
        ↓
Targeted Practice
        ↓
Feedback
        ↓
Diagnosis
        ↓
Replan
        ↓
True Exam
        ↓
Personalized Mock Exam
```

Agent 负责动态决策，Harness 负责可靠执行。

## 推荐实施顺序

不要先写 Agent。

```text
知识树 / 真题 / Question Pool
↓
领域模型
↓
反馈闭环
↓
Student Model
↓
Planner
↓
Question Selector
↓
Agent Decision
↓
Harness
↓
真题画像
↓
千人千卷
↓
Benchmark / Optimization
```

原因：没有稳定业务状态和真实数据，Agent 只能变成 Demo。
