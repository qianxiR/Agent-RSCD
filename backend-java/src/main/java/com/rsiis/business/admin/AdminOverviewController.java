package com.rsiis.business.admin;

import java.util.LinkedHashMap;
import java.util.Map;

import org.springframework.http.MediaType;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.rsiis.business.identity.IdentityRepository;
import com.rsiis.business.project.ProjectEntity;
import com.rsiis.business.project.ProjectMapper;

/**
 * 管理端运营总览 (/api/v1/admin/overview)。
 *
 * P0 只返回 Java 侧可确定的用户/项目计数；任务/存储/GPU 由 Python/监控接入后补齐。
 */
@RestController
@RequestMapping("/api/v1/admin/overview")
@PreAuthorize("hasAuthority('user:manage')")
public class AdminOverviewController {

    private final IdentityRepository repo;
    private final ProjectMapper projectMapper;

    public AdminOverviewController(IdentityRepository repo, ProjectMapper projectMapper) {
        this.repo = repo;
        this.projectMapper = projectMapper;
    }

    @GetMapping(produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> overview() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "success");
        body.put("users", repo.countUsers());
        body.put("active_users", repo.countActiveUsers());
        body.put("projects", projectMapper.selectCount(new QueryWrapper<ProjectEntity>()));
        body.put("running_jobs", 0);
        body.put("storage_used_bytes", 0);
        body.put("note", "任务/存储指标待 Python/监控接入");
        return body;
    }
}
