# 人工介入与决策成本

用同一批 Curve 3pool 历史调查判断比较六种策略。每个已生成的判断可直接采用、追加核验或交由人工。成本包括共享取数、模型生成、按需核验、人工时间、错误损失及适用的风险模型推理和训练摊销。

## 数据和口径

两个开发窗口合计 36 次预定研究判断，任务包括池内 USDC 占比方向、失衡峰值时点和集中度告警。同一事件及其所有重复运行共同留出。预测风险时只使用任务类型、报告引用数量和报告字段是否存在等当时可见的信息。逻辑回归和两种 CPU 神经网络方案都按此口径评估。神经网络先训练一个小型表格自编码器，再比较仅训练分类头和同时微调末个编码层。

历史 36 次决策的成本计价保留可调整的情景参数作为开发基准。默认每次人工复核 5 分钟，每分钟 1 个成本单位，每次追加核验 0.2 个单位。人工复核后的残余错误概率默认 2%。币种余额、链上请求次数和模型用量来自归档。历史 36 决策归档（`results/human-observations.json`）保持 0 条实测记录，不把留出窗口的实测人工耗时外推回开发集。全量核验相对原流程多 9 次转人工并拦下一次错误判断。按默认情景，避免这一错误的损失需超过 52.2 个成本单位（完美复核）或 67.7 个成本单位（计入残余错误）才划算。[情景参数](scenario.json) · [逐项结果](results/policy-trace.csv) · [盈亏临界点与风险分母](results/diagnostics.json)

## 结果、补证扩展与三窗口确认实验

- **开发集历史回放（36 次判断）**：成本规则和逻辑回归在历史回放中均有 2 次转人工、0 次自动错误，平均成本约 0.65 单位。两个末层训练方案各有 4 次转人工、0 次自动错误，成本约 1.18 单位。原始流程的 35 次自动判断只有 1 次错误，数据支持历史策略比较和情景分析；小样本统计上界（Clopper-Pearson 与 Beta 先验）及叠加确定性证据补齐后的策略对比（全量核验转人工 10→2、平均成本 2.27→0.78、盈亏临界点 52.2/67.7→12.2/14.2）见 [`../audit/evidence_recovery/README.md`](../audit/evidence_recovery/README.md)。
- **三个独立留出窗口确认（已完成，54 次判断）**：`2023-06-15`、`2023-10-23` 和 `2024-01-11` 均已完成双源核对、两位审核人九项参考一致确认及 18×3 = 54 次正式模型运行（配对比较全量独立算术核验与叠加确定性补齐证据）。三窗口合计转人工从 **12/54 降至 3/54**、正确发布从 **120 增至 132**，未观察到自动误放；DeepSeek 平台实际扣费 **¥0.34073796**；`2023-06-15` 两项基准转人工的实测人工复核用时为 **6 分 48 秒与 8 分 18 秒（合计 15 分 06 秒，未外推）**。详见 [`../audit/holdout_validation/RESULTS.md`](../audit/holdout_validation/RESULTS.md)。

## 复现

从仓库根目录运行以下命令。

```sh
python3 -m pip install -r decision_cost/requirements.txt
python3 -B decision_cost/evaluation.py --output /tmp/5017-cost-replayed
python3 -B -m unittest discover -s decision_cost -p "test_*.py" -v
```

可更改情景参数并用 `--scenario /path/to/scenario.json` 重跑。存在实测人工记录时，可以用 `--reviews /path/to/timing.csv` 载入，生成的空白模板在本地回放的输出目录。风险评分计时与其货币折算可随机器和负载变化。独立窗口的实测归档重放见 `audit/holdout_validation/replay.py`，复用流程见仓库根目录的 `CONFIRMATION.md`。

[成本分解](results/cost.svg) · [人工介入与错误](results/workload.svg) · [敏感性矩阵](results/sensitivity.svg) · [主结果 CSV](results/policy-summary.csv)
