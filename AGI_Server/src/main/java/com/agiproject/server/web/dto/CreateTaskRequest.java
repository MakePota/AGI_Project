package com.agiproject.server.web.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/**
 * 建任务的请求体。
 *
 * <p>校验放在这里（而不是等 Agent 报错）有两个好处：错误信息更贴用户
 * （"任务目标不能为空"而不是下游抛出的字段名），而且**不必为一次注定失败的
 * 调用去访问 Agent**。
 *
 * @param goal 要完成的事情，自然语言。长度上限与数据库列保持一致——
 *             两处不一致就会出现"校验通过但插入失败"。
 */
public record CreateTaskRequest(
        @NotBlank(message = "任务目标不能为空")
        @Size(max = 2000, message = "任务目标过长，最多 2000 字")
        String goal
) {
}
