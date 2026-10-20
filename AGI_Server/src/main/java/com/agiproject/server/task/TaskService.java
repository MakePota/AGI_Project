package com.agiproject.server.task;

import com.agiproject.server.agent.AgentClient;
import com.agiproject.server.agent.AgentContract;
import com.agiproject.server.agent.AgentContract.AgentArtifact;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.ObjectMapper;

import java.time.Instant;
import java.time.format.DateTimeParseException;
import java.util.Arrays;
import java.util.List;
import java.util.NoSuchElementException;
import java.util.Objects;

/**
 * 任务的业务逻辑：把 Agent 的答复抄进本地表，并在需要时把它们对齐。
 *
 * <p><b>核心原则：服务端不推断任务状态，只抄 Agent 的。</b>
 * 因此这里没有任何"根据事件猜状态"的代码——那类代码一旦和 Agent 的真实状态
 * 出现分歧，就会产生"两个系统各说各话"且无从对账的局面。
 *
 * <p><b>为什么这些方法不加 {@code @Transactional}：</b> 它们中间会发起 HTTP 调用。
 * 把网络调用包在数据库事务里，会让事务在等待对方响应期间一直占着连接——
 * 这是很常见的性能事故源。这里的做法是"读一次、调用一次、写一次"，
 * 每一步各自成事务，没有跨网络的长事务。
 */
@Service
public class TaskService {

    private static final Logger log = LoggerFactory.getLogger(TaskService.class);

    /**
     * 没有登录之前，所有任务都归属这个固定的本地用户。
     *
     * <p>写死一个常量而不是留空/可空，是因为 {@code user_id} 列本来就是
     * 为"以后加登录"准备的：现在有值，将来只是换成真实用户 id。
     */
    public static final String LOCAL_USER_ID = "local-user";

    /** 列表页一次最多去上游对齐多少条，避免"一次列表请求放大成 N 个 HTTP 调用"。 */
    private static final int MAX_REFRESH_PER_LIST = 20;

    private final TaskRepository repository;
    private final AgentClient agentClient;
    private final ArtifactService artifactService;
    private final ObjectMapper objectMapper;

    public TaskService(TaskRepository repository,
                       AgentClient agentClient,
                       ArtifactService artifactService,
                       ObjectMapper objectMapper) {
        this.repository = repository;
        this.agentClient = agentClient;
        this.artifactService = artifactService;
        this.objectMapper = objectMapper;
    }

    /** 建任务：交给 Agent，然后把它的答复抄进本地表。 */
    public AgentContract.AgentTask create(String goal) {
        AgentContract.AgentTask created = agentClient.createTask(goal);

        Instant now = Instant.now();
        Instant createdAt = parseInstant(created.createdAt(), now);
        TaskEntity entity = new TaskEntity(
                created.taskId(),
                LOCAL_USER_ID,
                created.goal() != null ? created.goal() : goal,
                created.status(),
                createdAt,
                parseInstant(created.updatedAt(), createdAt)
        );
        // 走同一个抄写点，而不是在这里手写 status/result —— 两处各写一份，
        // 迟早会出现“建任务时没抄 artifacts、查任务时却有”这种不一致。
        apply(entity, created);
        repository.save(entity);
        log.info("已创建任务 {}（状态 {}）", created.taskId(), created.status());
        return created;
    }

    /**
     * 查一个任务。
     *
     * <p>**没结束的任务先向 Agent 对齐一次再返回。** 这是"不推断状态"的代价与实现：
     * 本地存的是镜像，而镜像是会过期的；只要有人来看，就先跟权威对一次。
     * 好处是不需要后台常驻任务去轮询，代价是这次读取会多一个 HTTP 调用——
     * 对单人使用完全划算。
     */
    public AgentContract.AgentTask get(String taskId) {
        TaskEntity entity = require(taskId);
        if (!TaskStatuses.isTerminal(entity.getStatus())) {
            return refresh(entity);
        }
        return toView(entity);
    }

    /**
     * 列表：最近的任务在前，顺便把没结束的对齐一遍。
     *
     * <p><b>列表里不带审批载荷</b>（{@code approval} 恒为 null）：本地不存它，
     * 而每条都现去 Agent 取会把一次列表请求放大成 N 个 HTTP 调用。
     * 前端看到某条任务处于"等待批准"时，再取一次它的详情或订阅事件流即可——
     * 这正是"中断是可查询状态"带来的好处：谁想看，什么时候看都行（ADR 0015）。
     */
    public List<AgentContract.AgentTask> list() {
        List<TaskEntity> entities = repository.findByUserIdOrderByCreatedAtDesc(LOCAL_USER_ID);

        int refreshed = 0;
        for (TaskEntity entity : entities) {
            if (TaskStatuses.isTerminal(entity.getStatus())) {
                continue;
            }
            if (refreshed >= MAX_REFRESH_PER_LIST) {
                log.warn("未结束的任务超过 {} 条，本次列表只对齐了前 {} 条",
                        MAX_REFRESH_PER_LIST, MAX_REFRESH_PER_LIST);
                break;
            }
            try {
                refresh(entity);
                refreshed++;
            } catch (RuntimeException ex) {
                // 一条对齐失败不该让整个列表打不开：Agent 没起来的时候，
                // 用户至少应该还能看到本地已有的任务记录。
                log.warn("对齐任务 {} 失败：{}", entity.getId(), ex.getMessage());
            }
        }
        return entities.stream().map(this::toView).toList();
    }

    /** 提交审批决定，并立即把结果对齐回来。 */
    public AgentContract.AgentTask decide(String taskId, boolean approved, String reason) {
        require(taskId);
        AgentContract.AgentTask updated = agentClient.decideApproval(taskId, approved, reason);
        TaskEntity entity = require(taskId);
        apply(entity, updated);
        repository.save(entity);
        log.info("任务 {} 的审批结果为 {}，当前状态 {}", taskId, approved, updated.status());
        return updated;
    }

    private AgentContract.AgentTask refresh(TaskEntity entity) {
        AgentContract.AgentTask latest = agentClient.getTask(entity.getId());
        apply(entity, latest);
        repository.save(entity);
        return latest;
    }

    /** 把 Agent 的答复抄进实体。**只抄，不改写**。 */
    private void apply(TaskEntity entity, AgentContract.AgentTask task) {
        entity.setStatus(task.status());
        entity.setResult(task.result());
        entity.setError(task.error());
        entity.setArtifacts(writeArtifacts(task.artifacts()));
        entity.setUpdatedAt(parseInstant(task.updatedAt(), Instant.now()));

        // 任务进入终态时，尽力把图片文件也拉到本地缓存。
        // 这一步失败**不能阻塞元数据镜像**：用户以后请求图片时
        // ArtifactController 还会再试一次；而状态与结果丢了就再也补不回来。
        if (TaskStatuses.isTerminal(task.status()) && task.artifacts() != null) {
            for (AgentArtifact artifact : task.artifacts()) {
                if (artifact == null) {
                    continue;
                }
                try {
                    artifactService.ensureStored(task.taskId(), artifact);
                } catch (RuntimeException ex) {
                    log.warn("缓存任务 {} 的产物 {} 失败：{}",
                            task.taskId(), artifact.name(), ex.getMessage());
                }
            }
        }
    }

    /**
     * 找一个产物，返回它的元数据。
     *
     * <p>非终态任务先向 Agent 对齐一次：产物可能刚生成，本地镜像还没抄到。
     * 终态任务不再问上游——这正是 artifacts 必须持久化的原因。
     */
    public AgentArtifact getArtifact(String taskId, String name) {
        TaskEntity entity = require(taskId);
        AgentArtifact found = findArtifact(entity, name);
        if (found == null && !TaskStatuses.isTerminal(entity.getStatus())) {
            refresh(entity);
            found = findArtifact(entity, name);
        }
        if (found == null) {
            throw new ArtifactNotFoundException("任务 " + taskId + " 没有产物 " + name);
        }
        return found;
    }

    private AgentArtifact findArtifact(TaskEntity entity, String name) {
        if (name == null) {
            return null;
        }
        return readArtifacts(entity.getArtifacts()).stream()
                .filter(artifact -> name.equals(artifact.name()))
                .findFirst()
                .orElse(null);
    }

    /** 把 JSON 读回产物清单。**坏数据降级为空列表**，不要让它把任务详情一起拖垮。 */
    private List<AgentArtifact> readArtifacts(String json) {
        if (json == null || json.isBlank()) {
            return List.of();
        }
        try {
            AgentArtifact[] parsed = objectMapper.readValue(json, AgentArtifact[].class);
            return parsed == null
                    ? List.of()
                    : Arrays.stream(parsed).filter(Objects::nonNull).toList();
        } catch (JacksonException ex) {
            log.warn("无法解析任务产物清单，按空列表处理：{}", ex.getMessage());
            return List.of();
        }
    }

    /** 序列化产物清单。序列化失败同样降级为空数组，理由同上。 */
    private String writeArtifacts(List<AgentArtifact> artifacts) {
        if (artifacts == null || artifacts.isEmpty()) {
            return "[]";
        }
        try {
            return objectMapper.writeValueAsString(artifacts);
        } catch (JacksonException ex) {
            log.warn("无法序列化任务产物清单，按空数组处理：{}", ex.getMessage());
            return "[]";
        }
    }

    private TaskEntity require(String taskId) {
        return repository.findById(taskId)
                .orElseThrow(() -> new NoSuchElementException("没有这个任务：" + taskId));
    }

    /**
     * 把实体呈现成与 Agent 相同的形状。
     *
     * <p>**刻意复用 {@code AgentContract.AgentTask} 作为对外响应体**，而不是另建一个
     * 自己的 DTO 再写一遍映射：本服务的对外接口就是"任务镜像"，
     * 两边形状不一致反而需要一层映射去维护，而那一层迟早会漂移。
     * 代价是上游形状变化会直接传到前端——当前接受，因为整个设计就是要当一个忠实的薄转发层。
     */
    private AgentContract.AgentTask toView(TaskEntity entity) {
        return new AgentContract.AgentTask(
                entity.getId(),
                entity.getStatus(),
                entity.getGoal(),
                entity.getCreatedAt().toString(),
                entity.getUpdatedAt().toString(),
                entity.getResult(),
                entity.getError(),
                // 本地不存审批载荷（Server 不存事件，见 ADR 0015）；
                // 等待中的任务上面已经对齐过，那时 approval 是从 Agent 现取的。
                null,
                readArtifacts(entity.getArtifacts())
        );
    }

    /** 解析 ISO-8601 时间；解析不了就用兜底值并留痕——宁可时间不准，也不要整个请求失败。 */
    private static Instant parseInstant(String value, Instant fallback) {
        if (value == null || value.isBlank()) {
            return fallback;
        }
        try {
            return Instant.parse(value);
        } catch (DateTimeParseException ex) {
            log.warn("无法解析时间戳 {}，改用 {}", value, fallback);
            return fallback;
        }
    }
}
