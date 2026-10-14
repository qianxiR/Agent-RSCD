package com.rsiis.business.project;

import java.util.List;
import java.util.UUID;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.UpdateWrapper;
import com.rsiis.business.client.PythonInternalClient;

/**
 * 项目管理业务逻辑 (自 Python agent_db.create/list/update/delete_project 迁入)。
 *
 * 入参: user_id 与请求体 (见各方法)
 * 处理: CRUD 直连 rsiis_business 库; 删除时先通知 Python 把该项目的会话解绑
 *       (替代原库内 ON DELETE SET NULL 外键, 外键已随迁移移除)
 * 出参: 见各方法
 * 边界:
 * - folder_path 非空创建走 UPSERT (同路径复用已有项目)
 * - Python 不可达时解绑降级为仅记日志: 会话残留旧 project_id 无悬挂外键风险,
 *   不阻塞删除 (与原外键 SET NULL 语义的差异点)
 */
@Service
public class ProjectService {

    private static final Logger log = LoggerFactory.getLogger(ProjectService.class);

    private final ProjectMapper projectMapper;
    private final PythonInternalClient python;

    public ProjectService(ProjectMapper projectMapper, PythonInternalClient python) {
        this.projectMapper = projectMapper;
        this.python = python;
    }

    /**
     * 列出用户全部项目 (按更新时间倒序)。
     */
    public List<ProjectView> list(String userId) {
        List<ProjectEntity> rows = projectMapper.selectList(
                new QueryWrapper<ProjectEntity>().eq("user_id", userId)
                        .orderByDesc("updated_at"));
        return rows.stream().map(this::toView).toList();
    }

    /**
     * 创建项目。folderPath 非空且已存在同路径项目时复用返回已有 id (UPSERT)。
     *
     * @return 新建或复用的项目 id; 不会返回 null (冲突/失败由异常表达)
     */
    @Transactional
    public String create(String userId, CreateProjectRequest req) {
        if (req.folderPath() != null && !req.folderPath().isBlank()) {
            ProjectEntity existing = projectMapper.selectOne(
                    new QueryWrapper<ProjectEntity>().eq("folder_path", req.folderPath()));
            if (existing != null) {
                log.info("[Project] 复用现有项目 (folder_path={}): {}", req.folderPath(), existing.getId());
                return existing.getId();
            }
        }
        ProjectEntity entity = new ProjectEntity();
        entity.setId(UUID.randomUUID().toString());
        entity.setUserId(userId);
        entity.setName(req.name());
        entity.setDescription(req.description() == null ? "" : req.description());
        entity.setFolderPath(blankToNull(req.folderPath()));
        projectMapper.insert(entity);
        log.info("[Project] 创建成功: {} name={} folder={}",
                entity.getId(), entity.getName(), entity.getFolderPath() == null ? "-" : entity.getFolderPath());
        return entity.getId();
    }

    /**
     * 更新名称和/或描述 (至少一个字段非 null), 触碰 updated_at。
     *
     * @return true 表示项目存在且已更新; false 表示项目不存在
     */
    @Transactional
    public boolean update(String projectId, UpdateProjectRequest req) {
        UpdateWrapper<ProjectEntity> uw = new UpdateWrapper<ProjectEntity>()
                .eq("id", projectId)
                .set("updated_at", java.time.OffsetDateTime.now());
        if (req.name() != null) {
            uw.set("name", req.name());
        }
        if (req.description() != null) {
            uw.set("description", req.description());
        }
        return projectMapper.update(null, uw) > 0;
    }

    /**
     * 删除项目 (会话的 project_id 被 Python 侧置 NULL, 不级联删除会话)。
     *
     * @return true 表示项目存在且已删除
     */
    @Transactional
    public boolean delete(String projectId) {
        detachConversations(projectId);
        return projectMapper.delete(new QueryWrapper<ProjectEntity>().eq("id", projectId)) > 0;
    }

    // ==================== 内部工具 ====================

    /**
     * 通知 Python 把该项目的会话 project_id 置 NULL (best-effort, 失败仅日志)。
     */
    private void detachConversations(String projectId) {
        try {
            python.post("/internal/conversations/detach-project", java.util.Map.of(),
                    "{\"project_id\":\"" + projectId + "\"}");
        } catch (Exception e) {
            log.warn("[Project] 会话解绑失败 (不阻塞删除, 会话将残留旧 project_id): project={} 原因={}",
                    projectId, e.getMessage());
        }
    }

    private String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }

    private ProjectView toView(ProjectEntity e) {
        return new ProjectView(e.getId(), e.getName(),
                e.getDescription() == null ? "" : e.getDescription(),
                e.getCreatedAt(), e.getUpdatedAt(),
                e.getFolderPath() == null ? "" : e.getFolderPath());
    }
}
