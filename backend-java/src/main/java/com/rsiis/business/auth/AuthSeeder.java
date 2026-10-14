package com.rsiis.business.auth;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationRunner;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.crypto.password.PasswordEncoder;

import java.util.UUID;

/**
 * 种子用户初始化: users 表为空时写入默认管理员 admin/admin123。
 *
 * 仅在空表时执行 (幂等); 生产环境必须通过环境变量改密或删除该默认账户。
 */
@Configuration
public class AuthSeeder {

    private static final Logger log = LoggerFactory.getLogger(AuthSeeder.class);

    @Bean
    public ApplicationRunner seedAdminUser(UserMapper userMapper, PasswordEncoder encoder) {
        return args -> {
            if (userMapper.selectCount(null) > 0) {
                return;
            }
            UserEntity admin = new UserEntity();
            admin.setId(UUID.randomUUID().toString());
            admin.setUsername("admin");
            admin.setPasswordHash(encoder.encode("admin123"));
            admin.setRole("admin");
            userMapper.insert(admin);
            log.info("[Auth] users 表为空, 已写入种子用户 admin (默认口令 admin123, 请尽快修改)");
        };
    }
}
