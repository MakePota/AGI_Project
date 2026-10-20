"""对外契约：`AGI_Server`（Java）与前端唯一需要认识的东西。

**只定义三个形状**，内部模型（`Plan` / `PlanStep` / `ToolCall` / `Review` / `Event`）
一律不外传。理由见 ADR 0015：跨语言共享数据模型有序列化语义陷阱——Python 会省略
有默认值的字段，Java 会把它序列化成 `null`，同一份 JSON 在两侧可能被理解成不同的意思，
而且这种差异**不会报错**，只会在某个边界条件下出错。

**但词汇表（枚举）是共用的**，不另造一套：

- 共用枚举与"导出模型"是两件事。枚举只是字符串常量，没有默认值省略的问题。
- 另造一套平行的事件名，必然出现两套词汇各自漂移，然后没人知道该以哪套为准。

代价要写在明处：**改动这些枚举就是改动对外契约。** 尤其新增 `TaskStatus` /
`EventType` 取值时，Java 侧可能不认识它——这不是"改个枚举"，是改接口。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from agi_agent.schemas import EventType, RiskLevel, SchemaModel, TaskStatus, utc_now


class ApprovalStep(SchemaModel):
    """计划里的一步，供人审批时查看。

    `arguments` 必须在这里：审批看的应该是**真正会被执行的东西**，而不是模型写的描述。
    这条与 `policies/risk.py` 里审批摘要的取舍同源（那里也是描述与参数都给）。
    """

    index: int
    description: str
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    risk: RiskLevel


class ApprovalRequest(SchemaModel):
    """等待人工批准的请求。

    `summary` 与 CLI 里打印的审批摘要是**同一份文本**（`policies.approval_summary`），
    所以两个入口看到的措辞一致，不会出现"网页上说的和终端里不一样"。
    """

    summary: str
    steps: list[ApprovalStep] = Field(default_factory=list)


class ArtifactRef(SchemaModel):
    """一个已经落盘的产物，供客户端构造下载地址。

    **不含本机路径、也不含供应商的临时 URL。** 前者是 Agent 的内部事实，
    后者 24 小时就失效；客户端只需要 ``name``，通过 Server 的
    ``/api/tasks/{taskId}/artifacts/{name}`` 取文件。
    """

    name: str
    kind: str
    mime: str
    size: int


class TaskRef(SchemaModel):
    """一个任务的当前状态——**这是客户端唯一需要读的东西**。

    `approval` 非空就表示「正在等你批准」。客户端不需要记住"我是不是漏掉了一个
    SSE 事件"：任何时刻重新拉一次这个对象，就知道现在该做什么。这正是 ADR 0015
    把中断做成状态（而不是挂着的连接）想要的效果。
    """

    task_id: str
    status: TaskStatus
    goal: str
    created_at: datetime
    updated_at: datetime = Field(default_factory=utc_now)
    result: str | None = None
    error: str | None = None
    approval: ApprovalRequest | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)


class UploadResult(SchemaModel):
    """一张已上传图片的位置与基本信息。

    `path` 是**本机绝对路径（正斜杠）**，可以直接交给图像工具当参数。
    之所以用正斜杠：它会出现在任务目标文本里、由模型抄进工具参数，
    而反斜杠在文本与 JSON 里更容易被转义或吃掉；正斜杠在 Windows 上同样有效。
    """

    path: str
    mime: str
    size: int
    name: str


class TaskEvent(SchemaModel):
    """执行过程中的一条事件。

    `seq` 从 0 单调递增，供客户端增量拉取（`since=seq`）：断线重连后既不会漏，
    也不会重复消费。

    `payload` 对调用方是**不透明的 JSON**——Server 只转发与落库，不需要理解内容。
    这样内部事件怎么写都不会波及 Java 侧。
    """

    seq: int
    type: EventType
    at: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)
