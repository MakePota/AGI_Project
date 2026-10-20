package com.agiproject.server.agent;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;
import java.util.Map;

/**
 * 与 {@code AGI_Agent} 之间的跨服务契约。
 *
 * <p><strong>刻意窄。</strong> 这里只映射 Agent 对外承诺的三个形状
 * （任务状态 / 事件 / 审批请求），**不映射它内部的 Plan、ToolCall、Review**。
 * 理由见 ADR 0015：跨语言共享数据模型有序列化语义陷阱（Python 会省略有默认值的
 * 字段，Java 会把它序列化成 null），而且内部模型每加一个字段都要考虑对端兼容。
 *
 * <p>所有字段都显式标注 {@code @JsonProperty} 而不是靠全局命名策略：
 * Python 侧是 snake_case，Java 侧是 camelCase，这是最容易出错的接缝。
 * 写在字段上，改契约时一眼就能看到对应关系，不必去猜某处全局配置是怎么设的。
 *
 * <p>{@code AgentEvent.payload} 用 {@code Map} 接，**不解析成具体类型**——
 * 对服务端来说它是不透明的：只负责转发（见 ADR 0015）。
 */
public final class AgentContract {

    private AgentContract() {
    }

    /**
     * 一个任务的当前状态，也是 Agent 对外的唯一"事实来源"。
     *
     * <p>{@code approval} 非空就表示"正在等你批准"。客户端不需要记住
     * "我是不是漏了一个事件"：任何时刻重新拉一次这个对象就知道该做什么。
     *
     * <p>时间字段用 {@code String} 而不是 {@code Instant}：让框架去解析
     * 会给日期格式的误配留下空间（时区、纳秒位数都可能不一致），
     * 这里显式解析，格式不对会立刻抛出且错误信息清楚。
     */
    public record AgentTask(
            @JsonProperty("task_id") String taskId,
            String status,
            String goal,
            @JsonProperty("created_at") String createdAt,
            @JsonProperty("updated_at") String updatedAt,
            String result,
            String error,
            AgentApproval approval,
            @JsonProperty("artifacts") List<AgentArtifact> artifacts
    ) {
    }

    /**
     * 一个已经落盘的产物（当前只有图片）。
     *
     * <p><b>只有名字，没有路径。</b> 文件位置由 Agent 的产物目录 + name 决定；
     * 对外暴露绝对路径既没必要，也会把本机目录结构带进数据库和前端。
     * {@code kind} 现在固定是 {@code image}，留着是为了将来加文件/音频时不用改契约。
     */
    public record AgentArtifact(
            String name,
            String mime,
            long size,
            String kind
    ) {
    }

    /**
     * 一条执行事件。
     *
     * <p>{@code seq} 从 0 单调递增，是"断线重连不丢不重"的依据：
     * 客户端带上 {@code since=seq} 就能精确续上。
     */
    public record AgentEvent(
            long seq,
            String type,
            String at,
            Map<String, Object> payload
    ) {
    }

    /** 等待人工批准的请求。{@code summary} 与 CLI 里打印的审批摘要是同一份文本。 */
    public record AgentApproval(String summary, List<AgentApprovalStep> steps) {
    }

    /**
     * 一张已上传图片的位置与信息。
     *
     * <p>{@code path} 是**智能体那一侧的本机路径**。本服务拿到它之后会写进任务目标文本，
     * 由 planner 抄进图像工具的参数——图片**不经过任何外部存储**（见 ADR 0017）。
     * 服务端不需要、也不应该去理解这个路径指向哪里。
     */
    public record UploadResult(String path, String mime, long size, String name) {
    }

    /**
     * 计划里的一步。
     *
     * <p>{@code arguments} 必须在这里：审批看的应该是**真正会被执行的东西**，
     * 而不是模型写的描述。描述可以写错，参数不会——参数就是要发给工具的东西。
     */
    public record AgentApprovalStep(
            int index,
            String description,
            String tool,
            Map<String, Object> arguments,
            String risk
    ) {
    }
}
