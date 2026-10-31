package com.rsiis.business.auth;

import java.util.ArrayList;
import java.util.List;

import org.springframework.stereotype.Service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.rsiis.business.config.AppProperties;
import com.rsiis.business.identity.IdentityRepository;
import com.rsiis.business.identity.RbacService;
import com.rsiis.business.security.MfaCipher;
import com.rsiis.business.security.TotpService;

/**
 * 管理端 MFA (TOTP) 绑定与校验。
 *
 * 入参: 用户 / 6 位码 / setup token
 * 处理: 首次绑定生成 secret + 恢复码; secret 加密入库; 恢复码只存 SHA-256 哈希
 * 出参: SetupResult / 校验布尔值
 * 边界: 仅 system_admin 强制 MFA; 未绑定前不能访问管理 API; 恢复码一次性使用
 */
@Service
public class MfaService {

    private final IdentityRepository repo;
    private final RbacService rbac;
    private final TotpService totp;
    private final MfaCipher cipher;
    private final ObjectMapper objectMapper;
    private final boolean mfaRequired;
    private final String issuer;

    public MfaService(IdentityRepository repo, RbacService rbac, TotpService totp,
                      MfaCipher cipher, ObjectMapper objectMapper, AppProperties props) {
        this.repo = repo;
        this.rbac = rbac;
        this.totp = totp;
        this.cipher = cipher;
        this.objectMapper = objectMapper;
        this.mfaRequired = props.security().mfaRequired();
        this.issuer = props.security().mfaIssuer() == null
                ? "国土智察" : props.security().mfaIssuer();
    }

    public record SetupResult(String secret, String otpauthUri, List<String> recoveryCodes) {
    }

    public boolean requiredFor(IdentityRepository.UserRow user) {
        // 兼容历史 users.role=admin 但尚未绑定 user_roles 的账号，避免绕过 MFA。
        return mfaRequired && (rbac.isAdmin(user.id()) || "admin".equalsIgnoreCase(user.role()));
    }

    public SetupResult startSetup(IdentityRepository.UserRow user) {
        String secret = totp.generateSecret();
        List<String> recoveryCodes = totp.generateRecoveryCodes(8);
        repo.setMfaSecret(user.id(), cipher.encrypt(secret));
        repo.setMfaEnabled(user.id(), false, toJson(recoveryCodes));
        return new SetupResult(secret, totp.otpauthUri(issuer, user.username(), secret), recoveryCodes);
    }

    public void confirmSetup(IdentityRepository.UserRow user, String code) {
        if (user.mfaSecret() == null || user.mfaSecret().isBlank()) {
            throw AuthException.badRequest("MFA_NOT_STARTED", "请先获取 MFA 绑定信息");
        }
        String secret = cipher.decrypt(user.mfaSecret());
        if (!totp.verify(secret, code)) {
            throw AuthException.badRequest("MFA_CODE_INVALID", "验证码不正确");
        }
        repo.setMfaEnabled(user.id(), true, user.mfaRecoveryCodes());
    }

    public boolean verify(IdentityRepository.UserRow user, String code) {
        if (!user.mfaEnabled() || user.mfaSecret() == null || user.mfaSecret().isBlank()) {
            return false;
        }
        String secret = cipher.decrypt(user.mfaSecret());
        if (totp.verify(secret, code)) {
            return true;
        }
        return consumeRecoveryCode(user, code);
    }

    /**
     * 恢复码: 不区分大小写/连字符，匹配后从列表移除（一次性）。
     */
    private boolean consumeRecoveryCode(IdentityRepository.UserRow user, String code) {
        if (code == null || user.mfaRecoveryCodes() == null) {
            return false;
        }
        String normalized = code.replace("-", "").replace(" ", "").toUpperCase();
        String hash = sha256(normalized);
        List<String> hashes = fromJson(user.mfaRecoveryCodes());
        if (!hashes.contains(hash)) {
            return false;
        }
        List<String> remaining = new ArrayList<>(hashes);
        remaining.remove(hash);
        repo.setMfaEnabled(user.id(), true, toJson(remaining));
        return true;
    }

    private String toJson(List<String> values) {
        try {
            return objectMapper.writeValueAsString(values);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    private List<String> fromJson(String json) {
        try {
            return objectMapper.readValue(json, new TypeReference<List<String>>() { });
        } catch (Exception e) {
            return List.of();
        }
    }

    private String sha256(String value) {
        return RefreshTokenService.sha256(value);
    }
}
