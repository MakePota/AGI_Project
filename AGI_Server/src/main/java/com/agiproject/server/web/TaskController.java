package com.agiproject.server.web;

import com.agiproject.server.agent.AgentClient;
import com.agiproject.server.agent.AgentContract;
import com.agiproject.server.config.AgentProperties;
import com.agiproject.server.config.AsyncConfig;
import com.agiproject.server.task.TaskService;
import com.agiproject.server.web.dto.ApprovalDecisionRequest;
import com.agiproject.server.web.dto.CreateTaskRequest;
import jakarta.validation.Valid;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import reactor.core.Disposable;

import java.io.IOException;
import java.util.List;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

/**
 * 对前端的任务接口。
 *
 * <p>四个端点与 {@code AGI_Agent} 的内部接口**形状一致**——这不是巧合：
 * 本服务是薄转发层，保持同形意味着同一套验证脚本能同时验两个服务，
 * 也让"两边对不上账"这类问题从设计上就不容易发生。
 *
 * <p>唯一的差别是路径带 {@code /api} 前缀，给将来可能的静态资源、
 * 网关路由留出区分空间。
 */
@RestController
@RequestMapping("/api/tasks")
public class TaskController {

    private static final Logger log = LoggerFactory.getLogger(TaskController.class);

    private final TaskService taskService;
    private final AgentClient agentClient;
    private final AgentProperties properties;
    private final ScheduledExecutorService heartbeatScheduler;

    public TaskController(TaskService taskService,
                          AgentClient agentClient,
                          AgentProperties properties,
                          ScheduledExecutorService sseHeartbeatScheduler) {
        this.taskService = taskService;
        this.agentClient = agentClient;
        this.properties = properties;
        this.heartbeatScheduler = sseHeartbeatScheduler;
    }

    /** 建任务。返回 202 而不是 200：任务只是被受理，还在跑。 */
    @PostMapping
    @ResponseStatus(HttpStatus.ACCEPTED)
    public AgentContract.AgentTask create(@Valid @RequestBody CreateTaskRequest request) {
        return taskService.create(request.goal());
    }

    @GetMapping
    public List<AgentContract.AgentTask> list() {
        return taskService.list();
    }

    @GetMapping("/{taskId}")
    public AgentContract.AgentTask get(@PathVariable String taskId) {
        return taskService.get(taskId);
    }

    /** 提交人工审批决定。 */
    @PostMapping("/{taskId}/approval")
    public AgentContract.AgentTask approve(@PathVariable String taskId,
                                           @Valid @RequestBody ApprovalDecisionRequest request) {
        return taskService.decide(taskId, Boolean.TRUE.equals(request.approved()), request.reason());
    }

    /**
     * 事件流（SSE）。
     *
     * <p>本服务在这里只做**管道**：把 Agent 的流原样转给前端。
     * 不缓冲、不重放、不加工——事件的历史由 Agent 侧的有界缓冲负责，
     * 断线重连靠 {@code since} 参数向 Agent 续传（ADR 0015）。
     * 这样服务端就没有任何"要跟着清理"的流状态。
     *
     * <p>三件事必须做对，否则会出难查的问题：
     * <ol>
     *   <li><b>客户端断开时取消上游订阅</b>——上游是花钱的模型调用，
     *       继续为一个没人看的流保持连接等于持续烧钱；</li>
     *   <li><b>自己发心跳</b>——上游的注释行到不了前端（见 {@link AsyncConfig}）；</li>
     *   <li><b>超时给足</b>——任务可能长时间停在"等人批准"，超时给小了会误杀正常流程。</li>
     * </ol>
     */
    @GetMapping(value = "/{taskId}/events", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter events(@PathVariable String taskId,
                             @RequestParam(defaultValue = "0") long since) {
        SseEmitter emitter = new SseEmitter(properties.eventTimeoutMs());

        Disposable subscription = agentClient.streamEvents(taskId, since)
                .subscribe(
                        event -> forward(emitter, event),
                        error -> {
                            log.warn("任务 {} 的事件流中断：{}", taskId, error.getMessage());
                            endWithError(emitter, error);
                        },
                        () -> completeQuietly(emitter));

        ScheduledFuture<?> heartbeat = heartbeatScheduler.scheduleAtFixedRate(
                () -> beat(emitter),
                AsyncConfig.HEARTBEAT_SECONDS,
                AsyncConfig.HEARTBEAT_SECONDS,
                TimeUnit.SECONDS);

        emitter.onCompletion(() -> stop(heartbeat, subscription));
        emitter.onTimeout(() -> {
            stop(heartbeat, subscription);
            completeQuietly(emitter);
        });
        emitter.onError(ex -> stop(heartbeat, subscription));

        return emitter;
    }

    private void stop(ScheduledFuture<?> heartbeat, Disposable subscription) {
        heartbeat.cancel(false);
        subscription.dispose();
    }

    /**
     * 转发一条事件。
     *
     * <p><b>不要给这里的 data 指定 {@code MediaType.APPLICATION_JSON}。</b>
     * 上游给我们的已经是序列化好的 JSON 文本；再声明一次 JSON，
     * 消息转换器会把这段**字符串**再序列化一次，于是前端收到一个被引号包住的
     * 巨型字符串，而不是一个对象——错得很隐蔽，且只有真看数据才发现。
     */
    private void forward(SseEmitter emitter, ServerSentEvent<String> event) {
        try {
            emitter.send(SseEmitter.event().data(event.data()));
        } catch (IOException | IllegalStateException ex) {
            // 浏览器关掉标签页就会走到这里，属于正常现象，不是错误。
            completeQuietly(emitter);
        }
    }

    private void beat(SseEmitter emitter) {
        try {
            emitter.send(SseEmitter.event().comment("keep-alive"));
        } catch (IOException | IllegalStateException ex) {
            completeQuietly(emitter);
        }
    }

    /**
     * 上游出错时，先给前端发一条可读的错误事件再收尾。
     *
     * <p>直接 {@code completeWithError} 的话，前端只会看到连接莫名其妙断掉；
     * 发一条 {@code event: error} 至少能告诉它"是上游不可用"，而不是"你的网络坏了"。
     */
    private void endWithError(SseEmitter emitter, Throwable error) {
        try {
            emitter.send(SseEmitter.event().name("error").data("事件流中断：" + error.getMessage()));
        } catch (IOException | IllegalStateException ex) {
            // 前端已经走了，那就没什么可发的了。
        }
        completeQuietly(emitter);
    }

    /**
     * 安静地结束 SSE。
     *
     * <p>客户端已经断开时，{@code complete()} 自己也会再抛一次
     * {@code AsyncRequestNotUsableException}。那是“连接已经不可用”的正常结果，
     * 不是故障。让它冒出去会被全局异常处理器当成未预期异常，并尝试往
     * {@code text/event-stream} 响应写 {@code ProblemDetail}——又失败一次，
     * 每次任务正常结束都会留下两条 ERROR 日志。实测踩到过。
     */
    private static void completeQuietly(SseEmitter emitter) {
        try {
            emitter.complete();
        } catch (RuntimeException ignored) {
            // 连接已不可用，没有需要做的收尾动作。
        }
    }
}
