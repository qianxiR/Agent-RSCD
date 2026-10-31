package com.rsiis.business.identity;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

/**
 * 身份/RBAC/审计/Refresh Token 轻量仓储。
 *
 * 入参: 用户、角色、权限、Token、审计数据
 * 处理: 使用 JdbcTemplate 执行参数化 SQL; 新表与 users/project 扩列均由 schema.sql 幂等维护
 * 出参: record/Map; 不返回 password_hash 给管理端列表
 * 边界: 单组织一期固定 org_default; 项目级角色保留 project_id 字段, 后续再启用
 */
@Repository
public class IdentityRepository {

    public static final String DEFAULT_ORG_ID = "org_default";

    private final JdbcTemplate jdbc;

    public IdentityRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    // ==================== 用户 ====================

    public record UserRow(
            String id, String orgId, String username, String email, String passwordHash,
            String role, String status, boolean mfaEnabled, String mfaSecret,
            String mfaRecoveryCodes, OffsetDateTime lastLoginAt, OffsetDateTime createdAt) {
    }

    private static final RowMapper<UserRow> USER_MAPPER = (rs, i) -> new UserRow(
            rs.getString("id"),
            rs.getString("org_id"),
            rs.getString("username"),
            rs.getString("email"),
            rs.getString("password_hash"),
            rs.getString("role"),
            rs.getString("status"),
            rs.getBoolean("mfa_enabled"),
            rs.getString("mfa_secret"),
            rs.getString("mfa_recovery_codes"),
            rs.getObject("last_login_at", OffsetDateTime.class),
            rs.getObject("created_at", OffsetDateTime.class)
    );

    private static final String USER_COLUMNS =
            "id, org_id, username, email, password_hash, role, status, mfa_enabled, " +
            "mfa_secret, mfa_recovery_codes, last_login_at, created_at";

    public Optional<UserRow> findByUsername(String username) {
        List<UserRow> rows = jdbc.query(
                "SELECT " + USER_COLUMNS + " FROM users WHERE lower(username) = lower(?)",
                USER_MAPPER, username);
        return rows.stream().findFirst();
    }

    public Optional<UserRow> findById(String id) {
        List<UserRow> rows = jdbc.query(
                "SELECT " + USER_COLUMNS + " FROM users WHERE id = ?", USER_MAPPER, id);
        return rows.stream().findFirst();
    }

    public List<UserRow> listUsers(String keyword, String status, int limit) {
        StringBuilder sql = new StringBuilder("SELECT " + USER_COLUMNS + " FROM users WHERE 1=1");
        List<Object> args = new ArrayList<>();
        if (keyword != null && !keyword.isBlank()) {
            sql.append(" AND (lower(username) LIKE ? OR lower(coalesce(email,'')) LIKE ?)");
            String like = "%" + keyword.trim().toLowerCase() + "%";
            args.add(like);
            args.add(like);
        }
        if (status != null && !status.isBlank()) {
            sql.append(" AND status = ?");
            args.add(status);
        }
        sql.append(" ORDER BY created_at DESC LIMIT ?");
        args.add(Math.max(1, Math.min(limit, 200)));
        return jdbc.query(sql.toString(), USER_MAPPER, args.toArray());
    }

    public long countUsers() {
        Long n = jdbc.queryForObject("SELECT count(*) FROM users", Long.class);
        return n == null ? 0 : n;
    }

    public long countActiveUsers() {
        Long n = jdbc.queryForObject(
                "SELECT count(*) FROM users WHERE status = 'active'", Long.class);
        return n == null ? 0 : n;
    }

    public void insertUser(String id, String username, String email, String passwordHash,
                           String legacyRole, String orgId) {
        jdbc.update("""
                INSERT INTO users (id, org_id, username, email, password_hash, role, status,
                                   mfa_enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'active', false, now(), now())
                """, id, orgId == null ? DEFAULT_ORG_ID : orgId, username, email, passwordHash,
                legacyRole == null ? "user" : legacyRole);
    }

    public void updateStatus(String userId, String status) {
        jdbc.update("UPDATE users SET status = ?, updated_at = now() WHERE id = ?", status, userId);
    }

    public void updatePassword(String userId, String passwordHash) {
        jdbc.update("UPDATE users SET password_hash = ?, updated_at = now() WHERE id = ?",
                passwordHash, userId);
    }

    public void updateEmail(String userId, String email) {
        jdbc.update("UPDATE users SET email = ?, updated_at = now() WHERE id = ?", email, userId);
    }

    public void touchLogin(String userId) {
        jdbc.update("UPDATE users SET last_login_at = now() WHERE id = ?", userId);
    }

    public void setMfaSecret(String userId, String encryptedSecret) {
        jdbc.update("UPDATE users SET mfa_secret = ?, updated_at = now() WHERE id = ?",
                encryptedSecret, userId);
    }

    public void setMfaEnabled(String userId, boolean enabled, String recoveryCodes) {
        jdbc.update("UPDATE users SET mfa_enabled = ?, mfa_recovery_codes = ?, updated_at = now() WHERE id = ?",
                enabled, recoveryCodes, userId);
    }

    // ==================== 角色/权限 ====================

    public record RoleRow(String id, String code, String name, String scope, boolean builtin) {
    }

    public record PermissionRow(String code, String name, String module) {
    }

    private static final RowMapper<RoleRow> ROLE_MAPPER = (rs, i) -> new RoleRow(
            rs.getString("id"), rs.getString("code"), rs.getString("name"),
            rs.getString("scope"), rs.getBoolean("builtin"));

    public List<RoleRow> listRoles() {
        return jdbc.query("SELECT id, code, name, scope, builtin FROM roles ORDER BY builtin DESC, code",
                ROLE_MAPPER);
    }

    public Optional<RoleRow> findRoleByCode(String code) {
        return jdbc.query("SELECT id, code, name, scope, builtin FROM roles WHERE code = ?",
                ROLE_MAPPER, code).stream().findFirst();
    }

    public List<PermissionRow> listPermissions() {
        return jdbc.query("SELECT code, name, module FROM permissions ORDER BY module, code",
                (rs, i) -> new PermissionRow(rs.getString("code"), rs.getString("name"),
                        rs.getString("module")));
    }

    public List<String> roleCodesOf(String userId) {
        return jdbc.queryForList("""
                SELECT DISTINCT r.code
                FROM roles r JOIN user_roles ur ON ur.role_id = r.id
                WHERE ur.user_id = ? AND ur.project_id IS NULL
                """, String.class, userId);
    }

    public List<String> permissionsOf(String userId) {
        return jdbc.queryForList("""
                SELECT DISTINCT p.code
                FROM permissions p
                JOIN role_permissions rp ON rp.permission_code = p.code
                JOIN user_roles ur ON ur.role_id = rp.role_id
                WHERE ur.user_id = ? AND ur.project_id IS NULL
                """, String.class, userId);
    }

    public boolean isAdmin(String userId) {
        List<String> roles = roleCodesOf(userId);
        return roles.contains("system_admin");
    }

    public List<String> permissionCodesOfRole(String roleId) {
        return jdbc.queryForList(
                "SELECT permission_code FROM role_permissions WHERE role_id = ? ORDER BY permission_code",
                String.class, roleId);
    }

    public void replaceUserRoles(String userId, List<String> roleCodes) {
        jdbc.update("DELETE FROM user_roles WHERE user_id = ? AND project_id IS NULL", userId);
        for (String code : roleCodes) {
            jdbc.update("""
                    INSERT INTO user_roles (user_id, role_id, org_id)
                    SELECT ?, id, ? FROM roles WHERE code = ?
                    """, userId, DEFAULT_ORG_ID, code);
        }
    }

    public void replaceRolePermissions(String roleId, List<String> permissionCodes) {
        jdbc.update("DELETE FROM role_permissions WHERE role_id = ?", roleId);
        for (String code : permissionCodes) {
            jdbc.update("""
                    INSERT INTO role_permissions (role_id, permission_code)
                    SELECT ?, ? WHERE EXISTS (SELECT 1 FROM permissions WHERE code = ?)
                    """, roleId, code, code);
        }
    }

    public void insertRoleIfAbsent(String id, String code, String name, String scope, boolean builtin) {
        jdbc.update("""
                INSERT INTO roles (id, code, name, scope, builtin)
                VALUES (?, ?, ?, ?, ?) ON CONFLICT (code) DO NOTHING
                """, id, code, name, scope, builtin);
    }

    public void insertPermissionIfAbsent(String code, String name, String module) {
        jdbc.update("""
                INSERT INTO permissions (code, name, module)
                VALUES (?, ?, ?) ON CONFLICT (code) DO NOTHING
                """, code, name, module);
    }

    public void addRolePermissionIfAbsent(String roleId, String permissionCode) {
        jdbc.update("""
                INSERT INTO role_permissions (role_id, permission_code)
                VALUES (?, ?) ON CONFLICT DO NOTHING
                """, roleId, permissionCode);
    }

    // ==================== Refresh Token ====================

    public record RefreshRow(String id, String userId, String tokenHash, String familyId,
                             OffsetDateTime expiresAt, OffsetDateTime revokedAt) {
    }

    public void insertRefreshToken(String id, String userId, String tokenHash, String familyId,
                                   OffsetDateTime expiresAt, String userAgent, String ip) {
        jdbc.update("""
                INSERT INTO refresh_tokens (id, user_id, token_hash, family_id, expires_at, user_agent, ip)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """, id, userId, tokenHash, familyId, expiresAt, userAgent, ip);
    }

    public Optional<RefreshRow> findRefreshByHash(String tokenHash) {
        List<RefreshRow> rows = jdbc.query("""
                SELECT id, user_id, token_hash, family_id, expires_at, revoked_at
                FROM refresh_tokens WHERE token_hash = ?
                """, (rs, i) -> new RefreshRow(
                rs.getString("id"), rs.getString("user_id"), rs.getString("token_hash"),
                rs.getString("family_id"),
                rs.getObject("expires_at", OffsetDateTime.class),
                rs.getObject("revoked_at", OffsetDateTime.class)), tokenHash);
        return rows.stream().findFirst();
    }

    public void revokeRefreshToken(String id) {
        jdbc.update("UPDATE refresh_tokens SET revoked_at = now() WHERE id = ? AND revoked_at IS NULL", id);
    }

    public void revokeRefreshFamily(String familyId) {
        jdbc.update("UPDATE refresh_tokens SET revoked_at = now() WHERE family_id = ? AND revoked_at IS NULL",
                familyId);
    }

    public void revokeAllForUser(String userId) {
        jdbc.update("UPDATE refresh_tokens SET revoked_at = now() WHERE user_id = ? AND revoked_at IS NULL",
                userId);
    }

    // ==================== 审计 ====================

    public void insertAudit(String orgId, String actorId, String actorName, String action,
                            String resourceType, String resourceId, String result,
                            String beforeJson, String afterJson, String ip, String userAgent,
                            String requestId) {
        jdbc.update("""
                INSERT INTO audit_logs (org_id, actor_id, actor_name, action, resource_type,
                                        resource_id, result, before_json, after_json, ip,
                                        user_agent, request_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, orgId == null ? DEFAULT_ORG_ID : orgId, actorId, actorName, action,
                resourceType, resourceId, result, beforeJson, afterJson, ip, userAgent, requestId);
    }

    public List<Map<String, Object>> listAudit(String actor, String action, String from, String to,
                                                int limit) {
        StringBuilder sql = new StringBuilder("""
                SELECT id, actor_id, actor_name, action, resource_type, resource_id, result,
                       before_json, after_json, ip, user_agent, request_id, created_at
                FROM audit_logs WHERE 1=1
                """);
        List<Object> args = new ArrayList<>();
        if (actor != null && !actor.isBlank()) {
            sql.append(" AND (actor_id = ? OR lower(coalesce(actor_name,'')) LIKE ?)");
            args.add(actor);
            args.add("%" + actor.toLowerCase() + "%");
        }
        if (action != null && !action.isBlank()) {
            sql.append(" AND action = ?");
            args.add(action);
        }
        if (from != null && !from.isBlank()) {
            sql.append(" AND created_at >= ?::timestamptz");
            args.add(from);
        }
        if (to != null && !to.isBlank()) {
            sql.append(" AND created_at <= ?::timestamptz");
            args.add(to);
        }
        sql.append(" ORDER BY created_at DESC LIMIT ?");
        args.add(Math.max(1, Math.min(limit, 500)));
        return jdbc.query(sql.toString(), (rs, i) -> {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("id", rs.getLong("id"));
            row.put("actor_id", rs.getString("actor_id"));
            row.put("actor_name", rs.getString("actor_name"));
            row.put("action", rs.getString("action"));
            row.put("resource_type", rs.getString("resource_type"));
            row.put("resource_id", rs.getString("resource_id"));
            row.put("result", rs.getString("result"));
            row.put("before_json", rs.getString("before_json"));
            row.put("after_json", rs.getString("after_json"));
            row.put("ip", rs.getString("ip"));
            row.put("user_agent", rs.getString("user_agent"));
            row.put("request_id", rs.getString("request_id"));
            row.put("created_at", rs.getObject("created_at", OffsetDateTime.class));
            return row;
        }, args.toArray());
    }
}
