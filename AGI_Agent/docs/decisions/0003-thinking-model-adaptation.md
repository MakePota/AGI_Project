# 0003 适配思考型模型：统一 `json_mode`、不自动绑定工具

- 日期：2026-10-12
- 状态：已采用

## 背景

四个角色当前全部绑定 DeepSeek `deepseek-flash`，它是**思考型模型**。实测发现两条
LangChain 的默认路径在它上面都走不通：

| 路径 | 结果 |
| --- | --- |
| 默认结构化输出（`response_format` 的 `json_schema`） | `400 This response_format type is unavailable now` |
| `method="function_calling"` | `400 Thinking mode does not support this tool_choice` |

第二条的含义更重要：思考模式**不接受被强制的 `tool_choice`**。这也意味着
LangChain 的自动工具绑定（让模型自己挑选并填充工具参数）同样不可用。

## 决策

- **结构化输出统一走 `json_mode`**，并把 JSON Schema 写进提示词。
  封装为 `providers/llm/structured.py` 的 `invoke_structured()`——**供应商差异只集中在这一处**。
- **不使用 LangChain 的自动工具绑定**。改由 `tools` 注册表发布工具签名、`planner` 产出
  `arguments`、`executor` 按签名校验之后再调用。详见 ADR 0007。

## 被否决的方案

- **换一个非思考型模型来迁就框架**：把模型选型交给框架的限制，方向反了。
  思考型模型在规划环节恰恰是有价值的。
- **为每个供应商写一套分支逻辑**：差异会扩散到所有调用点，正是 ADR 0002 想避免的事。
- **降级成「让模型自由输出文本，再用正则解析」**：不可靠、难调试，出错时无法定位。

## 后果

- 供应商差异被关进一个文件（`structured.py`）。将来接 Claude 时，只需要在这里加分支，
  而不是改每个节点的调用。
- **但换供应商仍然需要实测**：上下文长度、并发限制、错误码各家不同，目前尚未逐个验证。
  因此对外表述是「换供应商**成本低**」，不是「换供应商无感」。
- 因为是手动绑定工具，`planner` 输出的 `arguments` 必须在执行前校验（ADR 0007），
  否则模型编造的参数名会以 `TypeError` 的形式炸在 executor 里，报错信息毫无诊断价值。
