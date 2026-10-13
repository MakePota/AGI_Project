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

## 开发

```bash
cd AGI_Agent && uv run pytest          # 161 个单测，不消耗任何 API 额度
cd AGI_Agent && uv run ruff check .    # 静态检查
cd AGI_Server && mvn test              # 10 个 Java 单测，不连库不联网
```

**端到端**（需要四个服务都跑着）：

```bash
cd AGI_Agent
uv run python scripts/smoke_api.py --base-url http://127.0.0.1:8090/api   # 接口层
uv run python scripts/ui_smoke.py                                         # 界面层（真实浏览器）
```

`ui_smoke.py` 会真的开一个浏览器把流程点一遍，包括"发一张画着随机数字的图、
看模型能不能读出来"——只有真的看见了才答得对。
