package com.rsiis.business.security;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Base64;

import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

import org.springframework.stereotype.Component;

import com.rsiis.business.config.AppProperties;

/**
 * MFA secret 加密存储 (AES/GCM)。
 *
 * 入参: 明文 secret
 * 处理: 优先使用 app.security.mfa-encryption-key (Base64 16/24/32 字节);
 *       留空时由 jwt-secret 做 SHA-256 派生; 12 字节随机 IV, 128-bit tag
 * 出参: Base64(iv + ciphertext) / 解密后的明文
 * 边界: 密钥变更会导致旧 secret 无法解密, 生产需固定并纳入密钥管理
 */
@Component
public class MfaCipher {

    private final SecretKeySpec key;
    private final SecureRandom random = new SecureRandom();

    public MfaCipher(AppProperties props) {
        String raw = props.security().mfaEncryptionKey();
        byte[] keyBytes;
        if (raw != null && !raw.isBlank()) {
            keyBytes = Base64.getDecoder().decode(raw);
            if (keyBytes.length != 16 && keyBytes.length != 24 && keyBytes.length != 32) {
                throw new IllegalStateException("MFA_ENCRYPTION_KEY 必须是 16/24/32 字节的 Base64");
            }
        } else {
            keyBytes = sha256(props.security().jwtSecret());
        }
        this.key = new SecretKeySpec(keyBytes, "AES");
    }

    public String encrypt(String plain) {
        try {
            byte[] iv = new byte[12];
            random.nextBytes(iv);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, key, new GCMParameterSpec(128, iv));
            byte[] cipherText = cipher.doFinal(plain.getBytes(StandardCharsets.UTF_8));
            byte[] out = new byte[iv.length + cipherText.length];
            System.arraycopy(iv, 0, out, 0, iv.length);
            System.arraycopy(cipherText, 0, out, iv.length, cipherText.length);
            return Base64.getEncoder().encodeToString(out);
        } catch (Exception e) {
            throw new IllegalStateException("MFA secret 加密失败", e);
        }
    }

    public String decrypt(String encoded) {
        try {
            byte[] all = Base64.getDecoder().decode(encoded);
            byte[] iv = new byte[12];
            System.arraycopy(all, 0, iv, 0, 12);
            byte[] cipherText = new byte[all.length - 12];
            System.arraycopy(all, 12, cipherText, 0, cipherText.length);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(128, iv));
            return new String(cipher.doFinal(cipherText), StandardCharsets.UTF_8);
        } catch (Exception e) {
            throw new IllegalStateException("MFA secret 解密失败", e);
        }
    }

    private static byte[] sha256(String value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
