package com.agiproject.server.task;

import java.util.Set;

/**
 * 任务状态的词汇表，以及"是否已结束"的判断。
 *
 * <p><b>为什么状态在实体里是 {@code String} 而不是 Java 枚举。</b>
 * 状态是 {@code AGI_Agent} 的，不是本服务的。写成枚举就意味着：Agent 那边新增一个
 * 取值时，这里的反序列化会**直接失败**（或者被迫塞一个语义不明的兜底值），
 * 于是"对方加了个状态"变成了"本服务挂了"。
 *
 * <p>而 {@link #isTerminal} 只关心一个很窄的问题："还要不要再去问 Agent"。
 * 不认识的状态一律当作**未结束**——保守方向是对的：多问一次没有代价，
 * 而把进行中的任务误判成结束，用户就会看到一个永远停在中间的任务。
 */
public final class TaskStatuses {

    private TaskStatuses() {
    }

    /** 不再变化的状态。取值与 Agent 侧的 {@code TaskStatus} 对齐。 */
    public static final Set<String> TERMINAL = Set.of(
            "completed",
            "failed",
            "cancelled",
            "needs_human"
    );

    /** 正在等人批准——前端据此弹出审批面板。 */
    public static final String AWAITING_APPROVAL = "awaiting_approval";

    public static boolean isTerminal(String status) {
        return status != null && TERMINAL.contains(status);
    }
}
