package com.rsiis.business.task;

import java.util.Map;

import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.rsiis.business.client.PythonInternalClient;

/**
 * AI 任务查询代理路由 (/api/v1/tasks, 只读)。
 *
 * ai_task / task_log 为 AI 运行时产物, 数据与端点均归 Python, 本服务纯代理。
 */
@RestController
public class TaskProxyController {

    private final PythonInternalClient python;

    public TaskProxyController(PythonInternalClient python) {
        this.python = python;
    }

    @GetMapping(value = "/api/v1/tasks", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> list(
            @RequestParam(required = false) String conversationId,
            @RequestParam(required = false) String status,
            @RequestParam(required = false) String userId,
            @RequestParam(defaultValue = "50") int limit) {
        return PythonInternalClient.toResponse(python.get("/internal/tasks",
                PythonInternalClient.query(
                        "conversation_id", conversationId,
                        "status", status,
                        "user_id", userId,
                        "limit", String.valueOf(limit))));
    }

    @GetMapping(value = "/api/v1/tasks/{taskId}", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> detail(@PathVariable long taskId) {
        return PythonInternalClient.toResponse(
                python.get("/internal/tasks/" + taskId, Map.of()));
    }

    @GetMapping(value = "/api/v1/tasks/{taskId}/logs", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> logs(@PathVariable long taskId,
                                       @RequestParam(defaultValue = "200") int limit) {
        return PythonInternalClient.toResponse(python.get("/internal/tasks/" + taskId + "/logs",
                Map.of("limit", String.valueOf(limit))));
    }
}
