package com.rsiis.business.config;

import java.nio.charset.StandardCharsets;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.MediaType;
import org.springframework.security.config.Customizer;
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
 * - off     全放行, 不注册 JWT 过滤器 (迁移期默认, 现有 E2E 无登录流程)
 * - observe 注册过滤器, 解析成功填充认证上下文, 不拦截无效请求 (联调观察)
 * - enforce /api/v1/auth/** 放行, 其余接口要求有效 JWT, 否则 401 JSON
 */
@Configuration
public class SecurityConfig {

    private static final String MODE_ENFORCE = "enforce";
    private static final String MODE_OBSERVE = "observe";

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
        String mode = props.security().mode();
        // 无状态 API: 关闭 CSRF 与会话, 统一 401 JSON 响应
        http.csrf(csrf -> csrf.disable())
                .sessionManagement(sm -> sm.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .exceptionHandling(eh -> eh.authenticationEntryPoint((req, res, ex) -> {
                    res.setStatus(401);
                    res.setContentType(MediaType.APPLICATION_JSON_VALUE);
                    res.setCharacterEncoding(StandardCharsets.UTF_8.name());
                    res.getWriter().write("{\"status\":\"error\",\"msg\":\"未认证或 token 无效\"}");
                }));

        if (MODE_ENFORCE.equals(mode)) {
            http.cors(Customizer.withDefaults())
                    .authorizeHttpRequests(auth -> auth
                            .requestMatchers(new AntPathRequestMatcher("/api/v1/auth/**")).permitAll()
                            .anyRequest().authenticated())
                    .addFilterBefore(jwtAuthFilter, UsernamePasswordAuthenticationFilter.class);
        } else {
            if (MODE_OBSERVE.equals(mode)) {
                http.addFilterBefore(jwtAuthFilter, UsernamePasswordAuthenticationFilter.class);
            }
            http.cors(Customizer.withDefaults())
                    .authorizeHttpRequests(auth -> auth.anyRequest().permitAll());
        }
        return http.build();
    }
}
