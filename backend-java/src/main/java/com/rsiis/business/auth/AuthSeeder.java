package com.rsiis.business.auth;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationRunner;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.crypto.password.PasswordEncoder;

import com.rsiis.business.identity.IdentityRepository;

/**
 * 种子数据: 默认组织 / 权限 / 内置角色 / 管理员。
 *
 * 幂等:
 *  - 角色/权限已存在则跳过;
 *  - 管理员仅在 users 为空时创建;
 *  - 已有 admin 没有 system_admin 角色时补绑。
 *
 * 管理端强制 MFA: admin 初始 mfa_enabled=false，首次登录返回 mfa_setup_required，
 * 完成 TOTP 绑定后才能进入 /api/v1/admin/**。
 */
@Configuration
public class AuthSeeder {

    private static final Logger log = LoggerFactory.getLogger(AuthSeeder.class);

    private static final Map<String, String[]> PERMISSIONS = new LinkedHashMap<>();

    static {
        add("project:read", "查看项目", "project");
        add("project:create", "创建项目", "project");
        add("project:update", "修改项目", "project");
        add("project:delete", "删除/归档项目", "project");
        add("project:member", "管理项目成员", "project");
        add("dataset:read", "查看数据集", "dataset");
        add("dataset:upload", "上传数据集", "dataset");
        add("dataset:delete", "删除数据集", "dataset");
        add("dataset:publish", "发布数据集为图层", "dataset");
        add("layer:read", "查看图层", "layer");
        add("layer:publish", "发布图层", "layer");
        add("layer:update", "修改图层/样式", "layer");
        add("layer:delete", "删除图层", "layer");
        add("job:read", "查看任务", "job");
        add("job:create", "创建任务", "job");
        add("job:cancel", "取消任务", "job");
        add("job:retry", "重试任务", "job");
        add("artifact:read", "查看产物", "artifact");
        add("artifact:download", "下载产物", "artifact");
        add("artifact:publish", "产物发布为图层", "artifact");
        add("report:read", "查看报告", "report");
        add("report:create", "生成报告", "report");
        add("report:download", "下载报告", "report");
        add("user:manage", "用户管理", "admin");
        add("role:manage", "角色与权限管理", "admin");
        add("org:manage", "组织管理", "admin");
        add("system:config", "系统设置", "admin");
        add("audit:read", "审计日志", "admin");
        add("storage:manage", "存储与配额", "admin");
        add("model:manage", "模型配置", "admin");
    }

    private static void add(String code, String name, String module) {
        PERMISSIONS.put(code, new String[]{name, module});
    }

    @Bean
    public ApplicationRunner seedIdentity(IdentityRepository repo, PasswordEncoder encoder) {
        return args -> {
            for (var entry : PERMISSIONS.entrySet()) {
                repo.insertPermissionIfAbsent(entry.getKey(), entry.getValue()[0], entry.getValue()[1]);
            }

            seedRole(repo, "system_admin", "系统管理员", "system", allPermissions());
            seedRole(repo, "project_admin", "项目管理员", "project", List.of(
                    "project:read", "project:update", "project:member",
                    "dataset:read", "dataset:upload", "dataset:delete", "dataset:publish",
                    "layer:read", "layer:publish", "layer:update", "layer:delete",
                    "job:read", "job:create", "job:cancel", "job:retry",
                    "artifact:read", "artifact:download", "artifact:publish",
                    "report:read", "report:create", "report:download"));
            seedRole(repo, "analyst", "分析员", "project", List.of(
                    "project:read", "dataset:read", "dataset:upload", "layer:read",
                    "job:read", "job:create", "job:cancel",
                    "artifact:read", "artifact:download",
                    "report:read", "report:create", "report:download"));
            seedRole(repo, "viewer", "访客", "project", List.of(
                    "project:read", "dataset:read", "layer:read", "job:read",
                    "artifact:read", "artifact:download",
                    "report:read", "report:download"));

            if (repo.countUsers() == 0) {
                String id = UUID.randomUUID().toString();
                repo.insertUser(id, "admin", "admin@rsiis.local",
                        encoder.encode("admin123"), "admin", IdentityRepository.DEFAULT_ORG_ID);
                repo.replaceUserRoles(id, List.of("system_admin"));
                log.warn("[Auth] 已创建种子管理员 admin/admin123；首次登录需绑定 MFA，请立即修改口令");
            } else {
                repo.findByUsername("admin").ifPresent(admin -> {
                    if (repo.roleCodesOf(admin.id()).isEmpty()) {
                        repo.replaceUserRoles(admin.id(), List.of("system_admin"));
                        log.info("[Auth] 已为现有 admin 补绑 system_admin 角色");
                    }
                });
            }
            log.info("[Auth] RBAC 种子就绪: roles={}, permissions={}",
                    repo.listRoles().size(), repo.listPermissions().size());
        };
    }

    private void seedRole(IdentityRepository repo, String code, String name, String scope,
                          List<String> permissions) {
        String id = "role_" + code;
        repo.insertRoleIfAbsent(id, code, name, scope, true);
        var role = repo.findRoleByCode(code).orElseThrow();
        for (String permission : permissions) {
            repo.addRolePermissionIfAbsent(role.id(), permission);
        }
    }

    private List<String> allPermissions() {
        return List.copyOf(PERMISSIONS.keySet());
    }
}
