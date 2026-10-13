# AGI Server

AGI Project 的业务服务：**用户与任务数据、对前端的 API**，并通过 HTTP 调用 `AGI_Agent`
完成智能体能力。用 Java 21 + Spring Boot 4 + MySQL。

## 它不做什么

- **不做智能体推理。** 计划、工具编排、审批判定、执行状态全在 `AGI_Agent`（ADR 0014）。
- **不做认证、不做界面。** 认证属于 Server（**本期未做**），界面属于 `AGI_Client`。

一句话：它是**薄转发层 + 任务镜像**。

## 跑起来

```bash
# 1) 数据库（Docker，绑 127.0.0.1:3307）
cp AGI_Server/.env.example AGI_Server/.env     # 首次：填一个本地开发口令
docker compose -f AGI_Server/docker-compose.yml up -d

# 2) 智能体引擎（另一个终端，必须先起——Server 要调它）
cd AGI_Agent && uv run agi-agent-api           # 监听 127.0.0.1:8123

# 3) 本服务
cd AGI_Server && mvn spring-boot:run           # 监听 127.0.0.1:8090
```

**停止**：`Ctrl+C` 之后要确认 JVM 真的退了——`mvn spring-boot:run` 会 fork 一个子进程，
只停掉 mvn 外壳的话，那个 JVM 会继续占着端口，下次启动就报 `Port 8090 was already in use`。
查一下 `netstat -ano | grep :8090`，有残留就 `taskkill /F /PID <pid>`。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/tasks` | 建任务，返回 **202** 与任务对象 |
| `GET` | `/api/tasks` | 任务列表（最近在前） |
| `GET` | `/api/tasks/{id}` | 任务详情；**未结束的会先向 Agent 对齐一次** |
| `GET` | `/api/tasks/{id}/events?since=` | SSE 事件流（薄转发；`since` 用于断线续传） |
| `POST` | `/api/tasks/{id}/approval` | 提交人工审批决定 |

**响应形状与 `AGI_Agent` 的完全一致**（字段名都是 snake_case）。这不是巧合：
本服务是薄转发层，同形意味着同一套脚本能验两个服务。

## 代码结构

```
src/main/java/com/agiproject/server/
├── ServerApplication.java      入口
├── config/                     WebClient（含"不设读超时"的解释）、SSE 心跳线程池、配置绑定
├── agent/                      与 Agent 的跨服务契约 + 客户端（HTTP 状态码 → 本服务的异常）
├── task/                       实体 / 仓库 / 服务（镜像逻辑与"按需对齐"都在这里）
└── web/                        控制器（含 SSE）+ 统一异常处理
src/main/resources/
├── application.yml             含多处"为什么这么设"的解释
└── db/migration/V1__init.sql   表结构（Flyway）
```

## 踩过的坑（都已写进对应文件的注释）

| 坑 | 症状 | 原因 |
| --- | --- | --- |
| **Boot 4 把自动配置拆成了独立模块** | 构建和启动都成功，但迁移一条都不执行，直到 Hibernate 校验时才报 `missing table` | 只加 `flyway-core` 不够，必须用 `spring-boot-starter-flyway` |
| **`data:` 后面那个空格是可选的** | 换一个 SSE 实现后事件一条都收不到 | FastAPI 写 `data: {...}`，Spring 的 `SseEmitter` 写 `data:{...}`，两者都合法 |
| **Spring Boot 默认绑 `0.0.0.0`** | 一个没有认证的服务对整个局域网开放 | 必须显式 `server.address: 127.0.0.1` |
| **给上游的 SSE 加读超时** | 停着等人批准的任务会被莫名掐断 | 空闲不等于故障；超时只能给连接阶段 |

## 测试

```bash
cd AGI_Server && mvn test          # 10 个单测：不启 Spring、不连库、不联网
cd AGI_Agent && uv run python scripts/smoke_api.py --base-url http://127.0.0.1:8090/api
```

第二个是**端到端**验证：建任务 → 轮询到"等待批准" → 提交审批 → 跑完，
再核对 404/409 与事件序列。它与验证 Agent 用的是同一个脚本。

## 要改它先看哪里

| 想改什么 | 去哪 |
| --- | --- |
| 端口、数据库连接、Agent 地址与超时 | `src/main/resources/application.yml` |
| 表结构 | 新增 `src/main/resources/db/migration/V2__*.sql`，**不要改 V1**（Flyway 会校验校验和） |
| 与 Agent 的字段映射 | `agent/AgentContract.java`（跨语言接缝，改这里要想清楚对端） |
| HTTP 状态码、错误响应形状 | `web/GlobalExceptionHandler.java` |
| "什么时候去问 Agent" | `task/TaskService.java`（**唯一**允许决定这件事的地方） |
| SSE 的转发与心跳 | `web/TaskController.java`、`config/AsyncConfig.java` |
