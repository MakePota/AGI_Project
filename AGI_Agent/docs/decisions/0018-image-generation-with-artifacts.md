# 0018 图片生成走千问 Qwen-Image-3.0，产物以结构化 artifact 传递

- 日期：2026-10-20
- 状态：已采用

## 背景

用户要在现有三层应用里加入“生成图片”能力。需求里已经确定的四件事：

1. 同时支持文生图（T2I）与图生图/编辑（I2I，使用本机参考图）；
2. 默认模型 `qwen-image-3.0-pro`，但必须能只改配置就换模型；
3. 生成结果要在界面上**真的看到图片**，刷新页面也不能丢；
4. 允许真实 API 探针与端到端验收。

外部约束与调研结论：

- 千问图片生成有 OpenAI 兼容与 DashScope 原生两种同步端点。**实测（2026-10-20）：
  旧域名 `https://dashscope.aliyuncs.com` 的 `/compatible-mode/v1/images/generations`
  直接返回 404**；官方兼容示例全部使用业务空间专属域名
  （`{WorkspaceId}.cn-beijing.maas.aliyuncs.com`），而本机没有 WorkspaceId。
  原生同步端点 `POST /api/v1/services/aigc/multimodal-generation/generation`
  在旧域名上可用，T2I 与 I2I 均通过。
- 原生请求体是 `input.messages` + `parameters`；参考图是 `content` 里的
  `{"image": ...}` 块，放在文本块之前；`size` 用星号（`512*512`）。
- 响应里图片在 `output.choices[].message.content[].image`，格式 PNG，
  **URL 只有 24 小时有效**。
- 计费：Pro 1K 约 0.25 元/张、2K 约 0.5 元/张；Pro 官方限流 1 RPM。失败请求不计费。
- 仓库里已有成熟的“能力工具 + 配置槽位”范式：`vision.py` 与 ADR 0012。
  项目还没有 MCP Client 层（ADR 0006 有意推迟）。

## 决策

### 1. 一个工具 `image.generate`，T2I/I2I 由有没有参考图决定

参数签名：

```text
image.generate（WRITE）
  prompt: str
  reference_images: list[str] = []   # 0 张 = T2I；1-3 张 = I2I
  size: str = "1024x1024"            # 也接受 auto / 1024*1024
  negative_prompt: str | None = None
  seed: int | None = None
```

不拆成两个工具：两者的差别只是**有没有参考图**，拆开会让 planner 多一次无意义的选择，
而且它可能选文生图、把用户给的参考图丢掉。

### 2. 模型是静态槽位，不由模型自报

新增 `Role.IMAGE_GENERATION` → `[llm.image_generation]` → `qwen-image-3.0-pro`。
计划里只能选工具名，填不了模型或档位——与 ADR 0012 同一条原则。

**这个槽位不能交给 `get_model`**：它返回 `ChatOpenAI`，只会调 `/chat/completions`。
误调不会报“用错了”，只会得到一个看起来像网络问题的 404。只有
`tools/builtin/image.py` 直接读配置并走图片端点。

### 3. 走 DashScope 原生同步端点，不走 OpenAI 兼容端点

理由是实测：旧域名的兼容 `/images/generations` 是 404。原生端点在旧域名上可用，
请求体形状已在 ADR 与探针脚本里固定。配置里的 `base_url` 只写域名
（`https://dashscope.aliyuncs.com`），工具追加固定路径；换地域/业务空间域名时只改配置。

### 4. 同步生成 + 立即下载落盘

生成请求是同步的（实测 512x512 约 7-9 秒）。拿到 URL 后**立即下载**到
`AGI_Agent/artifacts/`：URL 24 小时失效，绝不能把它当作持久事实交给前端或数据库。

**不引入异步任务 + 轮询**：同步端点已经够用，多一套 task_id 状态机只增加失败面。

### 5. `n=1`、`prompt_extend=false`、`watermark=false`

- `n` 固定 1：一次调用一张图，审批、计费、产物数量、失败语义都只有一种形状。
  需要多张就让 planner 调多次，每次都有独立审计记录。
- `prompt_extend=false`：审批时用户看到的是原始 prompt，出图也应该按原始 prompt。
  改写会让人无法判断“这张图为什么和我说的话不一样”。
- `watermark=false`：不默认加水印。

### 6. 风险等级 `WRITE`

会写本地文件、I2I 会把本机图片字节上传到阿里云、会产生费用。走现有审批中断；
审批摘要对 `prompt` 放宽到 500 字符（其余参数仍 80），否则用户批准的是被截断的提示词。

### 7. 产物以结构化 artifact 传递，不从回复文本解析

链路：

```text
ToolResult -> ToolCall.artifacts -> Task.artifacts
           -> TaskRef.artifacts -> Server artifactsJson + 文件缓存 -> Client <image>
```

- `ToolCall.result` 仍然是 `str`（reviewer/respond/CLI/事件预览的共同假设）。
- `Artifact` 只存 `name/kind/mime/size`，不存绝对路径、不存上游 URL。
- 界面用 `name` 通过 Server 取文件，不从 `reply` 正则抠路径。

### 8. Server 既镜像元数据，也缓存文件

终态任务不再向 Agent 对齐（`TaskService.get` 直接读本地镜像），所以：

- `task.artifacts` 列（V2 迁移）持久化产物元数据；
- 任务进入终态时尽力把文件拉到 `AGI_Server/artifacts/{taskId}/`；
  失败只记日志，不阻塞元数据镜像；
- `ArtifactController` 提供 **task-scoped** 的
  `GET /api/tasks/{taskId}/artifacts/{name}`，先校验归属再下载/读缓存。

这样 Agent 停机后，已经完成的任务仍然能看到图。

### 9. 失败按“请求是否已经发出”分界，发出后绝不重新生成

| 阶段 | 分类 | 处理 |
| --- | --- | --- |
| 参数、参考图、缺 Key | `ToolError` / `PreconditionError` | 可重规划或停下修环境 |
| 连接失败 / DNS / 代理 | `PreconditionError` | 请求没到供应商 |
| 401/403/404/429、400 参数或审核 | `PreconditionError` / `ToolError` | 请求被拒绝，未计费 |
| 发送后读超时、5xx、200 无 URL | `UncertainOutcomeError` | 停下，人工核实 |
| 下载失败、落盘失败、下载阶段未知异常 | `UncertainOutcomeError` | 只重试 GET；最终失败带上临时 URL |

**429 不静默等待**：Pro 限流 1 RPM，工具里 sleep 60 秒会把整个任务卡住；
直接停下并提示用户稍后重发。

### 10. reviewer 必须看到工具参数

图生图第一次真实验收时，工具成功、`reference_images` 也确实传了，但 reviewer
只看结果文本，无法确认“这是图生图还是纯文生图”，于是把任务驳回。
现在 reviewer 的渲染包含每次调用的参数（复用 `policies.risk.render_arguments`）。

## 被否决的方案

- **接入 MCP**（`wchiway/qwen-image-mcp` 等）。功能对得上，但这是一个固定流程
  （一次 POST + 下载），按 ADR 0006 应手写窄工具；引入 MCP Client 要付子进程、
  协议握手、跨进程调试的代价，换不来能力。Claude Code 的 Skill 也不是运行时能力。
- **两个工具（T2I / I2I）或一个带 `tier` 参数的工具**。前者让 planner 多一次
  无意义选择；后者等于让模型自报档位，违反 ADR 0007/0012。
- **OpenAI 兼容端点**。旧域名实测 404；为它引入 WorkspaceId 配置不值得，
  原生端点已够用。
- **把 DashScope 的临时 URL 直接给前端/数据库**。24 小时失效，且把外部地址
  写进了本项目的持久状态。
- **Server 每次请求都回源 Agent，不落本地**。Agent 停机后已完成的图就看不到了；
  终态任务又不再对齐，等于丢结果。
- **引入对象存储**。ADR 0017 的触发条件（两个服务部署到不同机器）没有出现。
- **异步任务 + 轮询**。同步端点已够用；多一套状态机只增加失败面。
- **`n>1` / 批量生成**。会让审批、计费、产物数量和失败语义同时复杂化。
- **请求发出后自动重试**。一次生成可能已经计费，重试会在账单上叠加一次。
- **用 `get_model` 调图片生成**。它只会造聊天模型。
- **把 base64 或二进制塞进 `TaskRef` / MySQL**。会撑大事件流、数据库和页面；
  产物只传名字，文件走专门端点。
- **在 `tool_finished` 事件到达时就让界面渲染图片**。实测踩到：文件已经在 Agent
  磁盘上，但 Server 的产物归属元数据要等任务结算后才落库，图片 URL 返回 404；
  `uni-image` 组件会把这次失败缓存住，终态后同一个 URL 不会重试。现在只在
  `refresh()` 拿到权威的 `TaskRef.artifacts` 后渲染。

## 后果

### 实测事实（2026-10-20）

- 旧域名兼容端点 `/images/generations`：**HTTP 404，空响应**。
- 原生端点 T2I（512x512）：HTTP 200，约 7.0 秒，PNG 117,492 字节。
- 原生端点 I2I（1 张参考图）：HTTP 200，约 9.2 秒，PNG 179,550 字节。
- 原生端点 I2I（3 张参考图，数组）：HTTP 200，约 9.3 秒，PNG 127,800 字节。
- 非法 size：HTTP 400 `InvalidParameter`，文案写面积上限
  `6553600 (2560x2560)`——与文档的 2048x2048 不一致；工具按文档的保守值校验。
- 非法 model：HTTP 400 `InvalidParameter` + `Model not exist.`。
- 端到端：Agent API、Server API、H5 界面均跑通 T2I；Agent 与 Server 的
  `TaskRef.artifacts` 非空；停掉 Agent 后 Server 仍能从本地缓存取图。

### 真实踩到的两个缺陷（已修，保留记录）

1. **`httpx.URL` 的属性是 `.host`，不是 `.hostname`。** 下载阶段因此抛
   `AttributeError`。更严重的是它发生在生成成功之后，却被 executor 当成普通失败，
   planner 立即准备再次生成——会在账单上叠加一次。现在 `generate_image` 在
   `_post_generation` 返回后包了一层兜底：任何未知异常都翻译成
   `UncertainOutcomeError`，绝不退化成普通失败。
2. **reviewer 看不到工具参数**。图生图被误判成“无法确认是否图生图”而驳回。
   现在 reviewer 的渲染包含参数；权限审批摘要与 reviewer 共用同一份参数渲染。

### 代价与已知局限

- 生成是同步长请求：512x512 约 7-9 秒，2K 更久；Pro 限流 1 RPM，
  连续任务之间要等约 60 秒。
- reviewer 看不到图片内容，只能确认“工具成功落盘”；画面是否符合要求仍需人工看。
- 产物不会自动清理：`AGI_Agent/artifacts/` 与 `AGI_Server/artifacts/` 都会增长。
- **下载失败时，临时 URL 会出现在错误信息/任务结果里**（24 小时有效）。
  这是为了让用户仍能人工取回；它等同于一张图片的短期访问凭据，不是什么永久密钥，
  但不要把它当成可以随便贴到公开地方的普通链接。
- planner 不能引用前一步的动态输出。实测中它曾规划出
  `path="<image.generate 返回的新图产物路径>"` 这种占位符步骤；该步骤必然失败。
  这是 planner 的既有局限，不在本次范围内修复。
- `size=auto` 可能落到 2K 档，费用翻倍；默认值固定为 `1024x1024`。
- 3 张满额参考图（3 × 5 MB）的请求体供应商是否接受尚未实测；代码设了 15 MB 总上限。

### 什么时候应该重新审视

- 拿到 WorkspaceId / 专属域名，且兼容端点恢复：可以简化传输层，但本决策的
  槽位与产物链路仍然成立。
- 供应商支持 base64 直出（现在返回 URL）：可以少一次下载，但“立即落盘”不变。
- 真出现“一次要生成多张”的需求：再评估 `n>1`，不要为了预留而提前开放。
- 两个服务部署到不同机器：ADR 0017 的 OSS 触发条件成立，届时再换存储。
