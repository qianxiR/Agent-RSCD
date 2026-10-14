package com.rsiis.business.security;

import java.io.IOException;
import java.util.List;

import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import io.jsonwebtoken.Claims;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

/**
 * JWT 认证过滤器: 解析 Authorization: Bearer <token>, 成功则填充 SecurityContext。
 *
 * 边界: 解析失败时不清除已有上下文也不拦截请求 — 拦截策略由 SecurityConfig
 * 的模式开关 (off/observe/enforce) 决定, 过滤器只负责「解析成功即认证」。
 */
@Component
public class JwtAuthFilter extends OncePerRequestFilter {

    private static final String BEARER_PREFIX = "Bearer ";

    private final JwtService jwtService;

    public JwtAuthFilter(JwtService jwtService) {
        this.jwtService = jwtService;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain chain) throws ServletException, IOException {
        String header = request.getHeader("Authorization");
        if (header != null && header.startsWith(BEARER_PREFIX)) {
            jwtService.parse(header.substring(BEARER_PREFIX.length())).ifPresent(claims -> {
                var authorities = List.of(new SimpleGrantedAuthority("ROLE_" + roleOf(claims)));
                var auth = new UsernamePasswordAuthenticationToken(
                        claims.getSubject(), null, authorities);
                // 补充用户名供日志/审计使用
                auth.setDetails(claims.get("username", String.class));
                SecurityContextHolder.getContext().setAuthentication(auth);
            });
        }
        chain.doFilter(request, response);
    }

    private String roleOf(Claims claims) {
        String role = claims.get("role", String.class);
        return role == null || role.isBlank() ? "user" : role;
    }
}
