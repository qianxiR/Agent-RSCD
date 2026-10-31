package com.rsiis.business.admin;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import org.springframework.http.MediaType;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.rsiis.business.audit.AuditService;
import com.rsiis.business.auth.AuthException;
import com.rsiis.business.identity.IdentityRepository;
import com.rsiis.business.identity.RbacService;

import jakarta.servlet.http.HttpServletRequest;

/**
 * 管理端角色与权限 (/api/v1/admin/roles)。
 *
 * 权限: role:manage。
 * 边界: 内置角色只读; 自定义角色可创建并改权限; 所有写操作写审计。
 */
@RestController
@RequestMapping("/api/v1/admin/roles")
@PreAuthorize("hasAuthority('role:manage')")
public class AdminRoleController {

    private final IdentityRepository repo;
    private final RbacService rbac;
    private final AuditService audit;

    public AdminRoleController(IdentityRepository repo, RbacService rbac, AuditService audit) {
        this.repo = repo;
        this.rbac = rbac;
        this.audit = audit;
    }

    public record CreateRoleRequest(String code, String name, String scope,
                                    @com.fasterxml.jackson.annotation.JsonAlias("permissionCodes")
                                    List<String> permissionCodes) {
    }

    public record UpdatePermissionsRequest(
            @com.fasterxml.jackson.annotation.JsonAlias("permissionCodes")
            List<String> permissionCodes) {
    }

    @GetMapping(produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> list() {
        List<Map<String, Object>> roles = repo.listRoles().stream().map(role -> {
            Map<String, Object> map = new LinkedHashMap<>();
            map.put("id", role.id());
            map.put("code", role.code());
            map.put("name", role.name());
            map.put("scope", role.scope());
            map.put("builtin", role.builtin());
            map.put("permissions", repo.permissionCodesOfRole(role.id()));
            return map;
        }).toList();
        return Map.of("status", "success", "roles", roles);
    }

    @GetMapping(value = "/permissions", produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> permissions() {
        return Map.of("status", "success", "permissions", repo.listPermissions());
    }

    @PostMapping(produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> create(@RequestBody CreateRoleRequest req, Authentication auth,
                                      HttpServletRequest request) {
        if (req.code() == null || !req.code().matches("[a-z][a-z0-9_]{2,40}")) {
            throw AuthException.badRequest("INVALID_ROLE_CODE", "角色 code 只能是小写字母/数字/下划线");
        }
        if (req.name() == null || req.name().isBlank()) {
            throw AuthException.badRequest("ROLE_NAME_REQUIRED", "角色名称不能为空");
        }
        if (repo.findRoleByCode(req.code()).isPresent()) {
            throw new AuthException(org.springframework.http.HttpStatus.CONFLICT,
                    "ROLE_EXISTS", "角色已存在");
        }
        String id = UUID.randomUUID().toString();
        repo.insertRoleIfAbsent(id, req.code(), req.name().trim(),
                req.scope() == null ? "project" : req.scope(), false);
        repo.replaceRolePermissions(id, req.permissionCodes() == null ? List.of() : req.permissionCodes());
        audit.record(actorId(auth), actorName(auth), "role.create", "role", id, "success",
                null, Map.of("code", req.code(), "name", req.name()), request);
        return Map.of("status", "success", "role_id", id);
    }

    @PutMapping(value = "/{roleCode}/permissions", produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> updatePermissions(@PathVariable String roleCode,
                                                 @RequestBody UpdatePermissionsRequest req,
                                                 Authentication auth, HttpServletRequest request) {
        var role = repo.findRoleByCode(roleCode).orElseThrow(
                () -> AuthException.badRequest("ROLE_NOT_FOUND", "角色不存在"));
        if (role.builtin()) {
            throw AuthException.badRequest("ROLE_BUILTIN", "内置角色只读，请复制为自定义角色");
        }
        var before = repo.permissionCodesOfRole(role.id());
        rbac.replaceRolePermissions(role.id(), req.permissionCodes());
        audit.record(actorId(auth), actorName(auth), "role.permissions.update", "role", role.id(),
                "success", Map.of("permissions", before),
                Map.of("permissions", req.permissionCodes() == null ? List.of() : req.permissionCodes()),
                request);
        return Map.of("status", "success");
    }

    private String actorId(Authentication auth) {
        return auth == null ? null : auth.getName();
    }

    private String actorName(Authentication auth) {
        return auth == null || auth.getDetails() == null ? null : auth.getDetails().toString();
    }
}
