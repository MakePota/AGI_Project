package com.agiproject.server.task;

import com.agiproject.server.agent.AgentClient;
import com.agiproject.server.agent.AgentContract;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.time.Instant;
import java.util.List;
import java.util.NoSuchElementException;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/**
 * 任务服务的行为。
 *
 * <p><b>不启动 Spring、不连数据库、不访问网络</b>：仓库与 Agent 客户端都是替身。
 * 这样测试跑起来是毫秒级的，也不需要一套可用的环境——真实链路（Spring 容器、
 * MySQL、Agent 服务）靠 `scripts/smoke_api.py` 端到端验证。
 *
 * <p>这里钉住的核心是一条设计约束：<b>服务端只抄 Agent 的状态，绝不自己推断</b>。
 * 它决定了几件具体行为——什么时候该去问上游、什么时候不该问、上游答什么就存什么。
 */
class TaskServiceTest {

    private TaskRepository repository;
    private AgentClient agentClient;
    private TaskService service;

    @BeforeEach
    void setUp() {
        repository = mock(TaskRepository.class);
        agentClient = mock(AgentClient.class);
        service = new TaskService(repository, agentClient);
    }

    private static AgentContract.AgentTask agentTask(String id, String status, String result) {
        return new AgentContract.AgentTask(
                id, status, "列出目录", "2026-10-13T10:00:00Z", "2026-10-13T10:00:05Z",
                result, null, null);
    }

    private static TaskEntity entity(String id, String status) {
        return new TaskEntity(id, TaskService.LOCAL_USER_ID, "列出目录", status,
                Instant.parse("2026-10-13T10:00:00Z"), Instant.parse("2026-10-13T10:00:00Z"));
    }

    @Test
    @DisplayName("建任务后把 Agent 的答复原样存下来，并且用 Agent 的 taskId 做主键")
    void createMirrorsTheAgentReply() {
        when(agentClient.createTask("列出目录")).thenReturn(agentTask("t-1", "received", null));

        AgentContract.AgentTask created = service.create("列出目录");

        ArgumentCaptor<TaskEntity> saved = ArgumentCaptor.forClass(TaskEntity.class);
        verify(repository).save(saved.capture());
        assertThat(saved.getValue().getId())
                .as("本地不重新生成 id：两边各有一套 id 就对不上账了")
                .isEqualTo("t-1");
        assertThat(saved.getValue().getStatus()).isEqualTo("received");
        assertThat(saved.getValue().getUserId()).isEqualTo(TaskService.LOCAL_USER_ID);
        assertThat(created.taskId()).isEqualTo("t-1");
    }

    @Test
    @DisplayName("查未结束的任务时会向 Agent 对齐一次——镜像是会过期的")
    void getRefreshesANonTerminalTask() {
        TaskEntity stored = entity("t-1", "executing");
        when(repository.findById("t-1")).thenReturn(Optional.of(stored));
        when(agentClient.getTask("t-1")).thenReturn(agentTask("t-1", "completed", "做完了"));

        AgentContract.AgentTask view = service.get("t-1");

        assertThat(view.status()).isEqualTo("completed");
        verify(agentClient).getTask("t-1");
        verify(repository).save(stored);
    }

    @Test
    @DisplayName("已结束的任务不再向上游发问——省一次没意义的调用")
    void getDoesNotAskTheAgentForAFinishedTask() {
        when(repository.findById("t-1")).thenReturn(Optional.of(entity("t-1", "completed")));

        AgentContract.AgentTask view = service.get("t-1");

        assertThat(view.status()).isEqualTo("completed");
        verifyNoInteractions(agentClient);
    }

    @Test
    @DisplayName("不认识的状态一律当作未结束——宁可多问一次，也不能把进行中的任务显示成已完成")
    void unknownStatusIsTreatedAsNotFinished() {
        // Agent 将来新增一个状态时，本服务不认识它。这时保守地去问一次，
        // 比自作主张地认为"结束了、不用管了"要安全得多。
        when(repository.findById("t-1")).thenReturn(Optional.of(entity("t-1", "some_new_status")));
        when(agentClient.getTask("t-1")).thenReturn(agentTask("t-1", "completed", "好了"));

        service.get("t-1");

        verify(agentClient).getTask("t-1");
    }

    @Test
    @DisplayName("提交审批会原样转发，并把结果抄回来")
    void decideForwardsTheDecisionAndApplies() {
        TaskEntity stored = entity("t-1", "awaiting_approval");
        when(repository.findById("t-1")).thenReturn(Optional.of(stored));
        when(agentClient.decideApproval("t-1", true, null))
                .thenReturn(agentTask("t-1", "completed", "已发送"));

        service.decide("t-1", true, null);

        verify(agentClient).decideApproval("t-1", true, null);
        assertThat(stored.getStatus()).isEqualTo("completed");
        assertThat(stored.getResult()).isEqualTo("已发送");
    }

    @Test
    @DisplayName("任务不存在时抛 NoSuchElementException，由异常处理器翻成 404")
    void unknownTaskIsReportedAsMissing() {
        when(repository.findById("nope")).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.get("nope"))
                .isInstanceOf(NoSuchElementException.class);
        verifyNoInteractions(agentClient);
    }

    @Test
    @DisplayName("列表里一条对齐失败，不该让整个列表打不开")
    void listSurvivesOneFailingRefresh() {
        TaskEntity ok = entity("t-ok", "executing");
        TaskEntity bad = entity("t-bad", "executing");
        when(repository.findByUserIdOrderByCreatedAtDesc(anyString()))
                .thenReturn(List.of(ok, bad));
        when(agentClient.getTask("t-ok")).thenReturn(agentTask("t-ok", "completed", "好了"));
        when(agentClient.getTask("t-bad")).thenThrow(new RuntimeException("Agent 掉线了"));

        List<AgentContract.AgentTask> tasks = service.list();

        // 关键：Agent 不可用时，用户至少还能看到本地已有的任务记录，
        // 而不是面对一个打不开的列表——后者会把一次局部故障放大成"整个应用坏了"。
        assertThat(tasks).hasSize(2);
        assertThat(tasks.getFirst().status()).isEqualTo("completed");
        assertThat(tasks.get(1).status()).isEqualTo("executing");
    }

    @Test
    @DisplayName("只有未结束的任务才会触发对齐——已结束的一条都不问")
    void listOnlyRefreshesUnfinishedTasks() {
        when(repository.findByUserIdOrderByCreatedAtDesc(anyString()))
                .thenReturn(List.of(entity("done", "completed"), entity("running", "executing")));
        when(agentClient.getTask("running")).thenReturn(agentTask("running", "completed", "好了"));

        service.list();

        verify(agentClient, times(1)).getTask("running");
        verify(agentClient, never()).getTask("done");
    }

    @Test
    @DisplayName("解析不了的时间戳不该让整个请求失败")
    void unparseableTimestampFallsBackInsteadOfFailing() {
        AgentContract.AgentTask weird = new AgentContract.AgentTask(
                "t-1", "received", "列出目录", "不是时间", null, null, null, null);
        when(agentClient.createTask(anyString())).thenReturn(weird);

        service.create("列出目录");

        ArgumentCaptor<TaskEntity> saved = ArgumentCaptor.forClass(TaskEntity.class);
        verify(repository).save(saved.capture());
        // 宁可时间不准（用兜底值），也不要因为一个格式问题让任务根本建不起来。
        assertThat(saved.getValue().getCreatedAt()).isNotNull();
    }

    @Test
    @DisplayName("状态判断：四个终态之外一律算未结束")
    void terminalStatuses() {
        assertThat(TaskStatuses.isTerminal("completed")).isTrue();
        assertThat(TaskStatuses.isTerminal("failed")).isTrue();
        assertThat(TaskStatuses.isTerminal("cancelled")).isTrue();
        assertThat(TaskStatuses.isTerminal("needs_human")).isTrue();
        assertThat(TaskStatuses.isTerminal("awaiting_approval")).isFalse();
        assertThat(TaskStatuses.isTerminal("executing")).isFalse();
        assertThat(TaskStatuses.isTerminal(null)).isFalse();
        assertThat(TaskStatuses.isTerminal("未来某个新状态")).isFalse();
    }
}
