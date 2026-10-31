package com.rsiis.business.admin;

import java.util.Map;

import org.springframework.http.MediaType;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.rsiis.business.identity.IdentityRepository;

/**
 * 管理端审计日志 (/api/v1/admin/audit)。
 *
 * 权限: audit:read。
 * 边界: 只读，不支持修改/删除；导出后续单独实现。
 */
@RestController
@RequestMapping("/api/v1/admin/audit")
@PreAuthorize("hasAuthority('audit:read')")
public class AdminAuditController {

    private final IdentityRepository repo;

    public AdminAuditController(IdentityRepository repo) {
        this.repo = repo;
    }

    @GetMapping(produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> list(@RequestParam(required = false) String actor,
                                    @RequestParam(required = false) String action,
                                    @RequestParam(required = false) String from,
                                    @RequestParam(required = false) String to,
                                    @RequestParam(defaultValue = "100") int limit) {
        var rows = repo.listAudit(actor, action, from, to, limit);
        return Map.of("status", "success", "count", rows.size(), "logs", rows);
    }
}
