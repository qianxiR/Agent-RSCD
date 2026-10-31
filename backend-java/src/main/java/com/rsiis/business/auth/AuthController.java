package com.rsiis.business.auth;

import java.util.LinkedHashMap;
import java.util.Map;

import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;

/**
 * 认证路由 (/api/v1/auth)。
 *
 * 登录分三段:
 *  1. POST /login             用户名口令 → 直接 tokens / mfa_required / mfa_setup_required
 *  2. POST /mfa/setup         首次绑定: 返回 secret + otpauth URI + 恢复码
 *     POST /mfa/setup/confirm 校验 6 位码 → 启用 MFA + tokens
 *     POST /mfa/verify        已绑定: 校验 6 位码 → tokens
 *  3. POST /refresh           旋转 refresh token
 *     POST /logout            撤销 refresh token
 */
@RestController
@RequestMapping("/api/v1/auth")
public class AuthController {

    private final AuthService authService;

    public AuthController(AuthService authService) {
        this.authService = authService;
    }

    @PostMapping(value = "/login", consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> login(@Valid @RequestBody LoginRequest req,
                                     HttpServletRequest request) {
        var result = authService.login(req.username(), req.password(), request);
        return loginResponse(result);
    }

    @PostMapping(value = "/mfa/setup", produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> setupMfa(@Valid @RequestBody AuthRequests.MfaSetupRequest req,
                                        HttpServletRequest request) {
        var result = authService.setupMfa(req.setupToken(), request);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "success");
        body.put("secret", result.secret());
        body.put("otpauth_uri", result.otpauthUri());
        body.put("recovery_codes", result.recoveryCodes());
        return body;
    }

    @PostMapping(value = "/mfa/setup/confirm", produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> confirmMfaSetup(@Valid @RequestBody AuthRequests.MfaConfirmRequest req,
                                               HttpServletRequest request) {
        return loginResponse(authService.confirmMfaSetup(req.setupToken(), req.code(), request));
    }

    @PostMapping(value = "/mfa/verify", produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> verifyMfa(@Valid @RequestBody AuthRequests.MfaVerifyRequest req,
                                         HttpServletRequest request) {
        return loginResponse(authService.verifyMfa(req.mfaToken(), req.code(), request));
    }

    @PostMapping(value = "/refresh", produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> refresh(@Valid @RequestBody AuthRequests.RefreshRequest req,
                                       HttpServletRequest request) {
        return tokenResponse(authService.refresh(req.refreshToken(), request));
    }

    @PostMapping(value = "/logout", produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> logout(@RequestBody(required = false) AuthRequests.LogoutRequest req) {
        if (req != null) {
            authService.logout(req.refreshToken());
        }
        return Map.of("status", "success");
    }

    private Map<String, Object> loginResponse(AuthService.LoginResult result) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", result.status());
        if (result.mfaToken() != null) {
            body.put("mfa_token", result.mfaToken());
        }
        if (result.setupToken() != null) {
            body.put("setup_token", result.setupToken());
        }
        if (result.tokens() != null) {
            body.putAll(tokenFields(result.tokens()));
        }
        if (result.user() != null) {
            body.put("user", userMap(result.user()));
        }
        return body;
    }

    private Map<String, Object> tokenResponse(RefreshTokenService.TokenPair pair) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "success");
        body.putAll(tokenFields(pair));
        return body;
    }

    private Map<String, Object> tokenFields(RefreshTokenService.TokenPair pair) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("access_token", pair.accessToken());
        body.put("refresh_token", pair.refreshToken());
        body.put("token_type", "Bearer");
        body.put("expires_in", pair.accessExpiresInSeconds());
        body.put("refresh_expires_in", pair.refreshExpiresInSeconds());
        return body;
    }

    private Map<String, Object> userMap(AuthService.UserView user) {
        Map<String, Object> map = new LinkedHashMap<>();
        map.put("user_id", user.id());
        map.put("org_id", user.orgId());
        map.put("username", user.username());
        map.put("email", user.email());
        map.put("role", user.role());
        map.put("status", user.status());
        map.put("mfa_enabled", user.mfaEnabled());
        map.put("roles", user.roles());
        map.put("permissions", user.permissions());
        return map;
    }

    @ExceptionHandler(AuthException.class)
    public ResponseEntity<Map<String, Object>> handleAuth(AuthException e) {
        return ResponseEntity.status(e.status()).body(Map.of(
                "status", "error",
                "code", e.code(),
                "msg", e.getMessage()
        ));
    }
}
