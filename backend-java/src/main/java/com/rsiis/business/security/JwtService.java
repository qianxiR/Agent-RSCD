package com.rsiis.business.security;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Date;
import java.util.Optional;

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
 * 处理: 签发带 sub(userId)/username/role/exp 的 token; 解析时验签并校验过期
 * 出参: 签发返回 token 字符串; 解析返回 Claims (失败返回 empty, 不抛异常)
 * 边界: 密钥不足 32 字节时启动即抛错 (Keys.hmacShaKeyFor 要求), 避免弱密钥静默上线
 */
@Service
public class JwtService {

    private final SecretKey key;
    private final int ttlHours;

    public JwtService(AppProperties props) {
        this.key = Keys.hmacShaKeyFor(props.security().jwtSecret().getBytes(StandardCharsets.UTF_8));
        this.ttlHours = props.security().jwtTtlHours();
    }

    /**
     * 签发 token。claims: sub=userId, username, role。
     */
    public String issue(String userId, String username, String role) {
        Instant now = Instant.now();
        return Jwts.builder()
                .subject(userId)
                .claim("username", username)
                .claim("role", role)
                .issuedAt(Date.from(now))
                .expiration(Date.from(now.plusSeconds(ttlHours * 3600L)))
                .signWith(key)
                .compact();
    }

    /**
     * 解析并验签。token 无效/过期返回 empty, 由调用方决定拦截策略。
     */
    public Optional<Claims> parse(String token) {
        try {
            return Optional.of(Jwts.parser().verifyWith(key).build()
                    .parseSignedClaims(token).getPayload());
        } catch (JwtException | IllegalArgumentException e) {
            return Optional.empty();
        }
    }
}
