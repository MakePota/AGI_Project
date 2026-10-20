package com.agiproject.server.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 如何联系 {@code AGI_Agent}。
 *
 * <p>用 record 做构造器绑定：字段不可变，且**缺配置会在启动时就报错**——
 * 比"跑起来之后第一次调用才发现没配"要早得多。
 *
 * @param baseUrl           Agent 内部服务 API 的地址，例如 {@code http://127.0.0.1:8123}
 * @param connectTimeoutMs  连接超时。给得短是刻意的：连不上就是连不上，
 *                          让用户立刻看到明确错误，而不是等一个默认的几十秒。
 * @param requestTimeoutMs  普通请求（建任务 / 查状态 / 提交审批）的整体超时。
 *                          这三个在 Agent 那边都是"立刻返回、任务在后台排队"，
 *                          所以可以给得短。**事件流不走这个值**。
 * @param eventTimeoutMs    事件流的最长等待。**别按"请求超时"理解它**——
 *                          任务可能长时间停在"等人批准"，期间流上只有心跳；
 *                          值给小了会把正常流程误杀。
 * @param artifactTimeoutMs 下载产物（图片）的超时。**不能复用 requestTimeoutMs**：
 *                          那个 15 秒是为“立刻返回”的接口设的，而这里要把一个
 *                          可能几 MB 的文件完整读进内存。
 */
@ConfigurationProperties(prefix = "agi.agent")
public record AgentProperties(
        String baseUrl,
        int connectTimeoutMs,
        long requestTimeoutMs,
        long eventTimeoutMs,
        long artifactTimeoutMs
) {
}
