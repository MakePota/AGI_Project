package com.agiproject.server.web;

import com.agiproject.server.agent.AgentUnavailableException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ProblemDetail;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.multipart.MaxUploadSizeExceededException;
import org.springframework.web.servlet.resource.NoResourceFoundException;

import java.net.URI;
import java.util.NoSuchElementException;
import java.util.stream.Collectors;

/**
 * 把异常统一翻成 HTTP 响应。
 *
 * <p><b>为什么这件事值得单独一层：</b> 没有它，异常会变成 Spring 默认的
 * 500 白页或一段 HTML，前端拿不到可判断的结构；而错误路径的形态不统一，
 * 恰恰是前后端联调里最耗时间的地方。
 *
 * <p><b>用 Spring 自带的 {@link ProblemDetail}（RFC 7807），不自造
 * {@code {code, message, data}} 信封。</b> 理由是标准：客户端、网关、监控工具
 * 都认这个形状，而且不必让每个成功响应也套一层壳——成功的响应应该是它本来的样子。
 * 如果将来确实需要统一信封（有些团队有硬性规范），改这里一处即可。
 *
 * <p>状态码的分配原则是**让调用方一眼知道该怎么办**：
 * <ul>
 *   <li>400 —— 你的请求有问题（参数、JSON 格式）</li>
 *   <li>404 —— 那个任务不存在</li>
 *   <li>409 —— 状态冲突，先重新拉一次状态再决定</li>
 *   <li>503 —— 不是你的错，是智能体服务不可用</li>
 * </ul>
 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    @ExceptionHandler(NoSuchElementException.class)
    ProblemDetail handleNotFound(NoSuchElementException ex) {
        return problem(HttpStatus.NOT_FOUND, "任务不存在", ex.getMessage());
    }

    @ExceptionHandler(IllegalStateException.class)
    ProblemDetail handleConflict(IllegalStateException ex) {
        // 最典型的场景：对已经结束、或者已经被处理过的任务再提交一次审批。
        // 回 409 而不是 200，是为了让前端知道"你的状态过期了，重新拉一次"，
        // 而不是以为这次审批生效了。
        return problem(HttpStatus.CONFLICT, "状态冲突", ex.getMessage());
    }

    @ExceptionHandler(AgentUnavailableException.class)
    ProblemDetail handleAgentUnavailable(AgentUnavailableException ex) {
        // 这条与"任务失败"是两回事：前者是环境问题（服务没起、网络不通），
        // 后者是任务的结局。混成一个 500 会让排查方向完全跑偏。
        log.error("智能体服务不可用：{}", ex.getMessage());
        return problem(HttpStatus.SERVICE_UNAVAILABLE, "智能体服务不可用",
                ex.getMessage() + "（请确认 AGI_Agent 已在运行）");
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    ProblemDetail handleValidation(MethodArgumentNotValidException ex) {
        String detail = ex.getBindingResult().getFieldErrors().stream()
                .map(error -> error.getField() + ": " + error.getDefaultMessage())
                .collect(Collectors.joining("；"));
        return problem(HttpStatus.BAD_REQUEST, "请求参数不合法", detail);
    }

    @ExceptionHandler(HttpMessageNotReadableException.class)
    ProblemDetail handleUnreadable(HttpMessageNotReadableException ex) {
        // 请求体不是合法 JSON、或者字段类型对不上时走这里。
        // 不处理的话它会变成 500——而真正的问题出在客户端，报 500 会把排查方向带偏。
        return problem(HttpStatus.BAD_REQUEST, "请求体无法解析",
                "请确认正文是合法 JSON，且字段类型正确");
    }

    @ExceptionHandler(NoResourceFoundException.class)
    ProblemDetail handleNoResource(NoResourceFoundException ex) {
        // 请求了一个没有映射的路径。不处理的话会落到下面的兜底变成 500，
        // 而"路径写错了"是 404——报 500 会让人以为服务出了问题。
        return problem(HttpStatus.NOT_FOUND, "接口不存在", "请检查请求路径：" + ex.getResourcePath());
    }

    @ExceptionHandler(IllegalArgumentException.class)
    ProblemDetail handleBadArgument(IllegalArgumentException ex) {
        // 调用方给的东西不对：传了不是图片的文件、空文件，或者 Agent 那边判定的入参问题。
        return problem(HttpStatus.BAD_REQUEST, "请求内容不合法", ex.getMessage());
    }

    @ExceptionHandler(MaxUploadSizeExceededException.class)
    ProblemDetail handleTooLarge(MaxUploadSizeExceededException ex) {
        // 超过 Spring 自己那道闸（见 application.yml 的 multipart 上限）。
        // 走到这里说明连 Agent 都没看到这个请求——所以要说清是本服务拒的、以及为什么。
        return problem(HttpStatus.PAYLOAD_TOO_LARGE, "图片太大",
                "上传的图片超过了本服务接收的上限（8MB）");
    }

    @ExceptionHandler(Exception.class)
    ProblemDetail handleUnexpected(Exception ex) {
        // 兜底必须留痕：用户只看到"服务器内部错误"，而这行日志是唯一的线索。
        log.error("未预期的异常", ex);
        // **响应体里不写异常类名和原始信息**：那会把内部结构（类名、SQL 片段、
        // 甚至连接串）暴露出去，而这些内容在日志里已经完整保留，排查够用。
        return problem(HttpStatus.INTERNAL_SERVER_ERROR, "服务器内部错误", "请查看服务端日志");
    }

    private ProblemDetail problem(HttpStatus status, String title, String detail) {
        ProblemDetail problem = ProblemDetail.forStatusAndDetail(status, detail == null ? "" : detail);
        problem.setTitle(title);
        problem.setType(URI.create("about:blank"));
        return problem;
    }
}
