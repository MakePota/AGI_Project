# 功能：图像理解工具（千问）

- 状态：已完成（2026-10-13）。**端到端实测通过**：让 Agent 读一张写着数字的图，
  最终回复与图中数字一致；Key 缺失时正确停下；只读任务未受影响。
- 涉及文件：`src/agi_agent/tools/builtin/vision.py`、
  `src/agi_agent/config.toml`（`[llm.vision_quick]` / `[llm.vision_deep]`）、
  `src/agi_agent/schemas/common.py`（`Role.VISION_*`）、
  `tests/unit/test_vision_tools.py`
- 相关决策：[0012 图像理解走千问，档位由工具与配置决定](../decisions/0012-vision-via-dashscope.md)（主）、
  [0006 外部能力接入策略](../decisions/0006-external-capability-policy.md)、
  [0007 风险由 policies 赋值](../decisions/0007-tool-registry-signature-and-risk.md)、
  [0011 前提条件不满足时停下](../decisions/0011-halt-on-unmet-precondition.md)、
  [0002 模型层可插拔](../decisions/0002-pluginable-llm.md)

## 要解决什么问题

让 Agent 能「看懂图片」——用户丢来一个图片路径问「这里面是什么」，它可以真的读到内容。
用主力模型做不到：DeepSeek 不支持图像输入，而本项目也不想为此把整条链路换掉。

## 图片是怎么进来的

工具有两种用法，**进来的路径只有一条**：

```
界面选图 → POST /api/uploads（Server 转发）→ Agent 落盘到 uploads/ → 得到本机路径
        → 路径写进任务目标文本 → planner 交给 vision.* 工具
```

`POST /uploads` 的校验与工具**共用同一套规则**（`agi_agent/images.py`）：
文件头嗅探 + 体积上限。两处各写一套的话，会出现"上传时接受了、调用时却被拒"
这种自相矛盾的行为。图片不经过任何外部存储——理由见
[ADR 0017](../decisions/0017-image-upload-without-object-storage.md)。

## 最终怎么做（人能读懂，不贴代码）

两个工具，各自绑定一个模型档位；档位是「工具名 → 槽位 → 模型」两级静态绑定，
模型在计划里只能选工具名，**填不了档位**：

| 工具 | 槽位 | 模型 | 思考模式 | 用途 |
| --- | --- | --- | --- | --- |
| `vision.describe_image` | `Role.VISION_QUICK` | `qwen3.8-flash` | 关 | 看图说话、OCR、简单识别 |
| `vision.analyze_image` | `Role.VISION_DEEP` | `qwen3.8-max` | 开 | 图表、图片比对、需要推理的问题 |

关键机制：

- **两个都是 `WRITE` 风险**，所以任何含读图的计划都会在 `authorize` 停下来等人确认。
  确认时**摘要在描述之外还显示参数**，因此能看清要把**哪个文件**发出去。
- **图片先过三道本地检查**：沙箱（复用 `files.resolve_readable_path`，路径越界与凭据黑名单
  一并生效）→ 体积上限（5 MB，本项目自保值，非厂商限制）→ 文件头嗅探
  （PNG/JPEG/GIF/WEBP，**看文件头不看扩展名**）。
- **图片以 base64 data URL 内联发出**，与 OpenAI 兼容端点对齐。
- **失败按「谁来修」分类**（同 ADR 0011）：本地问题 → `ToolError`（planner 可重规划）；
  鉴权、网络、配额、模型 ID 写错 → `PreconditionError`（**停下问人**）。
  **读图永不产生「结果不确定」**——它没有副作用。

## 为什么这么做 / 被否决的方案

完整论证见 [ADR 0012](../decisions/0012-vision-via-dashscope.md)，这里只留结论：

- **为什么两个工具而不是一个带 `tier` 参数的工具**：后者等于让模型自报该用哪个模型，
  与用户要求及 ADR 0007 的原则冲突。
- **为什么归 `WRITE`**：图片会上传到阿里云，属于数据外发，必须有人过目。
- **为什么给 `ModelSpec` 加 `extra_body`**：思考模式默认开启且实测差 14 倍 token，
  必须能按档位关掉；这个参数不属于任何 OpenAI 标准字段，只能从配置下放。
- **被否决**：让千问接管文本角色；新造一套档位枚举；在工具里硬编码模型名；
  改走 DashScope 原生 multimodal 端点（兼容端点实测够用）。

## 已知局限与坑

- **planner 可能对一个简单问题选贵的档位。** 实测：问「图里写的是几」，它选了
  `analyze_image`（Max）。接受这个折中——档位不可由模型指定，但**选哪个工具是它的判断**。
- **几个分支尚未实测**：HTTP 400（例如模型不吃图像）与 429（配额）的分类是保守推断，
  代码里已就地对未实测部分标注。将来真撞上，按实况修正 `_classify_provider_error`。
- **图片必须落在沙箱内**（默认是项目根）。放在别处的图片会被沙箱拒绝——
  这不是 bug，是既定的安全边界；要放开就改 `AGI_ALLOWED_ROOTS`。
- **只支持四种格式**（PNG/JPEG/GIF/WEBP）。BMP、HEIC、TIFF 会被拒，报错里会说明原因。
- **一次只能看一张图**，不支持多图比对（`analyze_image` 的问题里可以描述"对比"，但
  它只收到一张图）。
- **模型读图的结果是概率性的**：同一张图不同次调用可能有措辞差异。
  所以真实调用不进自动测试，只人工验证。

## 要改它先看哪里

| 想改什么 | 去哪 |
| --- | --- |
| 换模型、开关思考模式、换端点 | `config.toml` 的 `[llm.vision_quick]` / `[llm.vision_deep]`，**代码零改动** |
| 改风险等级（比如嫌确认太频繁想改回只读） | `vision.py` 里两处 `risk=RiskLevel.WRITE` |
| 改体积上限、支持的图片格式 | `vision.py` 的 `_MAX_IMAGE_BYTES` 与 `_sniff_mime` |
| 改错误分类 | `vision.py` 的 `_classify_provider_error`（含未实测分支的标注） |
| 改提示词 | `vision.py` 的 `_DESCRIBE_INSTRUCTION` 与 `analyze_image` 里的指令 |
| 沙箱规则 | **不在本文件**，在 `tools/builtin/files.py`（本功能刻意复用，不要另写一套） |
| 审批摘要的格式 | `policies/risk.py` 的 `_render_arguments` / `approval_summary`（所有工具共用） |
