package com.rsiis.business.audit;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.rsiis.business.identity.IdentityRepository;

import jakarta.servlet.http.HttpServletRequest;

/**
 * 审计写入服务。
 *
 * 入参: 操作者、动作、资源、结果、before/after、请求
 * 处理: 序列化为 JSON 文本后写 audit_logs; 失败只记日志，不阻断业务
 * 出参: 无
 * 边界: actor 可为空（登录失败）；审计日志只追加，不提供更新/删除
 */
@Service
public class AuditService {

    private static final Logger log = LoggerFactory.getLogger(AuditService.class);

    private final IdentityRepository repo;
    private final ObjectMapper objectMapper;

    public AuditService(IdentityRepository repo, ObjectMapper objectMapper) {
        this.repo = repo;
        this.objectMapper = objectMapper;
    }

    public void record(String actorId, String actorName, String action, String resourceType,
                       String resourceId, String result, Object before, Object after,
                       HttpServletRequest request) {
        try {
            repo.insertAudit(
                    IdentityRepository.DEFAULT_ORG_ID,
                    actorId,
                    actorName,
                    action,
                    resourceType,
                    resourceId,
                    result,
                    before == null ? null : objectMapper.writeValueAsString(before),
                    after == null ? null : objectMapper.writeValueAsString(after),
                    clientIp(request),
                    request == null ? null : request.getHeader("User-Agent"),
                    request == null ? null : request.getHeader("X-Request-Id")
            );
        } catch (Exception e) {
            log.warn("[Audit] 写审计失败 (不影响业务): action={} actor={} err={}",
                    action, actorId, e.getMessage());
        }
    }

    public void login(String username, String actorId, boolean success, HttpServletRequest request) {
        record(actorId, username, success ? "auth.login.success" : "auth.login.failure",
                "user", actorId, success ? "success" : "failure",
                null, null, request);
    }

    public void mfa(String actorId, String actorName, String action, boolean success,
                    HttpServletRequest request) {
        record(actorId, actorName, action, "user", actorId,
                success ? "success" : "failure", null, null, request);
    }

    public static String clientIp(HttpServletRequest request) {
        if (request == null) {
            return null;
        }
        String forwarded = request.getHeader("X-Forwarded-For");
        if (forwarded != null && !forwarded.isBlank()) {
            return forwarded.split(",")[0].trim();
        }
        return request.getRemoteAddr();
    }
}
