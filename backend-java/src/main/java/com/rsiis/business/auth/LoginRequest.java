package com.rsiis.business.auth;

import jakarta.validation.constraints.NotBlank;

/**
 * 登录请求体。
 *
 * @param username 用户名
 * @param password 明文口令 (服务端 BCrypt 比对)
 */
public record LoginRequest(
        @NotBlank(message = "用户名不能为空") String username,
        @NotBlank(message = "口令不能为空") String password
) {
}
