package com.agiproject.server.web;

import com.agiproject.server.agent.AgentContract.AgentArtifact;
import com.agiproject.server.task.ArtifactService;
import com.agiproject.server.task.ArtifactUnavailableException;
import com.agiproject.server.task.TaskService;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.Set;

/**
 * 取任务产出的图片。
 *
 * <p><b>为什么路径里带 taskId：</b> 只凭 name 的话，任何知道文件名的人都能取到
 * 任意产物。带上 taskId 之后，{@link TaskService#getArtifact} 会先确认
 * “这个产物确实属于这个任务”。本服务没有认证，这层归属校验是唯一的边界。
 *
 * <p><b>为什么响应是文件而不是 JSON：</b> 前端用 {@code <image src>} 直接显示，
 * 不需要先取一段 base64 再解码。base64 塞进 JSON 会让响应体膨胀 1.33 倍，
 * 也会把二进制内容带进原本只传文本的接口形状里。
 */
@RestController
@RequestMapping("/api/tasks/{taskId}/artifacts")
public class ArtifactController {

    /** 只允许这四种图片类型；不把上游给的任意 Content-Type 原样转出去。 */
    private static final Set<String> ALLOWED_MIME =
            Set.of("image/png", "image/jpeg", "image/gif", "image/webp");

    private final TaskService taskService;
    private final ArtifactService artifactService;

    public ArtifactController(TaskService taskService, ArtifactService artifactService) {
        this.taskService = taskService;
        this.artifactService = artifactService;
    }

    @GetMapping("/{name}")
    public ResponseEntity<Resource> get(@PathVariable String taskId,
                                        @PathVariable String name) {
        // 先确认归属，再碰磁盘。顺序反了就会变成“任何名字都能触发一次下载”。
        AgentArtifact artifact = taskService.getArtifact(taskId, name);
        Path path = artifactService.ensureStored(taskId, artifact);

        MediaType contentType = parseMime(artifact.mime());
        String inlineName = ContentDisposition.inline()
                .filename(name, StandardCharsets.UTF_8)
                .build()
                .toString();

        return ResponseEntity.ok()
                .contentType(contentType)
                .contentLength(path.toFile().length())
                .header(HttpHeaders.CONTENT_DISPOSITION, inlineName)
                // 产物名唯一、内容不可变，可以缓存；private 是因为本服务没有认证，
                // 不能让中间代理把它当成公共资源。
                .header(HttpHeaders.CACHE_CONTROL, "private, max-age=86400")
                // 明确告诉浏览器“按 Content-Type 渲染，不要嗅探”。这是防止把
                // 一段伪装成图片的 HTML 当页面执行的常规措施。
                .header("X-Content-Type-Options", "nosniff")
                .body(new FileSystemResource(path));
    }

    private static MediaType parseMime(String mime) {
        try {
            MediaType parsed = MediaType.parseMediaType(mime);
            if (!ALLOWED_MIME.contains(parsed.toString())) {
                throw new ArtifactUnavailableException("产物类型不在白名单：" + mime);
            }
            return parsed;
        } catch (IllegalArgumentException ex) {
            // parseMediaType 对非法字符串抛的也是 IllegalArgumentException；
            // 换成项目自己的异常，让全局处理器给出 502 而不是 400。
            throw new ArtifactUnavailableException("无法识别的产物类型：" + mime, ex);
        }
    }
}
