# AGI Client

AGI Project 的界面：**下达任务、看执行进度、对需要确认的动作做人工审批**。
用 uni-app（Vue 3）编写，一套代码可编译到 H5、小程序与 App——当前只跑 H5。

它不直接连智能体引擎：请求先到 `AGI_Server`，由后者转给 `AGI_Agent`（ADR 0014）。

## 跑起来

界面依赖后端，所以顺序是：**数据库 → 引擎 → 业务服务 → 前端**。

```bash
# 1) 数据库
docker compose -f AGI_Server/docker-compose.yml up -d

# 2) 智能体引擎（监听 8123）
cd AGI_Agent && uv run agi-agent-api

# 3) 业务服务（监听 8090）
cd AGI_Server && mvn spring-boot:run

# 4) 前端。两种方式二选一：
#    图形界面：HBuilderX 里打开本项目 → 运行 → 运行到浏览器
#    命令行（HBuilderX 需先在运行状态）：
cd /path/to/HBuilderX && ./cli.exe launch web --project AGI_Client
```

启动后 **HBuilderX 的控制台会打印实际地址**——本机实测是 `http://localhost:5173/`
（Vite 的默认端口；被占用时它会自动换一个，所以以控制台打印的为准）。

**后端地址配在 [`api/config.js`](api/config.js) 一处**。以后把 H5 构建产物交给
Spring Boot 一起发布（同源）时，把它改成空字符串即可。

## 现在有什么

一个页面（`pages/index/index.vue`），四块内容：

| 区域 | 作用 |
| --- | --- |
| 状态徽章 | 未开始 / 等你批准 / 执行中 / 已完成 / 已失败 / 需人工处理 |
| 输入区 | 输入任务目标、**选一张图**、提交 |
| **审批面板** | 出现"等你批准"时弹出，列出**每一步的描述、工具名与参数**，并提供批准 / 拒绝 |
| 执行过程 | 事件流，一行一条（收到 / 分诊 / 规划 / 待批准 / 调用中 / 调用完 / 审查通过 / 完成） |

### 图片是怎么走完这条链路的

```
选图（预览，不上传）
  → 点"发送"时上传 → POST /api/uploads（Server 转发）→ Agent 存进自己的 uploads/
  → 拿到本机路径 → 路径写进任务目标文本 → planner 交给 vision.* 工具 → 千问读图
```

图片**不经过任何外部存储**——读图工具读的是本机文件，没有必要先传出去
（为什么不需要对象存储，见 ADR 0017）。**上传发生在点"发送"那一刻**：
选了图又改主意不发，磁盘上不会多出文件。

### 三个值得知道的设计点

1. **审批面板显示参数，而不只是描述。** 描述是模型写的散文，参数才是真正会被执行的东西
   （要发给哪个工具、动哪个文件、买哪个游戏）。只显示描述，人批准的是模型的说法，
   而不是将要发生的事。这条与 CLI、与 `policies` 里的审批摘要是同一个立场。

2. **刷新页面不会丢任务。** 页面把 `task_id` 存在本地存储里，重新打开时会先问一次状态、
   再重新订阅事件流，于是**执行到一半刷新，审批请求仍然在**。
   这能成立是因为"等待批准"在服务端是一个**可查询的状态**（ADR 0015），
   而不是挂在某条连接上的东西——做成后者的话，一刷新就丢。

3. **不猜状态。** 界面上的状态只有一个来源：后端返回的 `status`。
   事件只用来渲染过程，不据此推断"应该是什么状态"。两个来源迟早会不一致。

## 已知限制

- **只支持 H5。** uni-app 的 App 端**没有 `EventSource`**，收流式事件需要接原生插件
  或 renderjs 桥接（ADR 0016 记了这笔代价）。在非 H5 平台上，页面会给出一句明确提示，
  而不是静默地什么都不发生。**没有写假的轮询兜底**——没验证过的代码比明确不支持更糟。
- **没有登录。** 后端因此只绑 `127.0.0.1`，且 CORS 只放行本机来源
  （见 `AGI_Server` 的 `WebCorsConfig`）。**不要在加了认证之前把它暴露到局域网。**
- **任务串行执行。** 引擎侧的 SQLite checkpointer 不支持并发（ADR 0004），
  所以上一个任务跑完下一个才轮到。
- **一张图一个任务**，且"图在哪"是靠**任务文本**传给模型的（见 ADR 0017 的已知折中）。
  实测通过，但不是硬保证。**什么现象说明它出问题了**：图片明明上传成功、审批里也显示了路径，
  但图像工具报"文件不存在"——那说明模型把路径抄错了。出现这种报错时，
  就该给 `Task` 加附件字段（把路径改成结构化传递），而不是继续调提示词。
- **上传的图片不会被自动清理**，会一直留在 `AGI_Agent/uploads/` 里。

## 测试

```bash
cd AGI_Agent && uv run python scripts/ui_smoke.py
```

它用真实浏览器（Playwright）把上面的流程点一遍，包括"刷新后审批仍在"。
四个服务都起来之后才能跑。

## 要改它先看哪里

| 想改什么 | 去哪 |
| --- | --- |
| 后端地址 | `api/config.js`（唯一一处） |
| 接口调用、事件订阅 | `api/tasks.js` |
| 页面结构、样式、事件文案 | `pages/index/index.vue` |
| 页面标题、路由 | `pages.json` |
| App 名称、图标、appid | `manifest.json`（打包安卓时才需要填 appid） |

## 文档

| 想了解 | 去哪 |
| --- | --- |
| 整个仓库怎么跑起来、文档都在哪 | 仓库根目录的 [`README.md`](../README.md) |
| 项目当前做到哪一步 | 仓库根目录的 `Memory.md` |
| 为什么分成三层、为什么前端不直连引擎 | [ADR 0014](../AGI_Agent/docs/decisions/0014-split-server-and-agent.md) |
| 为什么"刷新不丢任务"能成立 | [ADR 0015](../AGI_Agent/docs/decisions/0015-interrupt-as-queryable-state.md) |
| 为什么用 uni-app、为什么只做 H5 | [ADR 0016](../AGI_Agent/docs/decisions/0016-server-stack-selection.md) |
| 图片为什么不走对象存储 | [ADR 0017](../AGI_Agent/docs/decisions/0017-image-upload-without-object-storage.md) |

**本文件同时充当本项目的架构说明**：这一层**需要人手写和维护的只有三个文件**
（`api/config.js` 配置 / `api/tasks.js` 接口 / `pages/index/index.vue` 页面），
其余是 HBuilderX 生成的脚手架（`App.vue`、`main.js`、`manifest.json`、`uni.scss` …），
基本不用动。上面的「现在有什么」与「三个设计点」已经把这一层讲完了，
再单开一份架构文档只会重复。

