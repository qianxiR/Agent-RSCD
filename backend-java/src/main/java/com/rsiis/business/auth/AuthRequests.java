package com.rsiis.business.auth;

import jakarta.validation.constraints.NotBlank;

/**
 * 认证相关请求体集合。
 */
public final class AuthRequests {

    private AuthRequests() {
    }

    public record MfaSetupRequest(
            @com.fasterxml.jackson.annotation.JsonAlias("setupToken")
            @NotBlank(message = "setup token 不能为空") String setupToken) {
    }

    public record MfaConfirmRequest(
            @com.fasterxml.jackson.annotation.JsonAlias("setupToken")
            @NotBlank(message = "setup token 不能为空") String setupToken,
            @NotBlank(message = "验证码不能为空") String code) {
    }

    public record MfaVerifyRequest(
            @com.fasterxml.jackson.annotation.JsonAlias("mfaToken")
            @NotBlank(message = "mfa token 不能为空") String mfaToken,
            @NotBlank(message = "验证码不能为空") String code) {
    }

    public record RefreshRequest(
            @com.fasterxml.jackson.annotation.JsonAlias("refreshToken")
            @NotBlank(message = "refresh token 不能为空") String refreshToken) {
    }

    public record LogoutRequest(
            @com.fasterxml.jackson.annotation.JsonAlias("refreshToken") String refreshToken) {
    }
}
