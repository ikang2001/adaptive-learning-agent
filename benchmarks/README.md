# Agent Evaluation 资产说明

`agent_cases.json` 的 20 条轨迹是快速 Smoke Benchmark，只用于阻止最基础的 Decision/Termination 回归，不能代表真实模型效果。

完整评测由以下可重复脚本构建：

```text
generate_evaluation_dataset.py
        ↓
1000 条分层业务 Case
        ↓
run_evaluation.py
        ↓
逐 Case Trace + 聚合指标 + Bad Case
        ↓
promote_bad_cases.py
        ↓
回归案例集
        ↓
compare_evaluation_reports.py
```

本地业务数据和运行结果不提交仓库，统一保存在：

```text
D:\CodexTemp\千人千案评测业务数据
├─ 数据集
├─ 评测运行
├─ 坏案例
├─ 回归案例
└─ 对比报告
```

数据来源标记为 `SYNTHETIC_BUSINESS_REALISTIC`：根据学习超时、正确率、重复错误、连续低完成度、证据不足、Prompt Injection、Tool 故障和 Guardrail 边界构造，不包含真实学生 PII。

真实模型评测必须显式使用：

```powershell
python scripts/run_evaluation.py --gateway qwen --confirm-real-model `
  --dataset 'D:\CodexTemp\千人千案评测业务数据\数据集\学习诊断评测集_v2.jsonl'
```

没有带 `gateway=qwen`、数据 Hash、模型版本和逐 Case 结果的报告，不得声称“真实模型准确率”或 Token 优化收益。
