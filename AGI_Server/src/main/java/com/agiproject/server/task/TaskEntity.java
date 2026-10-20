package com.agiproject.server.task;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.Instant;

/**
 * 任务表。
 *
 * <p><b>它是镜像，不是权威。</b> 执行状态的权威在 Agent 的 checkpointer 里，
 * 这张表存的只是"用户可见的那部分"：目标、状态、结果。所以：
 *
 * <ul>
 *   <li>{@code id} 用 Agent 生成的 taskId，本地**不重新生成**——两边各有一套 id
 *       就等于对不上账；</li>
 *   <li>{@code status} / {@code result} / {@code error} 只能由"抄 Agent 的答复"
 *       来更新，**本地绝不自己推断**（ADR 0014）。</li>
 * </ul>
 *
 * <p>没有无参构造函数以外的构造方式给 JPA 用，但也不需要：实体只在服务层构造。
 */
@Entity
@Table(name = "task")
public class TaskEntity {

    @Id
    @Column(name = "id", length = 64, nullable = false)
    private String id;

    /**
     * 所属用户。当前没有登录，写入的是一个固定的本地用户标识。
     *
     * <p>这一列现在就加，是为了以后真做登录时只需要"换个值"，
     * 而不必给存量数据做迁移、也不必改表结构。
     */
    @Column(name = "user_id", length = 64, nullable = false)
    private String userId;

    @Column(name = "goal", length = 2000, nullable = false)
    private String goal;

    @Column(name = "status", length = 32, nullable = false)
    private String status;

    @Column(name = "result", columnDefinition = "text")
    private String result;

    @Column(name = "error", columnDefinition = "text")
    private String error;

    /**
     * 产物清单的 JSON 数组。**存字符串而不是实体映射**：产物是 Agent 的事实，
     * 服务端只需要原样抄写与读回，不需要按字段查询它们。
     */
    @Column(name = "artifacts", columnDefinition = "text")
    private String artifacts;

    /** 取自 Agent 的创建时间——保持两边一致，不用本地时钟自己造一个。 */
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    /** 取自 Agent 的更新时间，即"这条镜像最后一次与 Agent 对齐"的时刻。 */
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    /** 给 JPA 用；业务代码请用带参数的那个。 */
    protected TaskEntity() {
    }

    public TaskEntity(String id, String userId, String goal, String status,
                      Instant createdAt, Instant updatedAt) {
        this.id = id;
        this.userId = userId;
        this.goal = goal;
        this.status = status;
        this.createdAt = createdAt;
        this.updatedAt = updatedAt;
    }

    public String getId() {
        return id;
    }

    public String getUserId() {
        return userId;
    }

    public String getGoal() {
        return goal;
    }

    public String getStatus() {
        return status;
    }

    public void setStatus(String status) {
        this.status = status;
    }

    public String getResult() {
        return result;
    }

    public void setResult(String result) {
        this.result = result;
    }

    public String getError() {
        return error;
    }

    public void setError(String error) {
        this.error = error;
    }

    public String getArtifacts() {
        return artifacts;
    }

    public void setArtifacts(String artifacts) {
        this.artifacts = artifacts;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Instant getUpdatedAt() {
        return updatedAt;
    }

    public void setUpdatedAt(Instant updatedAt) {
        this.updatedAt = updatedAt;
    }
}
