package com.rsiis.business.project;

import java.util.Map;

import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import jakarta.validation.Valid;

/**
 * 项目管理路由 (/api/v1/projects)。
 *
 * 路径/参数/响应格式对齐原 Python backend/api/projects.py, 前端无感切换。
 */
@RestController
@RequestMapping("/api/v1/projects")
public class ProjectController {

    private final ProjectService projectService;

    public ProjectController(ProjectService projectService) {
        this.projectService = projectService;
    }

    @GetMapping(produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> list(@RequestParam(defaultValue = "study_user") String userId) {
        return Map.of("projects", projectService.list(userId));
    }

    @PostMapping(produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> create(@Valid @RequestBody CreateProjectRequest req,
                                      @RequestParam(defaultValue = "study_user") String userId) {
        String projectId = projectService.create(userId, req);
        return Map.of("status", "success", "project_id", projectId);
    }

    @PatchMapping(value = "/{projectId}", produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> update(@PathVariable String projectId,
                                      @RequestBody UpdateProjectRequest req) {
        if (projectService.update(projectId, req)) {
            return Map.of("status", "success");
        }
        return Map.of("status", "error", "msg", "项目不存在");
    }

    @DeleteMapping(value = "/{projectId}", produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> delete(@PathVariable String projectId) {
        if (projectService.delete(projectId)) {
            return Map.of("status", "success");
        }
        return Map.of("status", "error", "msg", "项目不存在");
    }
}
