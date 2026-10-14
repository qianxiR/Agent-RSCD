package com.rsiis.business.client;

/**
 * Python 内部接口非 2xx 响应的透传异常。
 *
 * status/body 携带 Python 返回的原始状态码与响应体,
 * 由全局异常处理器原样转给前端 (保留「会话不存在」等业务错误语义)。
 */
public class InternalApiException extends RuntimeException {

    private final int status;
    private final String body;

    public InternalApiException(int status, String body) {
        super("Python 内部接口返回 " + status);
        this.status = status;
        this.body = body;
    }

    public int status() {
        return status;
    }

    public String body() {
        return body;
    }
}
