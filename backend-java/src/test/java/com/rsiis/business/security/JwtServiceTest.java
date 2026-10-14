package com.rsiis.business.security;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Optional;

import org.junit.jupiter.api.Test;

import com.rsiis.business.config.AppProperties;

import io.jsonwebtoken.Claims;

/**
 * JwtService 单元测试: 签发/解析回路、无效 token、过期 token。
 */
class JwtServiceTest {

    private AppProperties props(int ttlHours) {
        return new AppProperties(
                "http://127.0.0.1:8020",
                "test-internal-token",
                new AppProperties.Security("off",
                        "rsiis-unit-test-secret-key-0123456789abcdef", ttlHours));
    }

    @Test
    void issueThenParseRoundTrip() {
        JwtService service = new JwtService(props(1));
        String token = service.issue("user-1", "admin", "admin");
        Optional<Claims> claims = service.parse(token);
        assertThat(claims).isPresent();
        assertThat(claims.get().getSubject()).isEqualTo("user-1");
        assertThat(claims.get().get("username", String.class)).isEqualTo("admin");
        assertThat(claims.get().get("role", String.class)).isEqualTo("admin");
    }

    @Test
    void parseRejectsTamperedToken() {
        JwtService service = new JwtService(props(1));
        String token = service.issue("user-1", "admin", "admin");
        // 篡改 payload 部分后验签必须失败
        String tampered = token.substring(0, token.length() - 4) + "AAAA";
        assertThat(service.parse(tampered)).isEmpty();
    }

    @Test
    void parseRejectsGarbage() {
        JwtService service = new JwtService(props(1));
        assertThat(service.parse("not-a-jwt")).isEmpty();
        assertThat(service.parse("")).isEmpty();
    }

    @Test
    void parseRejectsExpiredToken() {
        // ttl = -1 小时 → 签发即过期
        JwtService service = new JwtService(props(-1));
        String token = service.issue("user-1", "admin", "admin");
        assertThat(service.parse(token)).isEmpty();
    }

    @Test
    void differentSecretRejectsForeignToken() {
        String token = new JwtService(props(1)).issue("user-1", "admin", "admin");
        // 换密钥的服务实例解析必须失败
        JwtService other = new JwtService(new AppProperties(
                "http://127.0.0.1:8020", "t",
                new AppProperties.Security("off",
                        "another-secret-key-abcdefghijklmnopqrstuvwxyz", 1)));
        assertThat(other.parse(token)).isEmpty();
    }
}
