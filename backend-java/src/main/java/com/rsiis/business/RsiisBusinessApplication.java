package com.rsiis.business;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

/**
 * 国土智察 Java 业务服务入口。
 *
 * 职责边界 (与 Python AI 服务分工):
 * - 本服务: 项目管理 (直管 rsiis_business 库)、会话/任务代理 (转发 Python 内部接口)、
 *   工作区文件浏览、登录认证 (JWT, 为后续权限管理预留)。
 * - Python 服务 (8020): Agent 推理、WebSocket 聊天、SamSeg、沙盒、文件产物。
 */
@SpringBootApplication
@MapperScan("com.rsiis.business.**")
@ConfigurationPropertiesScan("com.rsiis.business.config")
public class RsiisBusinessApplication {

    public static void main(String[] args) {
        SpringApplication.run(RsiisBusinessApplication.class, args);
    }
}
