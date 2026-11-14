# MSBD 5017 · Curve 3pool 链上证据与可复算核验

**研究问题：给生成报告加上可定位、可复算的链上证据，能否让更多正确结论通过核验？** 我们用 Curve 3pool 在两次稳定币压力事件中的历史快照，比较普通提示（G）和证据引导提示（E），并回放核验如何影响报告发布与后续研究判断。

| 实验 | 规模 | 主要结果 |
| --- | --- | --- |
| 正式 G/E 对照 | 2 个历史窗口、6 题、36 次计划模型运行 | 可复算正确覆盖率为 G **34/48**、E **43/48**；正式发布后保留的正确组件为 G **31/48**、E **42/48** |
| 发布前独立算术核验 | 6 个故意注入的共享错误和 6 个正确对照 | 新核验拦下 **6/6** 个同时通过 V1、V2 的错误，保留 **6/6** 个正确对照，并撤回 2 条依赖错误峰值时点的报价 |
| 历史决策回放 | 36 次研究判断、50 个小时取样点的报价筛查 | 核验拦下 1 次错误判断，同时将 8 次原本正确的判断转人工；五档最低输出门槛下，两种报价方法的筛查结果一致 |

[公开交接包](MSBD5017_Public_Handoff.zip) · [技术报告](MSBD5017_Final_Project_Report.pdf) · [组内交接](5017_GROUP_PROJECT_HANDOFF.md) · [范围与证据来源](LIMITATIONS.md)

## 1 数据与核验流程

数据覆盖 2023 年 3 月 USDC 脱锚和 2022 年 11 月 FTX 事件窗口。两个存档数据源各提供 51 个快照，涵盖 Curve 3pool 的代币余额、区块信息和指定输入规模的合约报价。

1. **冻结输入与规则。** 固定历史快照、题目、参考答案、评分容差和运行安排。六题共 18 个组件，其中 16 个可答，2 个检验证据是否足够支撑结论。
2. **生成结构化报告。** 普通提示组（G）和证据引导组（E）各安排 18 次模型运行。每条结构化主张可以附上数值、单位、时点和证据引用。
3. **重算并作出发布判断。** V1 使用主存档重算，V2 使用第二份 RPC 存档核对数据来源并重算。发布时保留通过的主张，也检查它所依赖的其他主张是否已通过。
4. **检查共享算术错误。** V1 和 V2 共用验证算术。新版单份报告及批量发布都额外从原始整数余额独立计算占比、占比变化和采样失衡峰值，并核对时点。报价算术仍由 V2 核验。

## 2 怎么读正式结果

| 指标 | 普通提示 G | 证据引导 E |
| --- | ---: | ---: |
| 计划运行 | 18 | 18 |
| 完成提交 | 16 | 18 |
| 原始数值正确 | 40/48 | 45/48 |
| 正确且可按证据复算 | 34/48 | 43/48 |
| 正式发布后保留的正确组件 | 31/48 | 42/48 |

48 是每组计划运行中可答组件的总数。G 组的两次预算停止计入分母。可复算正确覆盖率同时要求答案正确及证据支持重算。正式结果见包内 `experiments/formal_v1/result_summary.json`。参考答案的复核与正文审阅各有自己的统计口径，见[组内交接](5017_GROUP_PROJECT_HANDOFF.md)和[证据来源说明](LIMITATIONS.md)。

## 3 补充实验与应用边界

- **算术故障。** 在两个窗口内模拟分母漏项、代币精度错设和最小值误作最大值。对同一组案例，错误发布数从 6/6 降到 0/6，正确对照保持 6/6。包内 `audit/arithmetic_faults/` 保存逐案结果和复现入口。
- **研究决策回放。** 36 次方向判断、后续小时选择及集中度告警中，直接使用模型报告得到 34 次正确、1 次错误、1 次转人工。使用已核验报告后为 26 次正确、0 次错误、10 次转人工。回放记录了核验减少的错误和增加的人工复核量。
- **报价筛查。** 对 50 个历史取样点，将 1,000 USDC 的报价按比例外推至 100,000 USDC，每次都高估合约对完整规模的报价。最大差约 2.87 USDT。五档测试门槛的通过结果一致。实验衡量历史合约报价和规则判断，实际成交、费用和收益超出本次测量范围。

详见[技术报告](MSBD5017_Final_Project_Report.pdf)第 4、6 页，以及包内 `audit/decision_replay_20261112/`。[报告勘误](REPORT_ERRATA.md)补充了 [PR #1](https://github.com/lowcurry/5017-group-project/pull/1) 提出的复现与证据说明。

## 4 离线复现

下载 [公开交接包 ZIP](MSBD5017_Public_Handoff.zip) 和 [SHA-256 校验值](SHA256SUMS.txt)。解压后进入 `MSBD5017_Public_Handoff` 目录；以下命令使用 Python 3.10 或更新版本，无需 API key 或网络。

```sh
python3 -B docs/verify_delivery.py .
python3 -B -m curve_audit replay
python3 -B docs/replay_formal_faults.py .
python3 -B docs/check_code_provenance.py .
python3 -B audit/arithmetic_faults/run_arithmetic_faults.py .
python3 -B audit/arithmetic_faults/replay_publication_guard.py .
python3 -B audit/decision_replay_20261112/replay.py .
python3 -B docs/reproduce_current.py
python3 -B -m unittest discover -s tests
```

正式复现覆盖 36 次计划模型运行、6 份模板输出和 32 个冻结故障案例。当前公开包通过了 285 项 Python 测试。解压后的 README 提供各命令和结果文件的对应关系。

其他材料：[展示稿](MSBD5017_Project_Presentation.pptx) · [展示措辞修订说明](SLIDE_TEXT_PATCH.md) · [正文审阅 A](%E6%AD%A3%E6%96%87%E5%AE%A1%E9%98%85%E8%A1%A8-%E8%8D%89%E7%A8%BF-A.xlsx) · [正文审阅 B](%E6%AD%A3%E6%96%87%E5%AE%A1%E9%98%85%E8%A1%A8_%E5%B7%B2%E5%AE%8C%E6%88%90.xlsx)
