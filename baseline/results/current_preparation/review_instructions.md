# 真人核对填写说明

当前材料是新六题、十八组件，自动数值全部待核对。没有任何已完成的人审记录。

1. A/B 分别领取各自的空白 CSV，先阅读 `tasks.json` 中题面和证据掩码。
2. 只用 `dataset.json` 的原始整数、代币精度和原方案公式独立计算。首次答案提交前不要打开 `references.automatic.private.json`、`current_demo/outputs.json` 或对方填写结果；它们含程序候选值。
3. 每行填姓名、UTC 日期、yes/no 可答性、数值、单位、时点/区间、计算过程与原始区块/调用位置、approve 或 dispute。
4. 单点/峰值时点填完整 ISO UTC；最大单小时区间填 `起点|终点`。窗口差值没有单点时刻，可在计算说明中写出首末两点。
5. 引用单位：占比 percent、占比变化 percentage_points、报价 USDT/USDC、规模敏感度 basis_points、失衡 dimensionless。
6. 连续时间峰值不可由小时样本确定。不可答行的数值留空，说明具体限制；可答组件不能因另一个组件不可答而整题拒答。
7. C 使用仲裁表记录分歧和结论。自动检查发现分歧会阻止正式冻结，需要先调查并一致修订候选或记录，不能用 AI 替代仲裁签字。

A/B 的真实性和独立性无法由程序证明，请团队自行落实。角色姓名未定，程序不会虚构。

核对命令：

```sh
python3 -m curve_audit.current_runner check-human --preparation results/current_preparation --reviewer-a results/current_preparation/reviewer_A.csv --reviewer-b results/current_preparation/reviewer_B.csv
```

空表返回未通过符合当前真实状态。正式模型运行必须等人审、开发题校准和预算配置完成后冻结。
