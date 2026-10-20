package com.agiproject.server.task;

/**
 * 上游给的产物超过了本服务允许缓存的大小。
 *
 * <p>这不是“文件不存在”，也不是“Agent 掉线”，而是一次协议层面的异常：
 * 要么 Agent 的产物体积上限被改大了却忘了同步本服务，要么上游返回了
 * 一段意料之外的大响应。对应 502，让调用方知道问题在**上游**。
 */
public class ArtifactTooLargeException extends RuntimeException {

    public ArtifactTooLargeException(String message) {
        super(message);
    }
}
