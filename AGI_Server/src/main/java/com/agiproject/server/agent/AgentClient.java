package com.agiproject.server.agent;

import com.agiproject.server.config.AgentProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.MediaType;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientRequestException;
import org.springframework.web.reactive.function.client.WebClientResponseException;
import reactor.core.publisher.Flux;

import java.time.Duration;
import java.util.HashMap;
import java.util.Map;
import java.util.NoSuchElementException;

/**
 * 访问 {@code AGI_Agent} 的内部服务 API。
 *
 * <p><b>这一层只做翻译，不做业务判断。</b> 它把 HTTP 状态码翻成本服务的三类异常，
 * 让上层（和最终的 HTTP 响应）能区分：
 *
 * <ul>
 *   <li>任务不存在 → {@link NoSuchElementException} → 本服务回 404</li>
 *   <li>状态冲突（比如对已结束的任务再提交审批）→ {@link IllegalStateException} → 409</li>
 *   <li>连不上或对方出错 → {@link AgentUnavailableException} → 503</li>
 * </ul>
 *
 * <p>最后一条尤其要留住：<b>"Agent 服务没起来"和"任务失败了"是两件事</b>。
 * 把它们都压成 500 会让排查方向完全跑偏。
 */
@Component
public class AgentClient {

    private static final Logger log = LoggerFactory.getLogger(AgentClient.class);

    /** 出错时回显的响应体上限——避免把一整段 HTML 错误页塞进日志和响应里。 */
    private static final int ERROR_BODY_LIMIT = 300;

    private final WebClient client;
    private final AgentProperties properties;

    public AgentClient(WebClient agentWebClient, AgentProperties properties) {
        this.client = agentWebClient;
        this.properties = properties;
    }

    /** 建任务。Agent 立刻返回，任务在它那边排队执行。 */
    public AgentContract.AgentTask createTask(String goal) {
        return callAgent(client.post().uri("/tasks").bodyValue(Map.of("goal", goal)));
    }

    /** 查任务当前状态——**这是唯一的事实来源**，服务端存的只是镜像。 */
    public AgentContract.AgentTask getTask(String taskId) {
        return callAgent(client.get().uri("/tasks/{id}", taskId));
    }

    /** 提交人工审批决定。 */
    public AgentContract.AgentTask decideApproval(String taskId, boolean approved, String reason) {
        // 用 HashMap 而不是 Map.of：reason 允许为 null，而 Map.of 不接受 null 值，
        // 直接抛 NullPointerException——一个与被调用方完全无关的报错。
        Map<String, Object> body = new HashMap<>();
        body.put("approved", approved);
        body.put("reason", reason);
        return callAgent(client.post().uri("/tasks/{id}/approval", taskId).bodyValue(body));
    }

    /**
     * 订阅任务的 SSE 事件流。
     *
     * <p><b>刻意不加 {@code .timeout(...)}。</b> Reactor 的 {@code timeout} 是
     * "两次事件之间的间隔"，而任务停在"等人批准"时可能几分钟没有任何事件
     * （心跳是注释行，编解码器不会当事件交出来）。加了它，正常等待会被当成故障切断。
     * 流的寿命由 HTTP 层的 SseEmitter 超时控制，不在这里管。
     *
     * <p>上游的结束帧（{@code event: end}）不透传给客户端：它的作用是让**我们**
     * 知道该收尾了，而不是让前端看到一条内容为 {@code {}} 的无意义事件。
     */
    public Flux<ServerSentEvent<String>> streamEvents(String taskId, long since) {
        return client.get()
                .uri(builder -> builder.path("/tasks/{id}/events")
                        .queryParam("since", since)
                        .build(taskId))
                .accept(MediaType.TEXT_EVENT_STREAM)
                .retrieve()
                .bodyToFlux(new ParameterizedTypeReference<ServerSentEvent<String>>() {
                })
                .filter(event -> !"end".equals(event.event()))
                .onErrorMap(WebClientRequestException.class,
                        ex -> new AgentUnavailableException(
                                "事件流中断：无法连接智能体服务（" + properties.baseUrl() + "）", ex));
    }

    /**
     * 三个非流式调用共用的出口。
     *
     * <p>它们都返回 {@code AgentTask}，所以不做泛型；需要第二种返回类型时再泛化，
     * 现在为一个假想的未来加类型参数，只会让调用点更难读。
     */
    private AgentContract.AgentTask callAgent(WebClient.RequestHeadersSpec<?> spec) {
        Duration timeout = Duration.ofMillis(properties.requestTimeoutMs());
        try {
            return spec.retrieve()
                    .bodyToMono(AgentContract.AgentTask.class)
                    .block(timeout);
        } catch (WebClientResponseException ex) {
            throw translate(ex);
        } catch (WebClientRequestException ex) {
            log.warn("调用 Agent 失败：{}", ex.getMessage());
            throw new AgentUnavailableException(
                    "无法连接智能体服务（" + properties.baseUrl() + "）：" + ex.getMessage(), ex);
        } catch (IllegalStateException ex) {
            // block(Duration) 超时抛的就是它——这是它在这里唯一可能代表的意思，
            // 但仍要在消息里写明，免得将来别处也抛它时被误读成超时。
            throw new AgentUnavailableException(
                    "调用智能体服务超时（超过 " + properties.requestTimeoutMs() + " 毫秒）", ex);
        }
    }

    private RuntimeException translate(WebClientResponseException ex) {
        int status = ex.getStatusCode().value();
        String body = ex.getResponseBodyAsString();
        String detail = body.length() > ERROR_BODY_LIMIT ? body.substring(0, ERROR_BODY_LIMIT) + "…" : body;

        return switch (status) {
            case 404 -> new NoSuchElementException("智能体侧没有这个任务");
            case 409 -> new IllegalStateException("该任务当前不在等待批准，可能已经被处理过");
            default -> new AgentUnavailableException("智能体服务返回 HTTP " + status + "：" + detail);
        };
    }
}
