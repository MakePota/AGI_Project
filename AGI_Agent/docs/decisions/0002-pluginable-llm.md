# 0002 模型层可插拔：角色绑定配置，不绑定供应商

- 日期：2026-10-12
- 状态：已采用

## 背景

候选供应商有 Claude、ChatGPT、Kimi、智谱、千问、DeepSeek。个人项目需要**低成本试错**——
今天觉得 DeepSeek 便宜就用 DeepSeek，下周想试 Claude 应该只改一个地方，而不是全局搜索替换。

同时，节点代码不应该知道自己在用哪家模型：那会让「换供应商」从配置问题变成重构问题。

## 决策

与 `tools` 同构：**注册表 + 工厂 + 配置驱动**。

- 接口：`get_model(role) -> BaseChatModel`。节点只认**角色**，不认供应商。
- 供应商注册表：`openai` → `ChatOpenAI`（OpenAI 兼容端点，可覆盖 ChatGPT / DeepSeek / Kimi / 智谱 / 千问）；
  `anthropic` → `ChatAnthropic`（Claude，需要时再装包）。
- 每个角色一份 `ModelSpec`（`vendor`、`model`、`base_url`、`api_key_env`、`temperature`），
  统一写在 `AGI_Agent/config.toml`。**换供应商或换模型 = 改 `config.toml`，代码零改动**（已实测）。
- API Key 只以**环境变量名**的形式出现在 `config.toml`；真实密钥放在 `.env` 或系统环境变量。
- `Role` 定义在 `schemas/common.py`，由 `providers` 反向依赖——**角色是领域概念，不是供应商细节**。

## 被否决的方案

- **每个节点直接构造 `ChatOpenAI(...)`**：换供应商要改多处代码，且节点与供应商强耦合。
- **自造一套模型协议/抽象层**：`LangChain Core` 的 `BaseChatModel` 已是事实标准，
  重造只会增加维护面，且未来接 MCP、Langfuse 时反而要写更多适配。
- **用 `AGI_LLM__<角色>__MODEL` 这类嵌套环境变量做局部覆盖**：**实测不可用**——
  pydantic-settings 对嵌套模型是**整体替换**而非逐字段合并，只给一个字段会因其余字段缺失而校验失败。
  环境变量只用于**完整覆盖**或提供密钥。

## 后果

- 换供应商只改 `config.toml`。但**目标是低成本，不是无感**：框架层能抹平的差异（端点、鉴权）
  已经抹平；抹不平的差异（结构化输出、工具调用）见 ADR 0003，换供应商仍需实测。
- `config.toml` 在进程导入时读取**一次**，修改后需要重启进程。
- 密钥最终要迁到服务端密钥库。届时只需改变 `ModelSpec.api_key_env` 的解析来源，
  角色与模型配置完全不受影响。
