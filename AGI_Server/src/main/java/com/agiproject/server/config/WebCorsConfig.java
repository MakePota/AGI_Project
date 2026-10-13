package com.agiproject.server.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * 允许前端开发服务器跨源访问本服务。
 *
 * <p><b>为什么不用 {@code allowedOrigins("*")}。</b> 那是最省事也最危险的一种写法：
 * 浏览器里的任何网页都能向 {@code http://127.0.0.1:8090} 发请求——而本服务
 * **没有认证**，它背后是能操作 Steam 账号、读本地文件的 Agent。也就是说，
 * 你随便打开一个恶意页面，对方就能创建任务、并在自己的请求里顺手把审批也批掉。
 * 「服务只绑在 127.0.0.1」挡不住这个：请求是从**你的浏览器**发出的。
 *
 * <p>这里限定为**本机来源**（端口用通配符）。这样 HBuilderX 的开发服务器
 * 无论分到哪个端口都能用，而远端站点（Origin 是 https://某站）一律被拒。
 * 这正是我们需要的安全边界：允许本机的任意开发端口，不允许任何外部来源。
 */
@Configuration
public class WebCorsConfig implements WebMvcConfigurer {

    @Override
    public void addCorsMappings(CorsRegistry registry) {
        registry.addMapping("/api/**")
                .allowedOriginPatterns("http://localhost:[*]", "http://127.0.0.1:[*]")
                .allowedMethods("GET", "POST", "OPTIONS")
                .allowedHeaders("*")
                // 预检结果缓存 1 小时，避免每个请求前面都多一次 OPTIONS 往返。
                .maxAge(3600);
    }
}
