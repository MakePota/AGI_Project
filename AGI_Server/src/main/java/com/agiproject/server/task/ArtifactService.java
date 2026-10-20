package com.agiproject.server.task;

import com.agiproject.server.agent.AgentClient;
import com.agiproject.server.agent.AgentContract.AgentArtifact;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * 产物文件的本地缓存。
 *
 * <p><b>为什么 Server 要自己存一份：</b> 终态任务不再向 Agent 对齐
 * （{@code TaskService.get} 直接读本地镜像）。如果图片只留在 Agent 那边，
 * Agent 一停机，用户刷新页面就看不到已经生成的结果了。元数据在数据库里、
 * 二进制在这里，两者一起构成“任务结果的镜像”。
 *
 * <p><b>为什么是懒下载：</b> 任务执行时 Server 可能还没收到产物元数据；
 * 用户真正请求图片时再拉，既少一次竞态，也避免列表刷新时顺手下载一堆图。
 */
@Service
public class ArtifactService {

    private static final Logger log = LoggerFactory.getLogger(ArtifactService.class);

    /** 与 Agent 侧 ``artifacts._NAME_RE`` 保持一致：只接受代码生成的名字。 */
    private static final Pattern NAME_PATTERN =
            Pattern.compile("^[0-9]{8}-[0-9]{6}-[0-9a-f]{8}\\.(?:png|jpg|gif|webp)$");

    /** 任务 id 由 Agent 生成（hex），这里仍然做一次白名单，防御脏数据拼路径。 */
    private static final Pattern TASK_ID_PATTERN = Pattern.compile("^[A-Za-z0-9_-]{1,64}$");

    /** 只给浏览器显示图片；不把上游的任意 Content-Type 原样转出去。 */
    private static final Set<String> ALLOWED_MIME =
            Set.of("image/png", "image/jpeg", "image/gif", "image/webp");

    private final AgentClient agentClient;
    private final ArtifactProperties properties;

    public ArtifactService(AgentClient agentClient, ArtifactProperties properties) {
        this.agentClient = agentClient;
        this.properties = properties;
        log.info("产物的本地缓存目录：{}", properties.root());
    }

    /**
     * 确保这个产物已经在本地缓存里，返回它的路径。
     *
     * <p>{@code synchronized} 是刻意的：单人自用，串行化足够；它换来的是
     * “同一个文件不会被两个请求同时下载、同时写”。跨进程并发不在本期范围。
     */
    public synchronized Path ensureStored(String taskId, AgentArtifact artifact) {
        validate(taskId, artifact);

        Path root = properties.root();
        Path taskDir = root.resolve(taskId).normalize();
        Path target = taskDir.resolve(artifact.name()).normalize();

        // 第二道防线：即使正则被绕过，解析后的路径也必须仍在任务目录下。
        if (!taskDir.startsWith(root) || !target.startsWith(taskDir)) {
            throw new ArtifactNotFoundException("产物路径越界");
        }

        try {
            if (Files.isRegularFile(target) && Files.size(target) == artifact.size()) {
                return target;
            }

            byte[] data = agentClient.downloadArtifact(artifact.name());
            if (data.length > properties.maxBytes()) {
                throw new ArtifactTooLargeException(
                        "产物大小 " + data.length + " 字节，超过上限 " + properties.maxBytes());
            }
            if (data.length != artifact.size()) {
                // 元数据与内容对不上通常意味着 Agent 侧被手工改过或缓存过期；
                // 不把它当成致命错误，但必须留痕，否则下次很难解释。
                log.warn("产物 {} 的实际大小 {} 与元数据 {} 不一致",
                        artifact.name(), data.length, artifact.size());
            }
            if (!ALLOWED_MIME.contains(artifact.mime())) {
                throw new ArtifactUnavailableException(
                        "产物 MIME 不在白名单： " + artifact.mime());
            }
            if (!looksLikeImage(data)) {
                throw new ArtifactUnavailableException("产物内容不是受支持的图片格式");
            }

            Files.createDirectories(taskDir);
            Path temp = Files.createTempFile(taskDir, artifact.name(), ".tmp");
            try {
                Files.write(temp, data);
                moveAtomically(temp, target);
            } finally {
                Files.deleteIfExists(temp);
            }
            return target;
        } catch (IOException ex) {
            // 磁盘满、权限不足、目录被删……都属于“本服务暂时给不出这张图”。
            throw new ArtifactUnavailableException(
                    "写入产物的本地缓存失败：" + ex.getMessage(), ex);
        }
    }

    private static void validate(String taskId, AgentArtifact artifact) {
        if (taskId == null || !TASK_ID_PATTERN.matcher(taskId).matches()) {
            throw new ArtifactNotFoundException("非法的任务 id");
        }
        if (artifact == null || artifact.name() == null
                || !NAME_PATTERN.matcher(artifact.name()).matches()) {
            throw new ArtifactNotFoundException("非法的产物名");
        }
    }

    /**
     * 原子替换目标文件；文件系统不支持时退回普通替换。
     *
     * <p>直接写目标文件的话，进程中途崩溃会留下半截图片——它看起来像一个完整产物，
     * 界面只会显示破图，排查时很难想到是写了一半。
     */
    private static void moveAtomically(Path temp, Path target) throws IOException {
        try {
            Files.move(temp, target,
                    StandardCopyOption.REPLACE_EXISTING,
                    StandardCopyOption.ATOMIC_MOVE);
        } catch (AtomicMoveNotSupportedException ex) {
            Files.move(temp, target, StandardCopyOption.REPLACE_EXISTING);
        }
    }

    /** 按文件头判断图片类型；与 Agent 侧 ``images.sniff_mime`` 的规则保持一致。 */
    private static boolean looksLikeImage(byte[] data) {
        if (data.length >= 8 && data[0] == (byte) 0x89 && data[1] == 'P'
                && data[2] == 'N' && data[3] == 'G') {
            return true;
        }
        if (data.length >= 3 && data[0] == (byte) 0xFF
                && data[1] == (byte) 0xD8 && data[2] == (byte) 0xFF) {
            return true;
        }
        if (data.length >= 6 && data[0] == 'G' && data[1] == 'I' && data[2] == 'F'
                && data[3] == '8' && (data[4] == '7' || data[4] == '9') && data[5] == 'a') {
            return true;
        }
        return data.length >= 12 && data[0] == 'R' && data[1] == 'I' && data[2] == 'F'
                && data[3] == 'F' && data[8] == 'W' && data[9] == 'E'
                && data[10] == 'B' && data[11] == 'P';
    }
}
