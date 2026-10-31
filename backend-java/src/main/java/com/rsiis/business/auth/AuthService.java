package com.rsiis.business.auth;

import java.util.List;

import org.springframework.http.HttpStatus;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

import com.rsiis.business.audit.AuditService;
import com.rsiis.business.identity.IdentityRepository;
import com.rsiis.business.identity.RbacService;
import com.rsiis.business.security.JwtService;

import io.jsonwebtoken.Claims;
import jakarta.servlet.http.HttpServletRequest;

/**
 * 登录 / MFA / Refresh / 当前用户业务逻辑。
 *
 * 入参: 用户名口令、MFA token/code、refresh token
 * 处理: 口令校验 → 管理端强制 MFA 分流（绑定/校验）→ 签发 access+refresh；所有动作写审计
 * 出参: LoginResult / TokenPair / UserView
 * 边界: MFA token 只能用于 MFA 接口；refresh token 只存哈希；禁用用户立即失败
 */
@Service
public class AuthService {

    private final IdentityRepository repo;
    private final RbacService rbac;
    private final PasswordEncoder passwordEncoder;
    private final JwtService jwtService;
    private final RefreshTokenService refreshTokenService;
    private final MfaService mfaService;
    private final AuditService audit;

    public AuthService(IdentityRepository repo, RbacService rbac, PasswordEncoder passwordEncoder,
                       JwtService jwtService, RefreshTokenService refreshTokenService,
                       MfaService mfaService, AuditService audit) {
        this.repo = repo;
        this.rbac = rbac;
        this.passwordEncoder = passwordEncoder;
        this.jwtService = jwtService;
        this.refreshTokenService = refreshTokenService;
        this.mfaService = mfaService;
        this.audit = audit;
    }

    public record UserView(String id, String orgId, String username, String email, String role,
                           String status, boolean mfaEnabled, List<String> roles,
                           List<String> permissions) {
    }

    public record LoginResult(String status, String mfaToken, String setupToken,
                              RefreshTokenService.TokenPair tokens, UserView user) {
        static LoginResult tokens(RefreshTokenService.TokenPair pair, UserView user) {
            return new LoginResult("success", null, null, pair, user);
        }

        static LoginResult mfaRequired(String token) {
            return new LoginResult("mfa_required", token, null, null, null);
        }

        static LoginResult mfaSetupRequired(String token) {
            return new LoginResult("mfa_setup_required", null, token, null, null);
        }
    }

    public LoginResult login(String username, String password, HttpServletRequest request) {
        var userOpt = repo.findByUsername(username == null ? "" : username.trim());
        if (userOpt.isEmpty()) {
            audit.login(username, null, false, request);
            throw AuthException.unauthorized("LOGIN_FAILED", "用户名或口令错误");
        }
        var user = userOpt.get();
        if (!"active".equalsIgnoreCase(user.status())) {
            audit.login(username, user.id(), false, request);
            throw AuthException.forbidden("USER_DISABLED", "账号已禁用");
        }
        if (!passwordEncoder.matches(password == null ? "" : password, user.passwordHash())) {
            audit.login(username, user.id(), false, request);
            throw AuthException.unauthorized("LOGIN_FAILED", "用户名或口令错误");
        }

        if (mfaService.requiredFor(user)) {
            if (user.mfaEnabled()) {
                String token = jwtService.issueMfaToken(user.id(), JwtService.TYPE_MFA);
                audit.mfa(user.id(), user.username(), "auth.mfa.challenge", true, request);
                return LoginResult.mfaRequired(token);
            }
            String token = jwtService.issueMfaToken(user.id(), JwtService.TYPE_MFA_SETUP);
            audit.mfa(user.id(), user.username(), "auth.mfa.setup_required", true, request);
            return LoginResult.mfaSetupRequired(token);
        }

        audit.login(username, user.id(), true, request);
        return completeLogin(user, request);
    }

    public MfaService.SetupResult setupMfa(String setupToken, HttpServletRequest request) {
        var user = requireTypedUser(setupToken, JwtService.TYPE_MFA_SETUP);
        var result = mfaService.startSetup(user);
        audit.mfa(user.id(), user.username(), "auth.mfa.setup_start", true, request);
        return result;
    }

    public LoginResult confirmMfaSetup(String setupToken, String code, HttpServletRequest request) {
        var user = requireTypedUser(setupToken, JwtService.TYPE_MFA_SETUP);
        mfaService.confirmSetup(user, code);
        var refreshed = repo.findById(user.id()).orElseThrow(
                () -> AuthException.unauthorized("USER_NOT_FOUND", "用户不存在"));
        audit.mfa(user.id(), user.username(), "auth.mfa.setup_confirm", true, request);
        return completeLogin(refreshed, request);
    }

    public LoginResult verifyMfa(String mfaToken, String code, HttpServletRequest request) {
        var user = requireTypedUser(mfaToken, JwtService.TYPE_MFA);
        if (!mfaService.verify(user, code)) {
            audit.mfa(user.id(), user.username(), "auth.mfa.verify", false, request);
            throw AuthException.unauthorized("MFA_CODE_INVALID", "验证码不正确");
        }
        audit.mfa(user.id(), user.username(), "auth.mfa.verify", true, request);
        return completeLogin(user, request);
    }

    public RefreshTokenService.TokenPair refresh(String refreshToken, HttpServletRequest request) {
        return refreshTokenService.rotate(refreshToken, request);
    }

    public void logout(String refreshToken) {
        refreshTokenService.revoke(refreshToken);
    }

    public UserView me(String userId) {
        var user = repo.findById(userId).orElseThrow(
                () -> new AuthException(HttpStatus.UNAUTHORIZED, "UNAUTHORIZED", "用户不存在"));
        return toView(user);
    }

    public UserView toView(IdentityRepository.UserRow user) {
        return new UserView(user.id(), user.orgId(), user.username(), user.email(), user.role(),
                user.status(), user.mfaEnabled(), rbac.roleCodesOf(user.id()),
                rbac.permissionsOf(user.id()));
    }

    private LoginResult completeLogin(IdentityRepository.UserRow user, HttpServletRequest request) {
        repo.touchLogin(user.id());
        var pair = refreshTokenService.issue(user, request);
        return LoginResult.tokens(pair, toView(user));
    }

    private IdentityRepository.UserRow requireTypedUser(String token, String type) {
        Claims claims = jwtService.parseTyped(token, type)
                .orElseThrow(() -> AuthException.unauthorized("INVALID_MFA_TOKEN", "MFA 会话无效或已过期"));
        var user = repo.findById(claims.getSubject()).orElseThrow(
                () -> AuthException.unauthorized("USER_NOT_FOUND", "用户不存在"));
        if (!"active".equalsIgnoreCase(user.status())) {
            throw AuthException.forbidden("USER_DISABLED", "账号已禁用");
        }
        return user;
    }
}
