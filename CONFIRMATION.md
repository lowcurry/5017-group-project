# 新窗口确认

新窗口为 2023-06-15 00:00 至 06-16 00:00 UTC。两来源各有 26 个小时目标（含追问需用的后一小时），原始回执和补录保存在 `decision_cost/holdout/raw/`。两个来源的区块、余额和报价通过逐项核对。发布时正式模型运行数为零。

在仓库根目录运行下列命令生成三道题、九项候选参考、两份审阅表和 18 次计划运行。

```sh
python3 -B study/prepare_window.py \
  --primary decision_cost/holdout/raw/holdout_20230615_alchemy_erc20.json \
  --secondary decision_cost/holdout/raw/holdout_20230615_drpc_erc20.json \
  --output decision_cost/holdout/preparation
```

协调者各发一份空白审核表、tasks.json 及两来源数据。审核人各自独立填写九项。答题时使用题目的允许样本，按原始整数与币种精度复算。填写日期、证据或计算过程以及结论。answerable 填 yes 或 no。目标时点使用 ISO UTC，区间的开始和结束用竖线连接。非窗口定位的变化量可留空时间栏。协调者收回两份审核、完成必要的仲裁，再使用已有模型权限运行：

```sh
python3 -B decision_cost/confirmation.py \
  --review-a /path/to/completed-review-A.csv --review-b /path/to/completed-review-B.csv \
  --output /path/to/new-confirmation
```

存在分歧时通过 `--adjudication` 载入真实仲裁结果。该命令会验证冻结文件、两来源记录和人工参考，比较 18 次 G/E 调查的下游决策，并生成三份确定性模板。
