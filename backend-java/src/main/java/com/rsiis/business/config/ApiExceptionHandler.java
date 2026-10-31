package com.rsiis.business.config;

import java.util.Map;

import org.springframework.dao.DuplicateKeyException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import com.rsiis.business.auth.AuthException;

/**
 * 业务异常 → 统一 JSON。
 *
 * 覆盖: AuthException / 方法级权限拒绝 / 参数校验 / 唯一键冲突。
 * 边界: 不吞未知异常（交给 Spring 默认 500），避免掩盖真实 bug。
 */
@RestControllerAdvice
public class ApiExceptionHandler {

    @ExceptionHandler(AuthException.class)
    public ResponseEntity<Map<String, Object>> auth(AuthException e) {
        return ResponseEntity.status(e.status()).body(Map.of(
                "status", "error",
                "code", e.code(),
                "msg", e.getMessage() == null ? "" : e.getMessage()
        ));
    }

    @ExceptionHandler(AccessDeniedException.class)
    public ResponseEntity<Map<String, Object>> denied(AccessDeniedException e) {
        return ResponseEntity.status(HttpStatus.FORBIDDEN).body(Map.of(
                "status", "error",
                "code", "FORBIDDEN",
                "msg", "无权限"
        ));
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<Map<String, Object>> validation(MethodArgumentNotValidException e) {
        String msg = e.getBindingResult().getFieldErrors().stream()
                .findFirst()
                .map(err -> err.getDefaultMessage() == null ? "参数不合法" : err.getDefaultMessage())
                .orElse("参数不合法");
        return ResponseEntity.badRequest().body(Map.of(
                "status", "error",
                "code", "VALIDATION",
                "msg", msg
        ));
    }

    @ExceptionHandler(DuplicateKeyException.class)
    public ResponseEntity<Map<String, Object>> duplicate(DuplicateKeyException e) {
        return ResponseEntity.status(HttpStatus.CONFLICT).body(Map.of(
                "status", "error",
                "code", "CONFLICT",
                "msg", "数据已存在（用户名/邮箱/角色 code 重复）"
        ));
    }
}
