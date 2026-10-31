package com.rsiis.business.identity;

import java.util.List;

import org.springframework.stereotype.Service;

/**
 * RBAC 查询与角色分配。
 *
 * 入参: userId / roleCodes / permissionCodes
 * 处理: 读写 roles / permissions / role_permissions / user_roles; 单组织只处理 project_id IS NULL 的全局角色
 * 出参: 角色/权限列表或布尔判断
 * 边界: 权限最终以数据库为准; JWT 只携带身份, 每次请求由过滤器重新加载权限
 */
@Service
public class RbacService {

    private final IdentityRepository repo;

    public RbacService(IdentityRepository repo) {
        this.repo = repo;
    }

    public List<String> roleCodesOf(String userId) {
        return repo.roleCodesOf(userId);
    }

    public List<String> permissionsOf(String userId) {
        return repo.permissionsOf(userId);
    }

    public boolean isAdmin(String userId) {
        return repo.isAdmin(userId);
    }

    public void assignRoles(String userId, List<String> roleCodes) {
        repo.replaceUserRoles(userId, roleCodes == null ? List.of() : roleCodes);
    }

    public List<IdentityRepository.RoleRow> listRoles() {
        return repo.listRoles();
    }

    public List<IdentityRepository.PermissionRow> listPermissions() {
        return repo.listPermissions();
    }

    public List<String> permissionCodesOfRole(String roleId) {
        return repo.permissionCodesOfRole(roleId);
    }

    public void replaceRolePermissions(String roleId, List<String> permissionCodes) {
        repo.replaceRolePermissions(roleId, permissionCodes == null ? List.of() : permissionCodes);
    }

    /**
     * 判断角色是否包含某权限（供业务层做非注解式校验）。
     */
    public boolean hasPermission(String userId, String permissionCode) {
        return permissionsOf(userId).contains(permissionCode);
    }
}
