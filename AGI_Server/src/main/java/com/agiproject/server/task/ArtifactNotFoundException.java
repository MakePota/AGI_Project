package com.agiproject.server.task;

/**
 * 请求的产物不存在，或者不属于这个任务。
 *
 * <p><b>不要复用 {@link java.util.NoSuchElementException}。</b> 那个异常在
 * {@code GlobalExceptionHandler} 里的标题写死是“任务不存在”；产物丢了却提示
 * “任务不存在”，会让用户去查一个根本没问题的东西。
 */
public class ArtifactNotFoundException extends RuntimeException {

    public ArtifactNotFoundException(String message) {
        super(message);
    }
}
