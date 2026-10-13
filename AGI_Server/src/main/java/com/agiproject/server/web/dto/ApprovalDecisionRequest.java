package com.agiproject.server.web.dto;

import jakarta.validation.constraints.NotNull;

/**
 * 人工审批决定。
 *
 * <p><b>{@code approved} 用包装类型 {@code Boolean} 而不是 {@code boolean}，
 * 这是有意的。</b> 若用基本类型，请求里不带这个字段时 Jackson 会给它填
 * {@code false} —— 也就是"拒绝"，而且**没有任何报错**。一次手滑少传字段，
 * 结果是把用户的意图反转成拒绝执行。用 {@code Boolean} + {@code @NotNull}
 * 才能把"没传"和"传了 false"区分开。
 *
 * @param approved 是否批准
 * @param reason   拒绝原因，可空。会写进审批记录，便于事后追溯
 */
public record ApprovalDecisionRequest(
        @NotNull(message = "approved 不能为空，必须是 true 或 false")
        Boolean approved,
        String reason
) {
}
