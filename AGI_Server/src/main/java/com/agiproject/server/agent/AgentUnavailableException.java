package com.agiproject.server.agent;

/**
 * 联系不上 {@code AGI_Agent}，或它自身出错了。
 *
 * <p>它对应 HTTP <b>503</b>，和"任务本身失败了"是两回事——这条区分很重要：
 * 前者是环境问题（服务没起、网络不通），后者是任务结局。
 * 项目里对这个区分有专门的规矩（ADR 0011：按"谁来修"分类），
 * 服务端这一层必须原样保留它，不能把两者都压成 500。
 */
public class AgentUnavailableException extends RuntimeException {

    public AgentUnavailableException(String message, Throwable cause) {
        super(message, cause);
    }

    public AgentUnavailableException(String message) {
        super(message);
    }
}
