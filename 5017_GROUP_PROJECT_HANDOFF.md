# MSBD 5017 Group Project 进度交接

更新于 2026 年 11 月 8 日（香港时间）。以 2026 年 11 月 6 日复审版为基线。

## 项目与当前状态

本项目用 Ethereum 上的 Curve 3pool 研究两段市场压力期（2022 年 FTX 事件与 2023 年 USDC 脱锚）。系统将模型回答拆成结构化结论，绑定数据、区块、时间与计算依据，再用 V1 从主来源复算、用 V2 对照独立采集的第二 RPC 来源。发布流程按依赖处理结论，只输出通过核验的字段。

| 模块 | 当前进度 | 复查入口（ZIP 解压后） |
| --- | --- | --- |
| 数据与任务 | Alchemy 与 dRPC 各冻结 51 个小时级观测（USDC 26 点、FTX 25 点）。六题共 18 个答题组件，包含证据不足时应拒答的组件。 | `data/current_study/`、`results/current_preparation/` |
| 工程实现 | Python 调查、预算控制、双源验证、依赖撤回和受控发布已实现。Ganache 本地链完成证据包哈希登记及篡改演示。 | `curve_audit/`、`contracts/`、`results/current_chain/anchor.json` |
| 正式实验 | 冻结 36 次模型调度（G、E 各 18 次）及 6 份确定性模板。G 有 2 次预算停止，E 全部提交。已离线重放正式评分、发布记录和 32 个冻结故障案例的 64 项判定。 | `experiments/formal_v1/result_summary.json`、`results/submission_validation/formal_replay.json` |
| 人工参考与正文复核 | 人工参考采用组内提供的合并工作簿，程序核对文件绑定和 18 个组件。独立 A/B 与仲裁过程无可核验记录。正文另做一次规则化复核，覆盖 34 份实际提交的模型文本。 | `results/current_preparation/reference_review/`、`audit/PROSE_AUDIT.md` |
| 交付材料 | 技术报告 PDF、7 页展示 PPT（含备注）、5 分钟排练稿与可复现工程均已备齐。课程平台提交状态待组员核对。 | `report/`、`presentation/` |

## 正式结果与解释口径

G 和 E 使用同一 DeepSeek 模型及预算上限。E 的提示额外指导如何给出证据引用和可复算依据。T 是按固定公式计算的模板基线。

| 条件 | 原始正确 | 可复算且正确 | 通过发布且正确 |
| --- | ---: | ---: | ---: |
| G · 通用提示 | 40/48 | 34/48 | 31/48 |
| E · 证据引导提示 | 45/48 | 43/48 | 42/48 |
| T · 确定性模板 | 16/16 | 16/16 | 16/16 |

G 的两次预算停止遗漏 5 个可答组件，这些组件仍计入正式分母。排除这两个配对后，两组的原始正确均为 40/43，可复算且正确为 G 34/43、E 39/43。试跑观察影响了指标选择，评分规则在正式运行前冻结。正文复核采用单独的分母，作为描述性结果。研究范围限于一个池、两个事件窗口与六道固定题。链上哈希演示在本地临时链完成。

## 组员接手步骤

1. 从仓库下载 `MSBD5017_Public_Handoff.zip` 并解压。进入 `MSBD5017_Public_Handoff/`，使用 Python 3.10 或以上运行以下命令。前两项核对文件与正式结果，后两项检查离线工程流程与测试。四项均可在无 API 密钥的环境运行。

   ```bash
   python3 -B docs/verify_delivery.py .
   python3 -B docs/verify_formal_replay.py
   python3 -B docs/reproduce_current.py
   python3 -B -m unittest discover -s tests
   ```

2. 展示负责人先看 `presentation/Demo_and_Rehearsal.md`，按报告与 PPT 统一 G/E 分母、预算停止和人工复核口径。终端演示可直接运行正式重放脚本，备用输出在 `results/submission_validation/formal_replay.json`。
3. 提交负责人到课程平台核对截止时间、提交人及要求的文件格式，按要求使用组内留存的 11 月 6 日复审版原提交 ZIP 或报告、PPT 单件，并在组内同步提交回执。

公开交接包包含实现、测试、冻结输入和结果、人工参考计算表、报告与展示材料。课程提案原件、教师反馈、内部方向与试跑笔记、填有凭据的本地配置以及课程提交 ZIP 由组内单独保管。
