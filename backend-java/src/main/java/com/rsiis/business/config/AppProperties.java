package com.rsiis.business.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 业务服务自定义配置 (application.yml 中 app.* 前缀)。
 *
 * @param pythonBaseUrl Python AI 服务基础地址 (代理调用目标)
 * @param internalToken 服务间静态令牌, 与 Python 侧 INTERNAL_API_TOKEN 保持一致
 * @param security      安全配置 (模式开关 / JWT 密钥 / 有效期)
 */
@ConfigurationProperties(prefix = "app")
public record AppProperties(
        String pythonBaseUrl,
        String internalToken,
        Security security
) {

    /**
     * 安全子配置。
     *
     * @param mode         认证模式: off 全放行 / observe 仅解析不拦截 / enforce 强制 JWT
     * @param jwtSecret    HS256 签名密钥 (至少 32 字节)
     * @param jwtTtlHours  token 有效期 (小时)
     */
    public record Security(String mode, String jwtSecret, int jwtTtlHours) {
    }
}
