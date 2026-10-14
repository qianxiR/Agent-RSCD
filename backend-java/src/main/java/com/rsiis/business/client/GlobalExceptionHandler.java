package com.rsiis.business.client;

import java.nio.charset.StandardCharsets;
import java.util.Map;

import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * 代理层异常 → HTTP 响应的统一转换。
 *
 * - InternalApiException: 原样透传 Python 的状态码与响应体 (保留业务错误语义)
 * - IllegalStateException (Python 不可达): 502 + 明确提示
 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(InternalApiException.class)
    public ResponseEntity<String> internalApi(InternalApiException e) {
        return ResponseEntity.status(e.status())
                .contentType(new MediaType(MediaType.APPLICATION_JSON,
                        StandardCharsets.UTF_8))
                .body(e.body());
    }

    @ExceptionHandler(IllegalStateException.class)
    public ResponseEntity<Map<String, String>> unreachable(IllegalStateException e) {
        return ResponseEntity.status(HttpStatus.BAD_GATEWAY)
                .body(Map.of("status", "error", "msg", e.getMessage()));
    }
}
