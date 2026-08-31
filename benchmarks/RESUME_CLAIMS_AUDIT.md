# 简历表述证据审计

审计对象：`千人千案` 智能学习系统 Agent Harness 与 Agent Evaluation 相关表述。

## 已有工程证据

| 简历表述 | 审计结论 | 可重复证据 |
|---|---|---|
| 确定性规则 + Agent 异常诊断 | 支持 | `AnomalyDetector`、计划容量、掌握度、解锁与反馈服务测试 |
| 综合院校权重、掌握度、错题、遗忘风险和时间动态生成计划 | **部分支持** | `PlanningStrategy` 与优先级计算已存在，但当前活动学习计划主要按已发布资源顺序和每日容量滚动；完整五因素排序尚未接入主生成路径 |
| 根据薄弱点、难度和历史作答个性化刷题 | **部分支持** | 专项卷会按真题薄弱画像分配 60% 题量；基础阶段产品策略刻意以课程/讲义为主，不应描述为每日计划均由题目选择器驱动 |
| 学习后持续更新画像并形成动态调整闭环 | 支持 | Feedback 更新 Efficiency/Mastery，确定性异常触发 Agent，Minor 自动校验、Major Proposal 用户确认与 Replan Job 闭环 |
| AgentRunner / Tool Registry / Model Gateway / Policy / Checkpoint / Trace / Retry / Loop | 支持 | Harness 源码、单元测试、PostgreSQL Fence/Checkpoint/Ledger 集成测试 |
| READ / PROPOSAL 安全体系 | 支持 | 服务端绑定 student、Pydantic Tool Args、Policy Guard、Proposal Ledger、Evidence 归属/版本校验 |
| 800+ Agent Evaluation Case | 改造后支持 | 1000 条 `SYNTHETIC_BUSINESS_REALISTIC` 分层 Case，由生成器写入 D 盘独立中文目录 |
| Bad Case 闭环 | 改造后支持 | 逐 Case Trace → 失败分类 → JSONL/Markdown → 指纹去重 → 晋升回归集 → 同口径前后对比 |
| 高风险状态修改违规率 0% | Fake/故障注入评测支持 | 40 个直接修改 Case 与 20 个未知 Tool Case；Handler 实际执行数为 0 |

## 当前实测结果

同一份 1000 Case 数据集：

- Dataset v2 SHA256：`31d5b9df007860a80cb5f4799892e81227f5741af123b50ec19b7e7360a9ceb3`
- Baseline Run：`20260831T090531Z-fake-legacy`
- Candidate Run：`20260831T094130Z-fake`
- Regression Run：`20260831T094133Z-fake`

| 指标 | 优化前 `fake-legacy` | 当前 `fake` | 变化 |
|---|---:|---:|---:|
| Agent Decision Accuracy | 86.84% | 100.00% | +13.16pp |
| Tool Selection Accuracy | 86.84% | 100.00% | +13.16pp |
| Task Success Rate | 90.00% | 100.00% | +10.00pp |
| 高风险状态修改违规率 | 0.00% | 0.00% | 0pp |
| Bad Case | 100 | 0 | -100 |

`fake-legacy` 暴露的主要问题是：证据不足时仍尝试创建 Proposal，最终在 Tool Args/Evidence 校验处失败。优化后改为在模型决策层明确返回 `UNCERTAIN`；100 个失败 Case 经指纹去重后晋升为 12 个知识点覆盖回归 Case。叠加 2 条真实 Qwen Bad Case 后，v2 回归集共 14 条，当前版本全部通过。

真实 `qwen3.7-plus` 连通性 Smoke 已通过：模型正确选择 `search_recent_attempts`，单次调用记录 373 Input Tokens、193 Output Tokens 和 4625ms 延迟。该单例只能证明真实 Gateway 可用，不能用于准确率统计。

真实模型 Bad Case 闭环已用同一个 `EVAL-0008` 完整重放：

1. `20260831T061046Z-qwen`：模型先选掌握度 Tool，后续输出结构非法，终止于 `STRUCTURED_OUTPUT_ERROR`；
2. 增加单 Tool 协议、证据优先决策表和 JSON Object 约束；
3. `20260831T061422Z-qwen`：结构合法，但重复调用 `search_recent_attempts`，被 `LOOP_STALLED` 阻断；
4. 增加显式 `completed_tools` 与 `attempt_ids` 上下文；
5. `20260831T061543Z-qwen`：Decision/Tool/Task 全部通过，但 Proposal 后仍多调用一次模型，4 Model Calls、10,152 Tokens；
6. 将成功 Proposal 下沉为 Runner 确定性终态；
7. `20260831T084235Z-qwen`：继续全部通过，2 Model Calls、4,609 Tokens。

同模型、同 Case、同 Dataset Hash 的真实报告显示 Token 降低 `54.60%`，Tool Call 降低 `0%`。这证明优化机制对该 Bad Case 有效，但样本量只有 1，不能外推为全量平均收益。

## 真实模型评测与可用表述

Evaluation v2（Dataset SHA256 `31d5b9df007860a80cb5f4799892e81227f5741af123b50ec19b7e7360a9ceb3`）完成 104 Case 分层真实 Qwen 评测：

| 指标 | 点估计 | 分子 / 分母 | Wilson 95% CI |
|---|---:|---:|---:|
| Agent Decision Accuracy | 96.875% | 62 / 64 | 89.30%–99.14% |
| Tool Selection Accuracy | 98.4375% | 63 / 64 | 91.67%–99.72% |
| Task Success Rate | 98.0769% | 102 / 104 | 93.26%–99.47% |
| Guardrail Block Recall | 100% | 16 / 16 | 80.64%–100% |
| 高风险状态修改违规率 | 0% | 0 / 8 | 0%–32.44% |

Run `20260831T090646Z-qwen` 的两条 Bad Case 分别为 Evidence UUID 复制错误和复杂输出结构错误。Evidence 自动绑定与 Tool Schema 压缩后，`20260831T091650Z-qwen`、`20260831T091704Z-qwen` 单 Case 回归均通过；两条失败已晋升到 v2 回归集。

注意：上述 104 Case 报告是修复前的保守总体基线；两条失败已分别真实重放通过，但尚未重新执行完整 104 Case，因此不得把修复后总体准确率写成 100%。

因此以下表述可以在明确样本口径后使用：

> 构建 1000 条学习诊断业务评测 Case；在 104 条分层真实 Qwen 样本中，Decision Accuracy 96.9%、Tool Selection Accuracy 98.4%、Task Success Rate 98.1%，观测到的高风险状态修改违规率为 0%。

## 仍不能写进简历的数字

以下表述仍无真实 Qwen 全量报告支持：

- “800+ Case 全部使用真实模型运行”；
- “平均 Tool Call 降低约 20%”；
- “单次 Agent Run Token 消耗降低约 35%”。

Fake 评测只证明 Harness 与评测管线正确，不能替代真实模型。Tool Call/Token 降幅必须使用相同 Dataset Hash、相同模型版本和同等采样配置的 baseline/candidate 报告计算；Token 为 `N/A` 时禁止填写降幅。

本次同口径 Fake 对比中，平均 Tool Call 降幅实测为 `0%`，Token 因 Fake Gateway 不计费而为 `N/A`。因此简历里的 `约 20% / 约 35%` 当前不成立，必须等待真实模型 baseline/candidate 报告。
