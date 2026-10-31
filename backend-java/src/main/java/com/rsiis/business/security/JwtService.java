package com.rsiis.business.security;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Date;
import java.util.Optional;
import java.util.UUID;

import javax.crypto.SecretKey;

import org.springframework.stereotype.Service;

import com.rsiis.business.config.AppProperties;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.JwtException;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;

/**
 * JWT 签发与解析 (HS256)。
 *
 * 入参: 登录用户信息 / 待解析 token
 * 处理:
 *  - Access Token: sub=userId, username, role, typ=access, aud=user|admin, 15–30 分钟
 *  - MFA Token: typ=mfa / mfa_setup, 5–10 分钟, 只用于第二步校验
 * 出参: 签发返回 token 字符串; 解析失败返回 empty, 不抛异常
 * 边界: 密钥不足 32 字节时启动即抛错; refresh token 不在这里签发（随机串 + DB 哈希）
 */
@Service
public class JwtService {

    public static final String TYPE_ACCESS = "access";
    public static final String TYPE_MFA = "mfa";
    public static final String TYPE_MFA_SETUP = "mfa_setup";

    public static final String AUD_USER = "user";
    public static final String AUD_ADMIN = "admin";

    private final SecretKey key;
    private final int legacyTtlHours;
    private final int accessTtlMinutes;
    private final int mfaTtlMinutes = 5;

    public JwtService(AppProperties props) {
        this.key = Keys.hmacShaKeyFor(props.security().jwtSecret().getBytes(StandardCharsets.UTF_8));
        this.legacyTtlHours = props.security().jwtTtlHours();
        int configured = props.security().accessTtlMinutes();
        this.accessTtlMinutes = configured > 0 ? configured : Math.max(1, legacyTtlHours * 60);
    }

    /**
     * 旧版兼容入口: 签发 24h 级别的 access token（aud=user）。
     * 新代码请使用 issueAccess(..., audience)。
     */
    public String issue(String userId, String username, String role) {
        Instant now = Instant.now();
        return Jwts.builder()
                .subject(userId)
                .claim("username", username)
                .claim("role", role)
                .claim("typ", TYPE_ACCESS)
                .audience().add(AUD_USER).and()
                .issuedAt(Date.from(now))
                .expiration(Date.from(now.plusSeconds(legacyTtlHours * 3600L)))
                .signWith(key)
                .compact();
    }

    public String issueAccess(String userId, String username, String role, String audience) {
        Instant now = Instant.now();
        return Jwts.builder()
                .id(UUID.randomUUID().toString())
                .subject(userId)
                .claim("username", username)
                .claim("role", role)
                .claim("typ", TYPE_ACCESS)
                .audience().add(audience == null ? AUD_USER : audience).and()
                .issuedAt(Date.from(now))
                .expiration(Date.from(now.plusSeconds(accessTtlMinutes * 60L)))
                .signWith(key)
                .compact();
    }

    public String issueMfaToken(String userId, String type) {
        Instant now = Instant.now();
        return Jwts.builder()
                .id(UUID.randomUUID().toString())
                .subject(userId)
                .claim("typ", type)
                .issuedAt(Date.from(now))
                .expiration(Date.from(now.plusSeconds(mfaTtlMinutes * 60L)))
                .signWith(key)
                .compact();
    }

    public Optional<Claims> parse(String token) {
        try {
            return Optional.of(Jwts.parser().verifyWith(key).build()
                    .parseSignedClaims(token).getPayload());
        } catch (JwtException | IllegalArgumentException e) {
            return Optional.empty();
        }
    }

    /**
     * 只接受 typ=access 的 token；MFA/setup token 不能访问业务接口。
     */
    public Optional<Claims> parseAccess(String token) {
        return parse(token).filter(c -> TYPE_ACCESS.equals(c.get("typ", String.class)));
    }

    public Optional<Claims> parseTyped(String token, String type) {
        return parse(token).filter(c -> type.equals(c.get("typ", String.class)));
    }

    public int accessTtlMinutes() {
        return accessTtlMinutes;
    }
}
