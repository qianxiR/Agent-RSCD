package com.rsiis.business.security;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

import org.springframework.stereotype.Service;

/**
 * TOTP (RFC 6238) 工具: 生成 secret、校验 6 位码、生成 otpauth URI、生成恢复码。
 *
 * 入参: 用户名 / secret / code
 * 处理: Base32 编解码 + HmacSHA1 动态截断; 30 秒步长, 允许前后各 1 个窗口
 * 出参: secret / 校验结果 / URI / 恢复码
 * 边界: 不依赖第三方 OTP 库; 恢复码只返回一次, 由 MfaService 存哈希
 */
@Service
public class TotpService {

    private static final String BASE32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
    private static final int STEP_SECONDS = 30;
    private static final int DIGITS = 6;
    private static final SecureRandom RANDOM = new SecureRandom();

    public String generateSecret() {
        byte[] bytes = new byte[20];
        RANDOM.nextBytes(bytes);
        return base32Encode(bytes);
    }

    public List<String> generateRecoveryCodes(int count) {
        List<String> codes = new ArrayList<>();
        for (int i = 0; i < count; i++) {
            // 6 字节 → 10 个 Base32 字符，切成 XXXXX-XXXXX
            byte[] raw = new byte[6];
            RANDOM.nextBytes(raw);
            String s = base32Encode(raw);
            codes.add(s.substring(0, 5) + "-" + s.substring(5, 10));
        }
        return codes;
    }

    public boolean verify(String secret, String code) {
        if (secret == null || code == null) {
            return false;
        }
        String normalized = code.replaceAll("\\s", "");
        if (!normalized.matches("\\d{6}")) {
            return false;
        }
        long counter = Instant.now().getEpochSecond() / STEP_SECONDS;
        for (long offset = -1; offset <= 1; offset++) {
            String expected = codeAt(secret, counter + offset);
            if (constantTimeEquals(expected, normalized)) {
                return true;
            }
        }
        return false;
    }

    public String codeNow(String secret) {
        return codeAt(secret, Instant.now().getEpochSecond() / STEP_SECONDS);
    }

    public String otpauthUri(String issuer, String username, String secret) {
        String label = url(issuer) + ":" + url(username);
        return "otpauth://totp/" + label
                + "?secret=" + url(secret)
                + "&issuer=" + url(issuer)
                + "&algorithm=SHA1&digits=" + DIGITS + "&period=" + STEP_SECONDS;
    }

    private String codeAt(String secret, long counter) {
        try {
            byte[] key = base32Decode(secret);
            byte[] data = new byte[8];
            long value = counter;
            for (int i = 7; i >= 0; i--) {
                data[i] = (byte) (value & 0xff);
                value >>= 8;
            }
            Mac mac = Mac.getInstance("HmacSHA1");
            mac.init(new SecretKeySpec(key, "HmacSHA1"));
            byte[] hash = mac.doFinal(data);
            int offset = hash[hash.length - 1] & 0x0f;
            int binary = ((hash[offset] & 0x7f) << 24)
                    | ((hash[offset + 1] & 0xff) << 16)
                    | ((hash[offset + 2] & 0xff) << 8)
                    | (hash[offset + 3] & 0xff);
            int otp = binary % 1_000_000;
            return String.format("%06d", otp);
        } catch (Exception e) {
            throw new IllegalStateException("TOTP 计算失败", e);
        }
    }

    private boolean constantTimeEquals(String a, String b) {
        if (a.length() != b.length()) {
            return false;
        }
        int diff = 0;
        for (int i = 0; i < a.length(); i++) {
            diff |= a.charAt(i) ^ b.charAt(i);
        }
        return diff == 0;
    }

    private String url(String value) {
        return URLEncoder.encode(value == null ? "" : value, StandardCharsets.UTF_8);
    }

    private String base32Encode(byte[] data) {
        StringBuilder out = new StringBuilder();
        int buffer = 0;
        int bits = 0;
        for (byte b : data) {
            buffer = (buffer << 8) | (b & 0xff);
            bits += 8;
            while (bits >= 5) {
                bits -= 5;
                out.append(BASE32.charAt((buffer >> bits) & 0x1f));
            }
        }
        if (bits > 0) {
            out.append(BASE32.charAt((buffer << (5 - bits)) & 0x1f));
        }
        return out.toString();
    }

    private byte[] base32Decode(String input) {
        String s = input.replace("=", "").replace(" ", "").toUpperCase();
        int buffer = 0;
        int bits = 0;
        byte[] out = new byte[s.length() * 5 / 8 + 1];
        int index = 0;
        for (char c : s.toCharArray()) {
            int value = BASE32.indexOf(c);
            if (value < 0) {
                throw new IllegalArgumentException("非法 Base32 字符: " + c);
            }
            buffer = (buffer << 5) | value;
            bits += 5;
            if (bits >= 8) {
                bits -= 8;
                out[index++] = (byte) ((buffer >> bits) & 0xff);
            }
        }
        byte[] result = new byte[index];
        System.arraycopy(out, 0, result, 0, index);
        return result;
    }
}
