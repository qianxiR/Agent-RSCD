package com.rsiis.business.auth;

import org.springframework.http.HttpStatus;

/**
 * 认证/权限业务异常: Controller 统一转换为 JSON 错误响应。
 */
public class AuthException extends RuntimeException {

    private final HttpStatus status;
    private final String code;

    public AuthException(HttpStatus status, String code, String message) {
        super(message);
        this.status = status;
        this.code = code;
    }

    public HttpStatus status() {
        return status;
    }

    public String code() {
        return code;
    }

    public static AuthException badRequest(String code, String message) {
        return new AuthException(HttpStatus.BAD_REQUEST, code, message);
    }

    public static AuthException unauthorized(String code, String message) {
        return new AuthException(HttpStatus.UNAUTHORIZED, code, message);
    }

    public static AuthException forbidden(String code, String message) {
        return new AuthException(HttpStatus.FORBIDDEN, code, message);
    }
}
