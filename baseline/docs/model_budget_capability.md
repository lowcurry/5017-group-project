# 模型预算能力与执行门禁

核查日期：2026-10-15（Asia/Hong_Kong）。本次只读取官方公开文档、项目的 `provider.py` 和本机 CLI 的版本及帮助；未读取账户凭证、私有环境文件或发起模型推理。

## 结论

**当前已登录 Codex CLI 路径不能被声明为支持严格的等 token 确认实验。** 新模块 `curve_audit/budget_guard.py` 默认拒绝用该路径派发严格预算实验；不会把已测量的消耗、文字指令或超时机制当成事前硬上限。

本机帮助报告 `codex-cli 0.162.0-alpha.17.2`。`codex exec --help` 有模型、结构化输出、沙箱、JSON 事件和配置参数，没有每次请求输出 token 或累计 token 的硬上限选项。已有 `provider.complete()` 设置的是 subprocess 超时，并在请求结束后读取 usage，不能据此保证预算。

官方证据：

1. [Developer commands / CLI](https://learn.chatgpt.com/docs/developer-commands?surface=cli) 的 `codex exec` 参数表没有提供 token 预算选项。这里的判断与本机帮助相符；未通过试探性推理验证未公开参数。
2. [Configuration Reference](https://learn.chatgpt.com/docs/config-file/config-reference) 中的 `model_context_window` 表示模型上下文窗口，`model_auto_compact_token_limit` 是压缩触发阈值。二者不能作为整个实验的 token 消耗上限；该页未列出 `max_output_tokens` 配置。
3. [Sign in with ChatGPT: Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations) 明确把 `max_output_tokens` 列为该登录路径不支持的请求字段。因此也不能绕过 CLI 向相同的登录路径伪加这个参数。

这个结论限定于核查的当前适配路径。未来使用其他接口，必须重新核实其能力，不能仅因接口也叫 Responses 或支持 usage 就判定合格。

## 合格适配器须证明的能力

门禁 `require_hard_token_budget(capability)` 要求以下五项全部成立，并附上能力证据：

- 请求发出前，精确计数**完整模型输入**，包括指令、历史、工具返回、结构化输出 schema 及实际发送的其他上下文。
- 服务端强制输出 token 上限。
- 输出上限覆盖推理 token，不能只限制可见答案长度。
- 一次受控请求不会在客户端内部产生未计量的额外模型轮次、重试或工具调用。
- 已派发请求的真实输入与输出消耗可核算；异常情况下不能编造零消耗。

`BudgetCapability` 是由可信适配器提供的声明，并非对任意服务的自动认证。本模块自身不连接模型，也不会根据用户自填的布尔值证明接口具备能力。尚未接入满足上述条件的真实适配器，因此当前门禁保持关闭。

配置文件和 CLI 参数**不得注入或覆盖任意 capability**；当前 CLI 接入固定调用 `codex_cli_capability()` 工厂。门禁明确拒绝已核查不支持的 Codex CLI / ChatGPT plan provider 身份，即使调用者将全部能力标志写为 `true`，也不能通过。未来其他适配器必须由受信任代码工厂提供与实际调用路径绑定的声明；换一个 provider 名字并自填证据不构成认证。这个约束是接口集成边界，不是对任意 Python 代码修改的安全沙箱。

若后续只能使用当前 CLI，可另行开展**相同模型和轮数、实际消耗单列的探索实验**；它必须有不同的实验类别和冻结记录，不能冒称本方案要求的严格等 token 确认实验，也不能在遇到门禁后自动回退。

## 账本与公共初稿

`TokenBudgetLedger` 同时维护物理消耗和每个审核分支的归属成本：

- 共享初稿只生成一次，用同一个 `call_id` 归属到 A、B、C。
- `snapshot().actual_usage` 对该初稿只记一次。
- 每个 arm 的 `arm_attribution` 均归属完整初稿成本，用于检查该分支从初稿到终稿的总预算。
- 三个 arm 的归属量不可相加当成实际总消耗。
- `cached_input_tokens` 已包含于 input tokens，`reasoning_tokens` 已包含于 output tokens，二者不重复相加。缓存输入仍计入实验的输入 token 上限。

每次请求必须遵循：

1. `reserve(call_id, arms, exact_input_tokens, max_output_tokens, phase)`：按已用消耗加全部未完成请求的最大可能消耗检查上限；同时保留本次输出额度。
2. 可信适配器在发出一个请求前调用 `acknowledge_dispatch(...)`，确认发送输入与服务端输出 cap 与预留完全一致。该方法不会代替实际接口强制上限。
3. 返回真实 usage 后调用 `settle(call_id, usage)`；释放实际未使用的输出预留。

同一个 `call_id` 不能重复记账；重试必须另建 ID 并再次预留。公共初稿和每个 arm 的请求都消耗 model call 额度。并发请求在同一锁内预留，不能同时透支最后一份额度。

收到真实 usage 超出 cap 或输入计数不一致时，账本保留实际消耗并标为 enforcement violation，阻止后续派发。超时、缺失或格式错误的 usage 保留原预留，标为 unknown，并阻止后续派发；不能取消已派发请求来释放未知消耗。`actual_usage` 仅包含已知 usage 的调用，必须同时检查 `complete_usage` 和 `unknown_usage_calls`；`dispatched_model_calls` 单独记录已派发调用数量。

## 非模型资源

RPC 请求、工具调用、CPU 秒和事件持续时间经 `record_resources()` 独立记账，不折算成 token。共享资源事件也只在物理账本记一次，向各 arm 完整归属。事件持续时间的总和不等于并行实验的经过时间。

金额只可登记已知真实账单，不从 CLI usage 推造单价。账本将金额完整性标为 complete、partial 或 unavailable；已知部分的金额不能被展示为全实验总费用。

## 离线检验与接入边界

`tests/test_budget_guard.py` 只使用虚构的可强制预算测试适配器，覆盖公共初稿分摊、输入/输出/总量/调用次数上限、并发预留、重复记账、未知消耗、上游超限、推理及缓存 token 去重、独立资源事件和能力拒绝。测试适配器不代表已具备真实模型执行能力。

集成到确认实验时，须在创建正式模型实验或发起任何模型请求之前运行能力门禁，并把能力声明、预算配置和最终账本保存到本次运行目录。当前 `provider.py` 的真实调用路径必须通过这一门禁后方可用于严格预算实验；仅增加本模块并不自动完成接入。
