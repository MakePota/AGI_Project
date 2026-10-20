package com.agiproject.server.task;

import com.agiproject.server.agent.AgentClient;
import com.agiproject.server.agent.AgentContract.AgentArtifact;
import com.agiproject.server.agent.AgentUnavailableException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Base64;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 产物缓存的测试。
 *
 * <p><b>不启动 Spring、不连数据库、不访问网络。</b> Agent 客户端是替身，
 * 缓存目录是 JUnit 的临时目录。真实链路（Flyway、HTTP 下载）靠端到端烟测。
 *
 * <p>这里最值得钉住的是**路径安全**与**幂等**：一个错误的文件名不该读到
 * 任务目录之外；同一张图重复请求不该反复下载。
 */
class ArtifactServiceTest {

    /** 一张真实的 1x1 PNG；文件头嗅探需要它，随便的字节会在格式检查那一支失败。 */
    private static final byte[] PNG = Base64.getDecoder().decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2n0kAAAAASUVORK5CYII=");

    private static final String NAME = "20261020-120000-deadbeef.png";
    private static final String TASK_ID = "abc123";

    private AgentClient agentClient;
    private ArtifactService service;
    private Path root;

    @BeforeEach
    void setUp(@TempDir Path tempDir) {
        agentClient = mock(AgentClient.class);
        root = tempDir;
        service = new ArtifactService(
                agentClient,
                new ArtifactProperties(tempDir.toString(), 1024 * 1024));
    }

    private static AgentArtifact artifact(long size) {
        return new AgentArtifact(NAME, "image/png", size, "image");
    }

    @Test
    @DisplayName("下载并写入 taskId/name，返回可读的路径")
    void downloadsAndStores() throws IOException {
        when(agentClient.downloadArtifact(NAME)).thenReturn(PNG);

        Path path = service.ensureStored(TASK_ID, artifact(PNG.length));

        assertThat(path).isEqualTo(root.resolve(TASK_ID).resolve(NAME));
        assertThat(Files.readAllBytes(path)).isEqualTo(PNG);
        try (var files = Files.list(root.resolve(TASK_ID))) {
            assertThat(files.noneMatch(p -> p.getFileName().toString().endsWith(".tmp")))
                    .isTrue();
        }
    }

    @Test
    @DisplayName("本地已有且大小一致时不重复下载")
    void doesNotDownloadTwice() throws IOException {
        Path taskDir = root.resolve(TASK_ID);
        Files.createDirectories(taskDir);
        Files.write(taskDir.resolve(NAME), PNG);

        Path path = service.ensureStored(TASK_ID, artifact(PNG.length));

        assertThat(path).isEqualTo(taskDir.resolve(NAME));
        verify(agentClient, never()).downloadArtifact(NAME);
    }

    @Test
    @DisplayName("本地文件大小不一致时重新下载并覆盖")
    void reDownloadsWhenSizeDiffers() throws IOException {
        Path taskDir = root.resolve(TASK_ID);
        Files.createDirectories(taskDir);
        Files.write(taskDir.resolve(NAME), new byte[] {1, 2, 3});
        when(agentClient.downloadArtifact(NAME)).thenReturn(PNG);

        service.ensureStored(TASK_ID, artifact(PNG.length));

        verify(agentClient).downloadArtifact(NAME);
        assertThat(Files.readAllBytes(taskDir.resolve(NAME))).isEqualTo(PNG);
    }

    @Test
    @DisplayName("非法任务 id / 产物名直接拒绝，不碰磁盘")
    void rejectsPathTraversal() {
        assertThatThrownBy(() -> service.ensureStored("../evil", artifact(PNG.length)))
                .isInstanceOf(ArtifactNotFoundException.class);
        assertThatThrownBy(() -> service.ensureStored(TASK_ID,
                new AgentArtifact("../evil.png", "image/png", PNG.length, "image")))
                .isInstanceOf(ArtifactNotFoundException.class);
        verify(agentClient, never()).downloadArtifact(anyString());
    }

    @Test
    @DisplayName("内容不是图片时拒绝缓存")
    void rejectsNonImageContent() {
        when(agentClient.downloadArtifact(NAME)).thenReturn("not an image".getBytes());

        assertThatThrownBy(() -> service.ensureStored(TASK_ID, artifact(12)))
                .isInstanceOf(ArtifactUnavailableException.class);
    }

    @Test
    @DisplayName("MIME 不在白名单时拒绝缓存")
    void rejectsUnexpectedMime() {
        when(agentClient.downloadArtifact(NAME)).thenReturn(PNG);
        AgentArtifact html = new AgentArtifact(NAME, "text/html", PNG.length, "image");

        assertThatThrownBy(() -> service.ensureStored(TASK_ID, html))
                .isInstanceOf(ArtifactUnavailableException.class);
    }

    @Test
    @DisplayName("超过配置上限时抛 ArtifactTooLargeException")
    void rejectsOversized() {
        ArtifactService small = new ArtifactService(
                agentClient,
                new ArtifactProperties(root.toString(), 8));
        when(agentClient.downloadArtifact(NAME)).thenReturn(PNG);

        assertThatThrownBy(() -> small.ensureStored(TASK_ID, artifact(PNG.length)))
                .isInstanceOf(ArtifactTooLargeException.class);
    }

    @Test
    @DisplayName("上游不可用时原样抛出 503 类异常，不伪装成 404")
    void propagatesAgentUnavailable() {
        when(agentClient.downloadArtifact(NAME))
                .thenThrow(new AgentUnavailableException("Agent 掉线了"));

        assertThatThrownBy(() -> service.ensureStored(TASK_ID, artifact(PNG.length)))
                .isInstanceOf(AgentUnavailableException.class);
    }
}
