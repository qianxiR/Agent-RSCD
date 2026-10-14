package com.rsiis.business.config;

import java.net.http.HttpClient;
import java.time.Duration;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.web.client.RestClient;

/**
 * 调用 Python AI 服务内部接口的 RestClient。
 *
 * ★ 必须用 JdkClientHttpRequestFactory (java.net.http.HttpClient):
 *   SimpleClientHttpRequestFactory 底层 HttpURLConnection 不支持 PATCH,
 *   会话重命名/移动项目等 PATCH 代理会抛 Invalid HTTP method。
 *
 * 超时取舍: 常规内部查询 10s; 删除会话在 Python 侧 DB 删除后即返回
 * (沙盒/磁盘回收 fire-and-forget), 10s 读超时同样适用。
 */
@Configuration
public class RestClientConfig {

    @Bean
    public RestClient pythonRestClient(AppProperties props) {
        HttpClient httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(5))
                .build();
        JdkClientHttpRequestFactory factory = new JdkClientHttpRequestFactory(httpClient);
        factory.setReadTimeout(Duration.ofSeconds(10));
        return RestClient.builder()
                .baseUrl(props.pythonBaseUrl())
                .defaultHeader("X-Internal-Token", props.internalToken())
                .requestFactory(factory)
                .build();
    }
}
