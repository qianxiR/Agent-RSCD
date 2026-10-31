package com.rsiis.business.security;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

/**
 * TotpService 单元测试: secret 生成、当前码校验、错误码拒绝、恢复码格式。
 */
class TotpServiceTest {

    private final TotpService service = new TotpService();

    @Test
    void generatedSecretVerifiesItsCurrentCode() {
        String secret = service.generateSecret();
        String code = service.codeNow(secret);
        assertThat(code).matches("\\d{6}");
        assertThat(service.verify(secret, code)).isTrue();
    }

    @Test
    void rejectsMalformedOrWrongCode() {
        String secret = service.generateSecret();
        String code = service.codeNow(secret);
        String wrong = "000000".equals(code) ? "000001" : "000000";
        assertThat(service.verify(secret, wrong)).isFalse();
        assertThat(service.verify(secret, "12345")).isFalse();
        assertThat(service.verify(secret, "abc123")).isFalse();
    }

    @Test
    void recoveryCodesAreOneTimeFormatted() {
        var codes = service.generateRecoveryCodes(8);
        assertThat(codes).hasSize(8);
        assertThat(codes).allMatch(c -> c.matches("[A-Z2-7]{5}-[A-Z2-7]{5}"));
        assertThat(codes).doesNotHaveDuplicates();
    }
}
