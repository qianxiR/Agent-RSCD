package com.rsiis.business.client;

import java.util.Map;
import java.util.function.Supplier;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.util.UriComponentsBuilder;

/**
 * Python AI 服务内部接口客户端 (/internal/*)。
 *
 * 入参: 内部路径 + 查询参数 + 请求体 (JSON 字符串)
 * 处理: 以字符串收发, 响应体原样透传 (不做反序列化, 避免代理层丢字段);
 *       非 2xx 抛 InternalApiException (透传上游状态码与响应体),
 *       网络不可达抛 IllegalStateException (由全局处理器转 502)
 * 出参: ProxyResult (状态码 + 响应体)
 * 边界: Python 删除会话接口在 DB 删除后即返回 (沙盒/磁盘回收为 fire-and-forget),
 *       全局 10s 读超时对该接口同样适用, 无需单独放宽
 */
@Component
public class PythonInternalClient {

    private static final Logger log = LoggerFactory.getLogger(PythonInternalClient.class);

    private final RestClient restClient;

    public PythonInternalClient(RestClient pythonRestClient) {
        this.restClient = pythonRestClient;
    }

    /** 代理结果: 状态码 + 原样响应体。 */
    public record ProxyResult(int status, String body) {
    }

    /**
     * 代理结果 → HTTP 响应 (原样透传状态码与响应体, 供代理 Controller 统一使用)。
     */
    public static ResponseEntity<String> toResponse(ProxyResult result) {
        return ResponseEntity.status(result.status())
                .contentType(MediaType.APPLICATION_JSON)
                .body(result.body());
    }

    /**
     * 可空查询参数构造 (k1, v1, k2, v2, ...): 跳过 null/空值。
     * Map.of 不接受 null, 代理端点的可选参数必须经此构造。
     */
    public static Map<String, String> query(String... pairs) {
        Map<String, String> m = new java.util.LinkedHashMap<>();
        for (int i = 0; i + 1 < pairs.length; i += 2) {
            String v = pairs[i + 1];
            if (v != null && !v.isBlank()) {
                m.put(pairs[i], v);
            }
        }
        return m;
    }

    public ProxyResult get(String path, Map<String, String> query) {
        return call("GET", path, () -> restClient.get()
                .uri(uri(path, query)).retrieve().toEntity(String.class));
    }

    public ProxyResult post(String path, Map<String, String> query, String jsonBody) {
        return call("POST", path, () -> jsonBody == null
                ? restClient.post().uri(uri(path, query)).retrieve().toEntity(String.class)
                : restClient.post().uri(uri(path, query))
                        .contentType(MediaType.APPLICATION_JSON).body(jsonBody)
                        .retrieve().toEntity(String.class));
    }

    public ProxyResult patch(String path, Map<String, String> query, String jsonBody) {
        return call("PATCH", path, () -> jsonBody == null
                ? restClient.patch().uri(uri(path, query)).retrieve().toEntity(String.class)
                : restClient.patch().uri(uri(path, query))
                        .contentType(MediaType.APPLICATION_JSON).body(jsonBody)
                        .retrieve().toEntity(String.class));
    }

    public ProxyResult put(String path, Map<String, String> query, String jsonBody) {
        return call("PUT", path, () -> restClient.put().uri(uri(path, query))
                .contentType(MediaType.APPLICATION_JSON).body(jsonBody)
                .retrieve().toEntity(String.class));
    }

    public ProxyResult delete(String path, Map<String, String> query) {
        return call("DELETE", path, () -> restClient.delete()
                .uri(uri(path, query)).retrieve().toEntity(String.class));
    }

    // ==================== 内部工具 ====================

    private String uri(String path, Map<String, String> query) {
        UriComponentsBuilder builder = UriComponentsBuilder.fromPath(path);
        if (query != null) {
            query.forEach((k, v) -> {
                if (v != null && !v.isBlank()) {
                    builder.queryParam(k, v);
                }
            });
        }
        return builder.build().toUriString();
    }

    /**
     * 统一请求出口, 隔离 RestClient 两种失败形态:
     * 业务性非 2xx → InternalApiException (透传); 网络失败 → IllegalStateException (502)。
     */
    private ProxyResult call(String method, String path, Supplier<ResponseEntity<String>> invoker) {
        try {
            ResponseEntity<String> entity = invoker.get();
            return new ProxyResult(entity.getStatusCode().value(), entity.getBody());
        } catch (RestClientResponseException e) {
            log.warn("[Internal] {} {} → {}: {}", method, path, e.getStatusCode().value(),
                    e.getResponseBodyAsString().lines().findFirst().orElse(""));
            throw new InternalApiException(e.getStatusCode().value(), e.getResponseBodyAsString());
        } catch (ResourceAccessException e) {
            log.error("[Internal] {} {} 连接失败: {}", method, path, e.getMessage());
            throw new IllegalStateException("Python AI 服务不可达: " + e.getMessage(), e);
        }
    }
}
