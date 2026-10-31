package com.rsiis.business.admin;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import org.springframework.http.MediaType;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.Authentication;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.rsiis.business.audit.AuditService;
import com.rsiis.business.auth.AuthException;
import com.rsiis.business.auth.RefreshTokenService;
import com.rsiis.business.identity.IdentityRepository;
import com.rsiis.business.identity.RbacService;

import jakarta.servlet.http.HttpServletRequest;

/**
 * 管理端用户管理 (/api/v1/admin/users)。
 *
 * 权限: user:manage（system_admin 内置角色拥有）。
 * 功能: 列表、创建、修改状态/邮箱/角色、重置密码、撤销会话、软删除（禁用）。
 * 边界: 不返回 password_hash；禁用立即撤销 refresh token；所有写操作写审计。
 */
@RestController
@RequestMapping("/api/v1/admin/users")
@PreAuthorize("hasAuthority('user:manage')")
public class AdminUserController {

    private final IdentityRepository repo;
    private final RbacService rbac;
    private final PasswordEncoder passwordEncoder;
    private final RefreshTokenService refreshTokenService;
    private final AuditService audit;

    public AdminUserController(IdentityRepository repo, RbacService rbac,
                               PasswordEncoder passwordEncoder,
                               RefreshTokenService refreshTokenService, AuditService audit) {
        this.repo = repo;
        this.rbac = rbac;
        this.passwordEncoder = passwordEncoder;
        this.refreshTokenService = refreshTokenService;
        this.audit = audit;
    }

    public record CreateUserRequest(String username, String email, String password,
                                    @com.fasterxml.jackson.annotation.JsonAlias("roleCodes")
                                    List<String> roleCodes) {
    }

    public record UpdateUserRequest(String email, String status, String password,
                                    @com.fasterxml.jackson.annotation.JsonAlias("roleCodes")
                                    List<String> roleCodes) {
    }

    @GetMapping(produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> list(@RequestParam(required = false) String keyword,
                                    @RequestParam(required = false) String status,
                                    @RequestParam(defaultValue = "100") int limit) {
        var users = repo.listUsers(keyword, status, limit);
        List<Map<String, Object>> items = users.stream().map(this::userMap).toList();
        return Map.of("status", "success", "count", items.size(), "users", items);
    }

    @PostMapping(produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> create(@RequestBody CreateUserRequest req,
                                      Authentication auth, HttpServletRequest request) {
        if (req.username() == null || req.username().isBlank()) {
            throw AuthException.badRequest("USERNAME_REQUIRED", "用户名不能为空");
        }
        if (req.password() == null || req.password().length() < 12) {
            throw AuthException.badRequest("WEAK_PASSWORD", "口令至少 12 位");
        }
        repo.findByUsername(req.username()).ifPresent(u -> {
            throw new AuthException(org.springframework.http.HttpStatus.CONFLICT,
                    "USERNAME_EXISTS", "用户名已存在");
        });
        List<String> roles = normalizeRoles(req.roleCodes());
        String id = UUID.randomUUID().toString();
        repo.insertUser(id, req.username().trim(), blankToNull(req.email()),
                passwordEncoder.encode(req.password()), "user", IdentityRepository.DEFAULT_ORG_ID);
        repo.replaceUserRoles(id, roles);
        audit.record(actorId(auth), actorName(auth), "user.create", "user", id, "success",
                null, Map.of("username", req.username(), "roles", roles), request);
        return Map.of("status", "success", "user_id", id);
    }

    @PatchMapping(value = "/{id}", produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> update(@PathVariable String id,
                                      @RequestBody UpdateUserRequest req,
                                      Authentication auth, HttpServletRequest request) {
        var user = repo.findById(id).orElseThrow(
                () -> AuthException.badRequest("USER_NOT_FOUND", "用户不存在"));
        Map<String, Object> before = snapshot(user);
        if (req.email() != null) {
            repo.updateEmail(id, blankToNull(req.email()));
        }
        if (req.password() != null && !req.password().isBlank()) {
            if (req.password().length() < 12) {
                throw AuthException.badRequest("WEAK_PASSWORD", "口令至少 12 位");
            }
            repo.updatePassword(id, passwordEncoder.encode(req.password()));
        }
        if (req.status() != null && !req.status().isBlank()) {
            String status = req.status().toLowerCase();
            if (!List.of("active", "disabled").contains(status)) {
                throw AuthException.badRequest("INVALID_STATUS", "状态只能是 active/disabled");
            }
            repo.updateStatus(id, status);
            if ("disabled".equals(status)) {
                refreshTokenService.revokeAll(id);
            }
        }
        if (req.roleCodes() != null) {
            repo.replaceUserRoles(id, normalizeRoles(req.roleCodes()));
        }
        var updated = repo.findById(id).orElseThrow(
                () -> AuthException.badRequest("USER_NOT_FOUND", "用户不存在"));
        audit.record(actorId(auth), actorName(auth), "user.update", "user", id, "success",
                before, snapshot(updated), request);
        return Map.of("status", "success", "user", userMap(updated));
    }

    @PostMapping(value = "/{id}/revoke-sessions", produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> revokeSessions(@PathVariable String id, Authentication auth,
                                              HttpServletRequest request) {
        repo.findById(id).orElseThrow(
                () -> AuthException.badRequest("USER_NOT_FOUND", "用户不存在"));
        refreshTokenService.revokeAll(id);
        audit.record(actorId(auth), actorName(auth), "user.revoke_sessions", "user", id,
                "success", null, null, request);
        return Map.of("status", "success");
    }

    @DeleteMapping(value = "/{id}", produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> disable(@PathVariable String id, Authentication auth,
                                       HttpServletRequest request) {
        repo.findById(id).orElseThrow(
                () -> AuthException.badRequest("USER_NOT_FOUND", "用户不存在"));
        repo.updateStatus(id, "disabled");
        refreshTokenService.revokeAll(id);
        audit.record(actorId(auth), actorName(auth), "user.disable", "user", id,
                "success", null, Map.of("status", "disabled"), request);
        return Map.of("status", "success");
    }

    private List<String> normalizeRoles(List<String> roleCodes) {
        List<String> codes = roleCodes == null || roleCodes.isEmpty()
                ? List.of("viewer") : roleCodes;
        for (String code : codes) {
            if (repo.findRoleByCode(code).isEmpty()) {
                throw AuthException.badRequest("ROLE_NOT_FOUND", "角色不存在: " + code);
            }
        }
        return codes;
    }

    private Map<String, Object> userMap(IdentityRepository.UserRow user) {
        Map<String, Object> map = new LinkedHashMap<>();
        map.put("id", user.id());
        map.put("username", user.username());
        map.put("email", user.email());
        map.put("status", user.status());
        map.put("mfa_enabled", user.mfaEnabled());
        map.put("last_login_at", user.lastLoginAt());
        map.put("created_at", user.createdAt());
        map.put("roles", rbac.roleCodesOf(user.id()));
        return map;
    }

    private Map<String, Object> snapshot(IdentityRepository.UserRow user) {
        Map<String, Object> map = new LinkedHashMap<>();
        map.put("email", user.email());
        map.put("status", user.status());
        map.put("roles", rbac.roleCodesOf(user.id()));
        map.put("mfa_enabled", user.mfaEnabled());
        return map;
    }

    private String actorId(Authentication auth) {
        return auth == null ? null : auth.getName();
    }

    private String actorName(Authentication auth) {
        return auth == null || auth.getDetails() == null ? null : auth.getDetails().toString();
    }

    private String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value.trim();
    }
}
