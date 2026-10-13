package com.agiproject.server;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

/**
 * AGI_Server 的入口。
 *
 * <p>这个服务负责三件事：用户与任务数据的持久化、对前端的 API、以及把请求转给
 * {@code AGI_Agent}。**它不做任何智能体推理**——计划、工具编排、审批判定都在
 * Agent 那边（ADR 0014）。
 *
 * <p>{@code @ConfigurationPropertiesScan} 让 {@code @ConfigurationProperties}
 * 标注的配置类被自动发现注册；不写它的话 {@code agi.agent.*} 那几项读不进来，
 * 而症状是"配置看起来写了却没生效"。
 */
@SpringBootApplication
@ConfigurationPropertiesScan
public class ServerApplication {

    public static void main(String[] args) {
        SpringApplication.run(ServerApplication.class, args);
    }
}
