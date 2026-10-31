package com.rsiis.business.auth;

import java.util.LinkedHashMap;
import java.util.Map;

import org.springframework.http.MediaType;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 当前用户与权限 (/api/v1/me)。
 *
 * 前端路由守卫/菜单显隐使用；后端仍以方法级权限为准。
 */
@RestController
@RequestMapping("/api/v1/me")
public class MeController {

    private final AuthService authService;

    public MeController(AuthService authService) {
        this.authService = authService;
    }

    @GetMapping(produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> me(Authentication authentication) {
        if (authentication == null || !authentication.isAuthenticated()
                || "anonymousUser".equals(authentication.getPrincipal())) {
            throw AuthException.unauthorized("UNAUTHORIZED", "未登录");
        }
        var user = authService.me(authentication.getName());
        Map<String, Object> userMap = new LinkedHashMap<>();
        userMap.put("user_id", user.id());
        userMap.put("org_id", user.orgId());
        userMap.put("username", user.username());
        userMap.put("email", user.email());
        userMap.put("role", user.role());
        userMap.put("status", user.status());
        userMap.put("mfa_enabled", user.mfaEnabled());
        userMap.put("roles", user.roles());
        userMap.put("permissions", user.permissions());
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "success");
        body.put("user", userMap);
        return body;
    }
}
