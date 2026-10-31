package com.rsiis.business.auth;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.time.OffsetDateTime;
import java.util.Base64;
import java.util.HexFormat;
import java.util.Optional;
import java.util.UUID;

import org.springframework.stereotype.Service;

import com.rsiis.business.config.AppProperties;
import com.rsiis.business.identity.IdentityRepository;
import com.rsiis.business.identity.RbacService;
import com.rsiis.business.security.JwtService;

import jakarta.servlet.http.HttpServletRequest;

/**
 * Refresh Token 签发/轮换/撤销。
 *
 * 入参: 用户、请求
 * 处理: 随机 32 字节 Base64URL token, 数据库只存 SHA-256 哈希; 旋转时旧 token 立即撤销,
 *       family_id 保留同一登录链; 检测到已撤销 token 重放时撤销整个 family
 * 出参: TokenPair (access + refresh + 过期秒数)
 * 边界: refresh token 不写 JWT; 不存明文
 */
@Service
public class RefreshTokenService {

    private static final SecureRandom RANDOM = new SecureRandom();

    private final IdentityRepository repo;
    private final RbacService rbac;
    private final JwtService jwtService;
    private final int refreshTtlDays;

    public RefreshTokenService(IdentityRepository repo, RbacService rbac, JwtService jwtService,
                               AppProperties props) {
        this.repo = repo;
        this.rbac = rbac;
        this.jwtService = jwtService;
        this.refreshTtlDays = Math.max(1, props.security().refreshTtlDays());
    }

    public record TokenPair(String accessToken, String refreshToken,
                            long accessExpiresInSeconds, long refreshExpiresInSeconds) {
    }

    public TokenPair issue(IdentityRepository.UserRow user, HttpServletRequest request) {
        String familyId = UUID.randomUUID().toString();
        return issueInFamily(user, familyId, request);
    }

    public TokenPair rotate(String rawRefreshToken, HttpServletRequest request) {
        if (rawRefreshToken == null || rawRefreshToken.isBlank()) {
            throw AuthException.unauthorized("INVALID_REFRESH_TOKEN", "缺少 refresh token");
        }
        String hash = sha256(rawRefreshToken);
        var rowOpt = repo.findRefreshByHash(hash);
        if (rowOpt.isEmpty()) {
            throw AuthException.unauthorized("INVALID_REFRESH_TOKEN", "refresh token 无效");
        }
        var row = rowOpt.get();
        if (row.revokedAt() != null) {
            // 重放检测: 旧 token 被再次使用, 撤销整条登录链
            repo.revokeRefreshFamily(row.familyId());
            throw AuthException.unauthorized("REFRESH_REUSED", "refresh token 已失效，请重新登录");
        }
        if (row.expiresAt().isBefore(OffsetDateTime.now())) {
            repo.revokeRefreshToken(row.id());
            throw AuthException.unauthorized("REFRESH_EXPIRED", "refresh token 已过期，请重新登录");
        }
        var userOpt = repo.findById(row.userId());
        if (userOpt.isEmpty() || !"active".equalsIgnoreCase(userOpt.get().status())) {
            repo.revokeRefreshFamily(row.familyId());
            throw AuthException.unauthorized("USER_DISABLED", "账号不可用");
        }
        repo.revokeRefreshToken(row.id());
        return issueInFamily(userOpt.get(), row.familyId(), request);
    }

    public void revoke(String rawRefreshToken) {
        if (rawRefreshToken == null || rawRefreshToken.isBlank()) {
            return;
        }
        repo.findRefreshByHash(sha256(rawRefreshToken)).ifPresent(r -> repo.revokeRefreshToken(r.id()));
    }

    public void revokeAll(String userId) {
        repo.revokeAllForUser(userId);
    }

    private TokenPair issueInFamily(IdentityRepository.UserRow user, String familyId,
                                    HttpServletRequest request) {
        String audience = rbac.isAdmin(user.id()) ? JwtService.AUD_ADMIN : JwtService.AUD_USER;
        String access = jwtService.issueAccess(user.id(), user.username(), user.role(), audience);
        byte[] raw = new byte[32];
        RANDOM.nextBytes(raw);
        String refresh = Base64.getUrlEncoder().withoutPadding().encodeToString(raw);
        OffsetDateTime expiresAt = OffsetDateTime.now().plusDays(refreshTtlDays);
        repo.insertRefreshToken(
                UUID.randomUUID().toString(), user.id(), sha256(refresh), familyId, expiresAt,
                request == null ? null : request.getHeader("User-Agent"),
                AuditServiceIp.ip(request));
        return new TokenPair(access, refresh,
                jwtService.accessTtlMinutes() * 60L, refreshTtlDays * 86400L);
    }

    public static String sha256(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    /** 避免与 audit 包循环依赖的小工具。 */
    private static final class AuditServiceIp {
        static String ip(HttpServletRequest request) {
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
}
