package com.agiproject.server.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 后台调度用的线程池。
 *
 * <p>目前只有一个用途：给 SSE 连接定时发心跳。
 *
 * <p><b>为什么必须自己发心跳：</b> 上游（Agent）会发 {@code : keep-alive} 注释行，
 * 但 SSE 编解码器只把 {@code data} 交给应用层，注释行在半路就被丢掉了。
 * 于是任务停在"等人批准"时，前端那条连接可能几分钟没有任何字节——
 * 中间的代理、负载均衡甚至浏览器都会把它当成死连接掐掉。
 * 而且这个问题在本地直连时**完全看不出来**，只在中间隔了一层时才出现。
 *
 * <p>单线程足够：心跳只是往一组已建立的连接上写一小段常量文本，
 * 开多线程反而要多考虑并发写同一个 emitter 的顺序问题。
 * 设成守护线程是为了不阻塞进程退出。
 */
@Configuration
public class AsyncConfig {

    /** 心跳间隔。取 15 秒是业界常见值：远小于各类默认的 30～60 秒空闲超时。 */
    public static final long HEARTBEAT_SECONDS = 15;

    @Bean(destroyMethod = "shutdown")
    ScheduledExecutorService sseHeartbeatScheduler() {
        AtomicInteger counter = new AtomicInteger();
        return Executors.newSingleThreadScheduledExecutor(task -> {
            Thread thread = new Thread(task, "sse-heartbeat-" + counter.incrementAndGet());
            thread.setDaemon(true);
            return thread;
        });
    }
}
