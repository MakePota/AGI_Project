package com.agiproject.server.task;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.nio.file.Path;
import java.nio.file.Paths;

/**
 * 产物缓存的配置。
 *
 * @param dir      缓存目录。相对路径以**服务进程的工作目录**为基准；启动日志会
 *                 打印解析后的绝对路径，避免“它到底存到哪了”的猜测。
 * @param maxBytes 单个产物允许的最大字节数。**必须大于 Agent 侧的产物上限**
 *                 （20 MB）；否则 Agent 能生成、这里却下不下来。
 */
@ConfigurationProperties(prefix = "agi.artifacts")
public record ArtifactProperties(String dir, long maxBytes) {

    public ArtifactProperties {
        if (dir == null || dir.isBlank()) {
            throw new IllegalArgumentException("agi.artifacts.dir 不能为空");
        }
        if (maxBytes <= 0) {
            throw new IllegalArgumentException("agi.artifacts.max-bytes 必须大于 0");
        }
    }

    /** 解析成绝对路径；相对路径按进程工作目录展开。 */
    public Path root() {
        return Paths.get(dir).toAbsolutePath().normalize();
    }
}
