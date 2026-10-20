package com.agiproject.server.task;

/**
 * 产物存在元数据，但内容不可用：MIME 不在白名单、文件头不是图片，或者写入缓存失败。
 *
 * <p>它与“任务不存在”“Agent 掉线”都不是一回事，所以单独一个类型、单独一个状态码
 * （502）：问题出在上游给的内容，调用方重试同一个地址也修不好。
 */
public class ArtifactUnavailableException extends RuntimeException {

    public ArtifactUnavailableException(String message) {
        super(message);
    }

    public ArtifactUnavailableException(String message, Throwable cause) {
        super(message, cause);
    }
}
