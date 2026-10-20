package com.agiproject.server.config;

import com.agiproject.server.task.ArtifactProperties;
import io.netty.channel.ChannelOption;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.reactive.ReactorClientHttpConnector;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.netty.http.client.HttpClient;

/**
 * 用来访问 {@code AGI_Agent} 的 HTTP 客户端。
 *
 * <p>两处配置是刻意的，都与"流会长时间没有数据"有关：
 *
 * <ul>
 *   <li><b>只设连接超时，不设读超时。</b> 任务可能停在"等人批准"上很久，期间流上
 *       什么数据都没有。任何读超时都会把这个**正常的等待**当成故障掐断，
 *       而且症状是"偶尔断"、极难复现。</li>
 *   <li><b>把单行缓冲上限抬到 1 MB。</b> 默认值对普通 JSON 够用，但事件里可能带
 *       工具返回的长文本，超限会直接抛错而不是截断。</li>
 * </ul>
 */
@Configuration
public class WebClientConfig {

    @Bean
    WebClient agentWebClient(AgentProperties properties) {
        HttpClient httpClient = HttpClient.create()
                .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, properties.connectTimeoutMs());

        return WebClient.builder()
                .baseUrl(properties.baseUrl())
                .clientConnector(new ReactorClientHttpConnector(httpClient))
                .codecs(codecs -> codecs.defaultCodecs().maxInMemorySize(1 << 20))
                .build();
    }

    /**
     * 下载产物的专用客户端。
     *
     * <p><b>为什么不复用上面那个：</b> 它的单行缓冲上限是 1 MB，而生成出来的
     * PNG 很容易超过这个数。超限时抛的是 {@code DataBufferLimitException}，
     * 它继承 {@code IllegalStateException}——现有 {@code AgentClient.call()}
     * 会把这类异常误翻译成“调用超时”，排查方向会被完全带偏。
     *
     * <p>单独一条链路还有一个好处：不必为了下载图片把 JSON/SSE 的缓冲上限
     * 全局抬高，那会掩盖“上游返回了异常大的 JSON”这种真正的问题。
     */
    @Bean
    WebClient agentArtifactWebClient(AgentProperties properties, ArtifactProperties artifacts) {
        HttpClient httpClient = HttpClient.create()
                .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, properties.connectTimeoutMs());

        return WebClient.builder()
                .baseUrl(properties.baseUrl())
                .clientConnector(new ReactorClientHttpConnector(httpClient))
                .codecs(codecs -> codecs.defaultCodecs()
                        .maxInMemorySize(Math.toIntExact(artifacts.maxBytes())))
                .build();
    }
}
