package com.rsiis.business.config;

import java.nio.charset.StandardCharsets;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.MediaType;
import org.springframework.security.config.Customizer;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;
import org.springframework.security.web.util.matcher.AntPathRequestMatcher;

import com.rsiis.business.security.JwtAuthFilter;

/**
 * Spring Security 配置。
 *
 * 模式开关 (app.security.mode):
 * - off      HTTP 层全放行（迁移期默认），但方法级 @PreAuthorize 仍然生效
 * - observe  注册过滤器、解析 token，不拦截无效请求
 * - enforce  /api/v1/auth/** 放行，其余接口要求有效 JWT
 *
 * ★ 管理端 /api/v1/admin/** 始终要求方法级权限（user:manage / role:manage / audit:read 等），
 *   与 mode 无关；off 只适合本地联调，生产必须 enforce。
 */
@Configuration
@EnableMethodSecurity
public class SecurityConfig {

    private static final String MODE_ENFORCE = "enforce";

    private final AppProperties props;
    private final JwtAuthFilter jwtAuthFilter;

    public SecurityConfig(AppProperties props, JwtAuthFilter jwtAuthFilter) {
        this.props = props;
        this.jwtAuthFilter = jwtAuthFilter;
    }

    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        // 无状态 API: 关闭 CSRF 与会话, 统一 401 JSON 响应
        http.csrf(csrf -> csrf.disable())
                .sessionManagement(sm -> sm.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .exceptionHandling(eh -> eh.authenticationEntryPoint((req, res, ex) -> {
                    res.setStatus(401);
                    res.setContentType(MediaType.APPLICATION_JSON_VALUE);
                    res.setCharacterEncoding(StandardCharsets.UTF_8.name());
                    res.getWriter().write("{\"status\":\"error\",\"code\":\"UNAUTHORIZED\",\"msg\":\"未认证或 token 无效\"}");
                }).accessDeniedHandler((req, res, ex) -> {
                    res.setStatus(403);
                    res.setContentType(MediaType.APPLICATION_JSON_VALUE);
                    res.setCharacterEncoding(StandardCharsets.UTF_8.name());
                    res.getWriter().write("{\"status\":\"error\",\"code\":\"FORBIDDEN\",\"msg\":\"无权限\"}");
                }));

        if (props.security().enforce()) {
            http.cors(Customizer.withDefaults())
                    .authorizeHttpRequests(auth -> auth
                            .requestMatchers(new AntPathRequestMatcher("/api/v1/auth/**")).permitAll()
                            .requestMatchers(new AntPathRequestMatcher("/api/v1/admin/**")).authenticated()
                            .anyRequest().authenticated());
        } else {
            http.cors(Customizer.withDefaults())
                    .authorizeHttpRequests(auth -> auth.anyRequest().permitAll());
        }
        // 无论 off/observe/enforce 都解析 token，便于方法级权限与管理端联调。
        http.addFilterBefore(jwtAuthFilter, UsernamePasswordAuthenticationFilter.class);
        return http.build();
    }
}
