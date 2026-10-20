# 功能：图片生成（千问 Qwen-Image-3.0）

- 状态：已完成（2026-10-20）。**真实端到端实测通过**：T2I、I2I（1 张与 3 张参考图）、
  Agent API、Server API、H5 界面；停掉 Agent 后 Server 仍能从本地缓存取图。
- 涉及文件：`src/agi_agent/tools/builtin/image.py`、`src/agi_agent/artifacts.py`、
  `src/agi_agent/tools/result.py`、`src/agi_agent/schemas/artifact.py`、
  `src/agi_agent/graph/nodes/{executor,reviewer,respond}.py`、
  `src/agi_agent/api/{contract,runner,app}.py`、`config.toml`（`[llm.image_generation]`）
- 相关决策：[0018 图片生成走千问，产物以 artifact 传递](../decisions/0018-image-generation-with-artifacts.md)（主）、
  [0012 图像理解走千问](../decisions/0012-vision-via-dashscope.md)、
  [0006 外部能力接入策略](../decisions/0006-external-capability-policy.md)、
  [0007 风险由 policies 赋值](../decisions/0007-tool-registry-signature-and-risk.md)、
  [0010 结果不确定不重试](../decisions/0010-no-retry-on-uncertain-write.md)、
  [0011 前提条件不满足时停下](../decisions/0011-halt-on-unmet-precondition.md)、
  [0017 图片不走对象存储](../decisions/0017-image-upload-without-object-storage.md)

## 要解决什么问题

让 Agent 能按文字描述生成图片，并支持用本机已有图片做图生图/编辑。难点不在调 API，
而在两件事：

1. 用户得**真的在界面上看到图**——模型返回的临时 URL 24 小时就失效，不能直接给前端；
2. 生成请求一旦发出就可能已经计费，失败时**绝不能自动重试**，否则账单会叠加。

## 最终怎么做（人能读懂，不贴代码）

一个工具，有没有参考图决定走哪条路：

| 参数 | 含义 |
| --- | --- |
| `prompt` | 图片内容描述（必填） |
| `reference_images` | 本机路径，0 张 = 文生图，1-3 张 = 图生图/编辑 |
| `size` | 默认 `1024x1024`，也接受 `auto` / `1024*1024` |
| `negative_prompt` | 不希望出现的内容 |
| `seed` | 随机种子 |

模型由配置静态绑定：`Role.IMAGE_GENERATION -> [llm.image_generation] -> qwen-image-3.0-pro`。
计划里只能选工具名，填不了模型；**这个槽位不能走 `get_model`**（它只会造聊天模型）。

完整链路：

```text
界面/CLI/API 下达任务
  → planner 选中 image.generate（WRITE，需要审批）
  → 审批面板显示 prompt / size / 参考图路径
  → 调 DashScope 原生同步端点
  → 拿到临时 URL，立刻下载到 AGI_Agent/artifacts/
  → ToolResult(text, artifacts)
  → executor 拆包：result 保持 str，artifacts 进 ToolCall/Task
  → TaskRef.artifacts
  → Server 抄进 task.artifacts（V2 迁移）并把文件缓存到 AGI_Server/artifacts/{taskId}/
  → Client 在终态 refresh 后渲染 <image>
```

关键机制：

- **风险 `WRITE`**：写文件、I2I 外发图片、产生费用，走整批审批中断。
- **提示词在审批摘要里放宽到 500 字符**：用户批准的是这段 prompt，截到 80 字符
  等于批准一个残缺请求。
- **失败按“请求是否发出”分界**：发出前 → `ToolError`/`PreconditionError`；
  发出后 → `UncertainOutcomeError`，停下问人，绝不重新生成。
- **下载只重试 GET**，不重试 POST；最终失败时错误信息里带临时 URL。
- **reviewer 能看到工具参数**：否则它无法判断“有没有真的用参考图”。

## 为什么这么做 / 被否决的方案

完整论证见 [ADR 0018](../decisions/0018-image-generation-with-artifacts.md)，这里只留结论：

- **为什么不用 OpenAI 兼容端点**：旧域名实测 404；原生同步端点可用。
- **为什么不接 MCP/Skill**：固定流程，按 ADR 0006 应手写窄工具；Skill 不是运行时能力。
- **为什么一个工具而不是两个**：差别只是有没有参考图；拆开会让 planner 丢参考图。
- **为什么 `n=1`**：审批、计费、产物数量、失败语义都只有一种形状。
- **为什么 Server 要缓存文件**：终态任务不再向 Agent 对齐；不缓存的话 Agent 一停，
  刷新页面就看不到已生成的图。
- **为什么界面不在 `tool_finished` 时提前渲染**：Server 的产物元数据要等任务结算后
  才落库，提前请求会 404；`uni-image` 会把失败缓存住，终态后不会重试。

## 已知局限与坑

- **Pro 限流 1 RPM**：连续任务之间要等约 60 秒；工具不会静默 sleep。
- **reviewer 看不到画面**：它只能确认“工具成功落盘”；画面是否符合要求要人工看。
- **产物不会自动清理**：Agent 与 Server 的 `artifacts/` 都会增长。
- **planner 不能引用前一步的动态输出**：它可能规划出
  `path="<image.generate 返回的新图产物路径>"` 这种占位符；该步骤必然失败。
- **`size=auto` 可能落到 2K 档**，费用翻倍；默认固定 1024x1024。
- **3 张满额参考图的请求体上限未实测**：代码设了 15 MB 总上限。
- **`httpx.URL.host` 不是 `.hostname`**：下载阶段的这个错误曾让 planner 准备重复生成；
  现在 `generate_image` 对“请求发出后的任何未知异常”统一兜底为
  `UncertainOutcomeError`。改下载相关代码时不要拆掉这层兜底。

## 要改它先看哪里

| 想改什么 | 去哪 |
| --- | --- |
| 换模型、换端点、换 Key | `config.toml` 的 `[llm.image_generation]`，**代码零改动** |
| 改风险等级（比如嫌确认太频繁） | `tools/builtin/image.py` 的 `register(ToolSpec(...))` |
| 改参数签名（尺寸、参考图数量） | `image.py` 的 `GenerateImageArgs` 与 `_normalize_size` |
| 改请求体形状 | `image.py` 的 `_build_payload`（原生协议的形状都在这里） |
| 改错误分类 | `image.py` 的 `_classify_http_error` 与 `_post_generation` |
| 改下载重试、体积上限 | `image.py` 的 `_download_image`、`artifacts.py` 的 `MAX_ARTIFACT_BYTES` |
| 改产物存储/命名/路径校验 | `artifacts.py`（Agent）与 `ArtifactService.java`（Server） |
| 改界面显示 | `AGI_Client/pages/index/index.vue` 的 `.result` 区与 `artifactSrc` |
| 改审批摘要里 prompt 的截断长度 | `policies/risk.py` 的 `_LONG_VALUE_LIMIT`；界面同步改 `argsText` |
| 改 reviewer 看到的参数 | `graph/nodes/reviewer.py` 的 `_render`（复用 `policies.risk.render_arguments`） |
