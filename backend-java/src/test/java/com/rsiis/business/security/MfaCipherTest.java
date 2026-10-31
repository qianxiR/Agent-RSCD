package com.rsiis.business.security;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import org.junit.jupiter.api.Test;

import com.rsiis.business.config.AppProperties;

/**
 * MfaCipher 单元测试: AES/GCM 往返、随机 IV、密文篡改失败。
 */
class MfaCipherTest {

    private MfaCipher cipher() {
        var security = new AppProperties.Security(
                "off", "rsiis-unit-test-secret-key-0123456789abcdef", 1,
                30, 14, true, "test", "");
        return new MfaCipher(new AppProperties("http://127.0.0.1:8020", "t", security));
    }

    @Test
    void encryptThenDecryptRoundTrip() {
        MfaCipher cipher = cipher();
        String secret = "JBSWY3DPEHPK3PXP";
        String encrypted = cipher.encrypt(secret);
        assertThat(encrypted).isNotEqualTo(secret);
        assertThat(cipher.decrypt(encrypted)).isEqualTo(secret);
    }

    @Test
    void samePlainTextUsesDifferentIv() {
        MfaCipher cipher = cipher();
        assertThat(cipher.encrypt("same-secret")).isNotEqualTo(cipher.encrypt("same-secret"));
    }

    @Test
    void tamperedCipherTextFails() {
        MfaCipher cipher = cipher();
        String encrypted = cipher.encrypt("secret");
        String tampered = encrypted.substring(0, encrypted.length() - 4) + "AAAA";
        assertThatThrownBy(() -> cipher.decrypt(tampered))
                .isInstanceOf(IllegalStateException.class);
    }
}
