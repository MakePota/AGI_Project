# AGI Project

一个长期演进的个人 AI 智能体应用——不是一次性 Demo。目标是让 Agent 能听懂自然语言任务、
制定计划、选择工具、分步执行、处理失败、**在涉及副作用时请你确认**，并把结果报告回来。

目前它真的能做的事：在 Steam 上查价 / 搜索 / 增删购物车、读本地文件、**看懂一张图片**，
以及一个能在浏览器里用的界面——输入任务、看执行过程、对需要确认的动作做审批。

## 三个部分

```text
AGI_Client  ──→  AGI_Server  ──→  AGI_Agent  ──→  Models / MCP Servers / Local Tools
  界面             业务服务           智能体引擎            外部能力
 uni-app        Java + Spring Boot    Python + LangGraph    DeepSeek / 千问 / Playwright
```

| 子项目 | 职责 | **不**做什么 |
| --- | --- | --- |
| [`AGI_Client`](AGI_Client/) | 下达任务、展示执行过程、人工审批 | 不直接连引擎；不含业务规则 |
| [`AGI_Server`](AGI_Server/) | 用户与任务数据、对前端的 API | 不做智能体推理、不做认证（本期未做） |
| [`AGI_Agent`](AGI_Agent/) | 任务理解、计划、工具编排、执行状态、安全策略 | 不做认证、不提供客户端页面 |

分三层的理由与边界演变见 [ADR 0014](AGI_Agent/docs/decisions/0014-split-server-and-agent.md)。

## 跑起来

四样东西，缺一个界面就打不开。**顺序不能颠倒**：前端要有业务服务，业务服务要有引擎，
引擎和业务服务都要数据库。

```bash
# 1) 数据库（Docker，绑 127.0.0.1:3307）
cp AGI_Server/.env.example AGI_Server/.env      # 首次：填一个本地开发口令
docker compose -f AGI_Server/docker-compose.yml up -d

# 2) 智能体引擎（监听 127.0.0.1:8123）
cd AGI_Agent && uv run agi-agent-api

# 3) 业务服务（监听 127.0.0.1:8090）
cd AGI_Server && mvn spring-boot:run

# 4) 前端（默认 http://localhost:5173/）
#    图形界面：HBuilderX 里打开 AGI_Client → 运行 → 运行到浏览器
#    命令行（HBuilderX 需先处于运行状态）：
cd <HBuilderX 安装目录> && ./cli.exe launch web --project AGI_Client
```

然后打开 **http://localhost:5173/**：输入一句话，或选一张图，回车。

**几个容易踩的地方**（都实测过）：

- **端口**：8090 与 3307 都是刻意选的。8080 被别的服务占了，3306 留给别的项目的数据库。
- **停服务**：`mvn spring-boot:run` 会 fork 一个子进程，`Ctrl+C` 只停外壳，JVM 会继续占着端口，
  下次启动就报 "Port 8090 was already in use"。查 `netstat -ano | grep :8090`，
  清 `taskkill /F /PID <pid>`。
- **Maven 首次构建**要下上百 MB。`~/.m2/settings.xml` 已配阿里云镜像。

## 文档地图

四个门类，**每个知识只写在一处**——写两处必然有一处过期。

| 想了解 | 去哪 |
| --- | --- |
| **这是什么、做到哪一步、下一步做什么** | [`Memory.md`](Memory.md)（**唯一**的状态来源） |
| **在这个仓库里干活的硬约束** | [`CLAUDE.md`](CLAUDE.md) 与 `Memory.md` §3 |
| **启动命令与端口** | 上面的「跑起来」；细节在各自 README |
| 引擎架构、技术栈、目录职责 | [`AGI_Agent/docs/architecture.md`](AGI_Agent/docs/architecture.md) |
| 业务服务的分层与数据流 | [`AGI_Server/docs/architecture.md`](AGI_Server/docs/architecture.md) |
| 界面怎么组织的、有哪些已知限制 | [`AGI_Client/README.md`](AGI_Client/README.md) |
| **为什么这样选**（含被否决的方案） | [`AGI_Agent/docs/decisions/`](AGI_Agent/docs/decisions/README.md)（ADR） |
| 某个功能怎么运转、要改它先看哪里 | [`AGI_Agent/docs/features/`](AGI_Agent/docs/features/README.md) |

**一处容易困惑的地方**：ADR（`AGI_Agent/docs/decisions/`）虽然放在引擎目录下，但 0014 之后的
几篇其实是**整个项目级**的决策（服务拆分、中断语义、技术栈、图片上传）。
现在这样放是因为决策目录跟着最早的项目走；等 `AGI_Server`、`AGI_Client` 自己攒出
足够多的决策时，再考虑把项目级的提到根目录——**不要为了统一而统一**。

## 术语速查

文档里反复出现、但**没有别处解释**的词。这里只给一句话，完整的故事在链接里。

| 术语 | 一句话 | 细节在哪 |
| --- | --- | --- |
| **ADR** | 架构决策记录：记下"当时为什么这么选、否掉了什么"。编号从 0001 起，**不复用不重排** | [`AGI_Agent/docs/decisions/`](AGI_Agent/docs/decisions/README.md) |
| **SSE** | 服务端单向推事件的 HTTP 长连接（`text/event-stream`）。OpenAI 的流式接口用的就是它 | [ADR 0015](AGI_Agent/docs/decisions/0015-interrupt-as-queryable-state.md) |
| **中断 / interrupt** | 图执行到需要人确认的一步就**停下来**，把计划交给人；人答完再继续。本项目里它是一个**可查询的状态**，不是挂着的连接 | [ADR 0015](AGI_Agent/docs/decisions/0015-interrupt-as-queryable-state.md) |
| **checkpointer** | LangGraph 用来把"任务执行到哪了"存下来的组件。本项目用 SQLite，**不支持并发** | [ADR 0004](AGI_Agent/docs/decisions/0004-sqlite-checkpointer.md) |
| **LangGraph** | 把智能体的流程写成一个有状态、可中断、可从断点恢复的图 | [`architecture.md`](AGI_Agent/docs/architecture.md) |
| **分诊 / triage** | 图的第一步：判断这句话是闲聊还是任务，决定往哪条路走 | [功能文档](AGI_Agent/docs/features/supervisor-graph.md) |
| **`READ` / `WRITE` 风险** | 工具的风险等级。`READ` 自动放行；`WRITE` 会**停下来等你批准**。等级由代码赋值，不信模型自己报 | [ADR 0007](AGI_Agent/docs/decisions/0007-tool-registry-signature-and-risk.md) |
| **幂等** | 同一个动作做多少次结果都一样。加购物车是幂等的；**"发消息"这类动作天生不是**（没有"已经发过了"这种状态可查），只能靠"停下问人"兜底 | [ADR 0010](AGI_Agent/docs/decisions/0010-no-retry-on-uncertain-write.md) |
| **MCP** | 一套让 AI 应用接入外部工具的通用协议。本项目**有意还没接**（等真实的现成工具出现） | [ADR 0006](AGI_Agent/docs/decisions/0006-external-capability-policy.md) |
| **薄转发层** | `AGI_Server` 的定位：只管转发与存镜像，不做智能体推理 | [`AGI_Server/docs/architecture.md`](AGI_Server/docs/architecture.md) |
| **Playwright** | 浏览器自动化库。Steam 那些工具靠它驱动真实浏览器 | [ADR 0005](AGI_Agent/docs/decisions/0005-playwright-for-browser-automation.md) |
| **DashScope / 千问** | 阿里云的模型服务。本项目**只**用它做图像理解，文本推理仍走 DeepSeek | [ADR 0012](AGI_Agent/docs/decisions/0012-vision-via-dashscope.md) |

## 开发

```bash
cd AGI_Agent && uv run pytest          # 161 个单测，不消耗任何 API 额度
cd AGI_Agent && uv run ruff check .    # 静态检查
cd AGI_Server && mvn test              # 10 个 Java 单测，不连库不联网
cd AGI_Agent && uv run python scripts/check_docs.py   # 文档腐化检查，见下
```

**`check_docs.py` 是什么**：文档本身会烂——文件改名后链接失效（不会报错）、
加了测试忘了改数字、新增 ADR 忘了登记进索引。这个脚本把这些**能被机械验证**的部分
查一遍：链接、单测数、ADR 篇数、索引完整性。

**收工时跑一次**（规则见 `CLAUDE.md`）：`Memory.md` 是唯一的状态来源，
它一旦漏更新，下一个会话就会拿着旧状态开工，而且不会有任何东西报错。
写文档时也建议跑——它比人眼可靠。

**端到端**（需要四个服务都跑着）：

```bash
cd AGI_Agent
uv run python scripts/smoke_api.py --base-url http://127.0.0.1:8090/api   # 接口层
uv run python scripts/ui_smoke.py                                         # 界面层（真实浏览器）
```

`ui_smoke.py` 会真的开一个浏览器把流程点一遍，包括"发一张画着随机数字的图、
看模型能不能读出来"——只有真的看见了才答得对。
