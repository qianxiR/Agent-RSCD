package com.rsiis.business.security;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import com.rsiis.business.identity.IdentityRepository;
import com.rsiis.business.identity.RbacService;

import io.jsonwebtoken.Claims;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

/**
 * JWT 认证过滤器: 解析 Authorization: Bearer <access token>，成功则填充 SecurityContext。
 *
 * 入参: HTTP 请求
 * 处理: 只接受 typ=access; 校验用户仍存在且 status=active; 从 DB 加载 roles/permissions
 *       Roles → ROLE_<CODE>; Permissions → 原样 authority (如 user:manage)
 * 出参: 无（副作用: SecurityContext）
 * 边界: 解析失败/用户禁用/权限加载失败均不认证; 是否拦截由 SecurityConfig 的 mode 决定
 */
@Component
public class JwtAuthFilter extends OncePerRequestFilter {

    private static final String BEARER_PREFIX = "Bearer ";

    private final JwtService jwtService;
    private final IdentityRepository identityRepository;
    private final RbacService rbacService;

    public JwtAuthFilter(JwtService jwtService, IdentityRepository identityRepository,
                         RbacService rbacService) {
        this.jwtService = jwtService;
        this.identityRepository = identityRepository;
        this.rbacService = rbacService;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain chain) throws ServletException, IOException {
        String header = request.getHeader("Authorization");
        if (header != null && header.startsWith(BEARER_PREFIX)) {
            jwtService.parseAccess(header.substring(BEARER_PREFIX.length())).ifPresent(claims -> {
                String userId = claims.getSubject();
                var userOpt = identityRepository.findById(userId);
                if (userOpt.isEmpty() || !"active".equalsIgnoreCase(userOpt.get().status())) {
                    return;
                }
                var user = userOpt.get();
                List<SimpleGrantedAuthority> authorities = new ArrayList<>();
                for (String role : rbacService.roleCodesOf(userId)) {
                    authorities.add(new SimpleGrantedAuthority("ROLE_" + role.toUpperCase()));
                }
                for (String permission : rbacService.permissionsOf(userId)) {
                    authorities.add(new SimpleGrantedAuthority(permission));
                }
                var auth = new UsernamePasswordAuthenticationToken(userId, null, authorities);
                auth.setDetails(user.username());
                SecurityContextHolder.getContext().setAuthentication(auth);
            });
        }
        chain.doFilter(request, response);
    }
}
