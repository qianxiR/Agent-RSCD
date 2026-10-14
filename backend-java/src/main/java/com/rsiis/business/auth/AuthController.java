package com.rsiis.business.auth;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.rsiis.business.security.JwtService;

import jakarta.validation.Valid;
import java.util.List;
import java.util.Map;

/**
 * 认证路由 (/api/v1/auth)。
 *
 * POST /api/v1/auth/login: 校验用户名口令 (BCrypt), 签发 JWT。
 * 失败统一返回「用户名或口令错误」, 不区分具体原因。
 */
@RestController
@RequestMapping("/api/v1/auth")
public class AuthController {

    private static final Logger log = LoggerFactory.getLogger(AuthController.class);

    private final UserMapper userMapper;
    private final PasswordEncoder passwordEncoder;
    private final JwtService jwtService;

    public AuthController(UserMapper userMapper, PasswordEncoder passwordEncoder, JwtService jwtService) {
        this.userMapper = userMapper;
        this.passwordEncoder = passwordEncoder;
        this.jwtService = jwtService;
    }

    @PostMapping(value = "/login", consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> login(@Valid @RequestBody LoginRequest req) {
        UserEntity user = userMapper.selectOne(
                new QueryWrapper<UserEntity>().eq("username", req.username()));
        // ★ 恒定时间语义: 用户不存在时也做一次哈希比对, 避免时序侧信道区分「无此用户」
        String hash = user != null ? user.getPasswordHash()
                : passwordEncoder.encode("no-such-user-placeholder");
        if (user == null || !passwordEncoder.matches(req.password(), hash)) {
            log.info("[Auth] 登录失败: username={}", req.username());
            return Map.of("status", "error", "msg", "用户名或口令错误");
        }
        String token = jwtService.issue(user.getId(), user.getUsername(), user.getRole());
        return Map.of(
                "status", "success",
                "token", token,
                "user_id", user.getId(),
                "username", user.getUsername(),
                "role", user.getRole()
        );
    }
}
