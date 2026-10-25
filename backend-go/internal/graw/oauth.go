package graw

// 文件职责：OAuth2.0 登录（可自定义提供方，内置 GitHub 模板），包含配置读写、
// 授权跳转、回调换码、用户映射（固定绑定 / 已绑定 / 自动创建）与一次性票据兑换。
//
// 核心流程：/oauth/start 生成并暂存 state 后跳转第三方 → /oauth/callback 一次性消费 state、
// 用授权码换 access_token、拉取用户信息并映射本地账号 → 签发 JWT 但不放进 URL，
// 而是暂存为 2 分钟的一次性 ticket → 前端调用 /oauth/exchange 换取 token 与用户信息。
//
// 安全约束与易踩坑：
//   - state 必须一次性校验并立刻删除，防 CSRF 与重放；过期状态由 pruneOAuthMaps 清理；
//   - 绝不按「同名」直接登录已有账号，只认 bind_identity 或已保存的 oauth_provider+subject；
//   - bind_username 必须与 bind_identity 成对配置，且只有完整管理员能改配置，防提权；
//   - 回调地址优先取 GRAW_PUBLIC_URL，未配置时才由请求 Host / X-Forwarded-* 推导；
//     无论哪种方式都必须与第三方后台登记的 redirect_uri 一致，反向代理未透传
//     协议/主机头（或 Host 被伪造）时会导致换码失败；
//   - 已开启两步验证或仍使用默认密码的账号禁止走 OAuth，避免绕过 TOTP 与强制改密；
//   - 配置存 data/oauth.json（含 client_secret），读接口只回 has_secret，绝不明文回传。
//
// 设计目标：
//   - 管理员可在设置里启用一个外部 OAuth2.0 提供方（授权码模式），
//     配置授权地址 / 令牌地址 / 用户信息地址 / 客户端凭据 / 用户名映射字段；
//   - 登录页只暴露「是否启用 + 提供方名称」，不泄露 client_id / secret；
//   - 回调成功后不把 JWT 直接放在 URL 里，而是换成一次性 ticket，
//     由前端调用 /api/auth/oauth/exchange 兑换，降低令牌泄漏面；
//   - 用户绑定采用三种安全策略（优先级从高到低）：
//     1) bind_username：所有 OAuth 登录固定映射到指定面板账号（单管理员场景推荐）；
//     2) 已绑定：用户记录里存的 oauth_provider + oauth_subject 完全匹配；
//     3) auto_create：自动创建一个无模块权限的普通用户（role=user）。
//     绝不按「同名」直接登录已有账号，避免第三方账号冒充本地管理员。

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
)

// OAuth2 相关常量：配置文件名、state / ticket 有效期、外部请求超时与响应体上限。
// state 10 分钟覆盖用户完成第三方登录的正常耗时；ticket 2 分钟只够前端立即兑换；
// 外部响应最多读 1MB，防止异常提供方返回超大内容拖垮进程。
const (
	oauthConfigFileName = "oauth.json"
	oauthStateTTL       = 10 * time.Minute
	oauthTicketTTL      = 2 * time.Minute
	oauthRequestTimeout = 20 * time.Second
	oauthMaxBodyBytes   = 1 << 20 // 外部响应最多读 1MB，防止异常提供方返回超大内容
)

// oauthHTTPClient 是所有外部 OAuth2 请求共用的客户端；超时统一 20 秒，
// 避免第三方无响应时一直占用 handler 协程。
var oauthHTTPClient = &http.Client{Timeout: oauthRequestTimeout}

// oauthSettings 对应 data/oauth.json 的结构，含 client_secret（文件权限由 SecureDataDir 收紧）。
// Enabled 为总开关；三个 URL 在启用时必填；UsernameField 决定自动创建用户时取哪个字段做用户名。
type oauthSettings struct {
	Enabled       bool   `json:"enabled"`
	ProviderName  string `json:"provider_name"`
	ClientID      string `json:"client_id"`
	ClientSecret  string `json:"client_secret"`
	AuthURL       string `json:"auth_url"`
	TokenURL      string `json:"token_url"`
	UserinfoURL   string `json:"userinfo_url"`
	Scopes        string `json:"scopes"`
	UsernameField string `json:"username_field"`
	AutoCreate    bool   `json:"auto_create"`
	BindUsername  string `json:"bind_username"`
	// BindIdentity 是允许映射到 BindUsername 的第三方账号标识（登录名 / 邮箱 / 数值 ID）。
	// 必须与 bind_username 成对配置：否则任何能访问该 OAuth 应用的第三方账号
	// 都能登录到管理员账号，形成提权漏洞。
	BindIdentity string `json:"bind_identity"`
}

// oauthSettingsRequest 是保存配置的请求体：空 secret 表示保留原值，
// clear_secret=true 才会真正清空（前端默认不暴露此开关），避免管理员每次保存都要重填密钥。
type oauthSettingsRequest struct {
	oauthSettings
	ClearSecret bool `json:"clear_secret"`
}

// oauthState 是 /oauth/start 暂存的 CSRF 状态：Expires 到期即失效，
// Redirect 保存发起登录时的面板内跳转路径，回调成功后原样带回。
type oauthState struct {
	Expires  time.Time
	Redirect string
}

// oauthTicket 是回调与前端兑换之间的一次性票据：Expires 控制 2 分钟有效期，
// Token 是已签发的 JWT，User 是脱敏后的用户信息。
type oauthTicket struct {
	Expires time.Time
	Token   string
	User    map[string]any
}

// oauthMu 保护两张内存表（state 与 ticket）；所有读写都必须持锁。
// oauthTicketM 中的票据在兑换时立即删除，保证一次性。
var (
	oauthMu      sync.Mutex
	oauthStates  = map[string]oauthState{}
	oauthTicketM = map[string]oauthTicket{}
	// oauthUserMu 串行化「查绑定 + 自动创建用户」，避免同一第三方账号并发首次登录
	// 时重复创建多个本地账号。
	oauthUserMu sync.Mutex
)

// registerOAuthRoutes 注册 OAuth2 路由：status/start/callback/exchange 必须公开
// （登录前访问、由第三方回调），config 读写仅完整管理员可用（路由层先做 adminOnly，
// handler 内再校验完整管理员，防止受限管理员改绑定关系提权）。
func registerOAuthRoutes(a *App) {
	a.Get("/api/auth/oauth/status", public(), oauthStatusHandler)
	a.Get("/api/auth/oauth/start", public(), oauthStartHandler)
	a.Get("/api/auth/oauth/callback", public(), oauthCallbackHandler)
	a.Post("/api/auth/oauth/exchange", public(), oauthExchangeHandler)
	a.Get("/api/auth/oauth/config", adminOnly(), oauthConfigGetHandler)
	a.Put("/api/auth/oauth/config", adminOnly(), oauthConfigSaveHandler)
}

// ---------------------------------------------------------------------------
// 配置读写
// ---------------------------------------------------------------------------

// defaultOAuthSettings 返回未配置时的默认值：提供方名 OAuth2、
// 默认 scope 与 OIDC 常见的 preferred_username 映射字段，其余字段留空。
func defaultOAuthSettings() oauthSettings {
	return oauthSettings{
		ProviderName:  "OAuth2",
		Scopes:        "openid profile email",
		UsernameField: "preferred_username",
	}
}

// loadOAuthSettings 读取 data/oauth.json：文件不存在时静默返回默认值，
// 其他读取/解析错误会记日志但仍返回（尽量可用）默认值，最后统一做规范化。
func loadOAuthSettings() oauthSettings {
	c := defaultOAuthSettings()
	if err := ReadJSON(DataPath(oauthConfigFileName), &c); err != nil && !IsNotExist(err) {
		log.Printf("graw: 读取 OAuth2 配置失败: %v", err)
	}
	return normalizeOAuthSettings(c)
}

// saveOAuthSettings 原子写入配置（临时文件 + rename，0600），失败返回写盘错误。
func saveOAuthSettings(c oauthSettings) error {
	return WriteJSONFile(DataPath(oauthConfigFileName), c)
}

// normalizeOAuthSettings 清理首尾空白并为空字段补默认值（提供方名 / scope / 用户名字段）。
// 只做规范化不做校验，非法值由 validateOAuthSettings 拦截。
func normalizeOAuthSettings(c oauthSettings) oauthSettings {
	c.ProviderName = strings.TrimSpace(c.ProviderName)
	if c.ProviderName == "" {
		c.ProviderName = "OAuth2"
	}
	c.ClientID = strings.TrimSpace(c.ClientID)
	c.ClientSecret = strings.TrimSpace(c.ClientSecret)
	c.AuthURL = strings.TrimSpace(c.AuthURL)
	c.TokenURL = strings.TrimSpace(c.TokenURL)
	c.UserinfoURL = strings.TrimSpace(c.UserinfoURL)
	c.Scopes = strings.TrimSpace(c.Scopes)
	if c.Scopes == "" {
		c.Scopes = "openid profile email"
	}
	c.UsernameField = strings.TrimSpace(c.UsernameField)
	if c.UsernameField == "" {
		c.UsernameField = "preferred_username"
	}
	c.BindUsername = strings.TrimSpace(c.BindUsername)
	c.BindIdentity = strings.TrimSpace(c.BindIdentity)
	return c
}

// validOAuthURL 判断配置的 URL 是否为带主机名的 http/https 地址。
// 不做内网地址限制（这些地址由完整管理员配置，属于可信输入）。
func validOAuthURL(raw string) bool {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || u.Host == "" {
		return false
	}
	return u.Scheme == "http" || u.Scheme == "https"
}

// validateOAuthSettings 校验并就地规范化配置，非法时返回 400 的具体原因：
//  1. 提供方名称 ≤48 字符；用户名字段只允许 login/email/preferred_username/username；
//  2. 三个 URL 填了就必须是 http(s)；
//  3. bind_username 与 bind_identity 必须同时存在或同时为空（否则任何第三方账号都能登录管理员）；
//  4. 启用时必须填全 client 凭据与三个地址。
func validateOAuthSettings(c *oauthSettings) *HTTPError {
	// 先规范化，后续长度/枚举判断都基于清理后的值
	*c = normalizeOAuthSettings(*c)
	if len([]rune(c.ProviderName)) > 48 {
		return NewHTTPError(400, "提供方名称不能超过 48 个字符")
	}
	// 用户名字段必须是受支持的枚举，防止自动创建时取到任意路径
	if c.UsernameField != "login" && c.UsernameField != "email" &&
		c.UsernameField != "preferred_username" && c.UsernameField != "username" {
		return NewHTTPError(400, "用户名映射字段仅支持 login / email / preferred_username / username")
	}
	// 三个地址填写后必须是合法 http(s)
	for _, item := range []struct {
		name string
		val  string
	}{
		{"授权地址", c.AuthURL},
		{"令牌地址", c.TokenURL},
		{"用户信息地址", c.UserinfoURL},
	} {
		if item.val != "" && !validOAuthURL(item.val) {
			return NewHTTPError(400, item.name+"必须是 http(s) 地址")
		}
	}
	if c.BindUsername != "" && !usernamePattern.MatchString(c.BindUsername) {
		return NewHTTPError(400, "绑定的面板用户名格式不正确")
	}
	// 绑定账号与允许标识必须成对出现，单独配置任一字段都会造成提权或不可用
	if (c.BindUsername == "") != (c.BindIdentity == "") {
		return NewHTTPError(400, "固定绑定面板账号必须同时填写「允许的第三方账号标识」")
	}
	if len([]rune(c.BindIdentity)) > 128 {
		return NewHTTPError(400, "第三方账号标识不能超过 128 个字符")
	}
	// 启用时必须具备完整的客户端凭据与三个端点
	if c.Enabled {
		if c.ClientID == "" || c.ClientSecret == "" {
			return NewHTTPError(400, "启用 OAuth2 登录时必须填写 Client ID 与 Client Secret")
		}
		if c.AuthURL == "" || c.TokenURL == "" || c.UserinfoURL == "" {
			return NewHTTPError(400, "启用 OAuth2 登录时必须填写授权、令牌与用户信息地址")
		}
	}
	return nil
}

// oauthConfigView 生成返回给管理员的配置视图：client_secret 只以 has_secret 布尔体现，
// 绝不明文回传。callback_url 由当前请求推导，供管理员复制到第三方后台登记
// （与实际回调使用的地址保持一致，避免 redirect_uri mismatch）。
func oauthConfigView(c oauthSettings, r *http.Request) map[string]any {
	return map[string]any{
		"enabled":        c.Enabled,
		"provider_name":  c.ProviderName,
		"client_id":      c.ClientID,
		"has_secret":     c.ClientSecret != "",
		"auth_url":       c.AuthURL,
		"token_url":      c.TokenURL,
		"userinfo_url":   c.UserinfoURL,
		"scopes":         c.Scopes,
		"username_field": c.UsernameField,
		"auto_create":    c.AutoCreate,
		"bind_username":  c.BindUsername,
		"bind_identity":  c.BindIdentity,
		"callback_url":   oauthCallbackURL(r),
	}
}

// oauthStatusHandler 是登录页用的公开状态接口，只返回是否启用与展示名。
// 不校验 ShunX 安全入口（登录页本身就需要拿到该信息）；因此这里也不返回任何敏感配置。
func oauthStatusHandler(w http.ResponseWriter, _ *http.Request) error {
	c := loadOAuthSettings()
	return WriteJSON(w, 200, map[string]any{
		"enabled":       c.Enabled,
		"provider_name": c.ProviderName,
	})
}

// requireFullAdminForOAuth 限制 OAuth 配置只能由「完整管理员」修改：
// 受限管理员若能改 bind_username，就能把自己映射到超级管理员账号，属于提权。
func requireFullAdminForOAuth(r *http.Request) *HTTPError {
	if _, full := UserPerms(CurrentUser(r)); !full {
		return NewHTTPError(403, "需要完整管理员权限才能配置 OAuth2.0 登录")
	}
	return nil
}

// oauthConfigGetHandler 返回完整配置视图，仅完整管理员可读（含 provider 地址等敏感信息）。
func oauthConfigGetHandler(w http.ResponseWriter, r *http.Request) error {
	if err := requireFullAdminForOAuth(r); err != nil {
		return err
	}
	return WriteJSON(w, 200, oauthConfigView(loadOAuthSettings(), r))
}

// oauthConfigSaveHandler 保存 OAuth2 配置，仅完整管理员可用。
//
// 密钥处理：clear_secret=true 时清空；否则请求里的 client_secret 为空表示沿用旧值，
// 这样前端不必回传明文密钥。校验通过后还要确认 bind_username 对应的面板用户存在，
// 最后写盘并记审计，返回脱敏后的新配置。
func oauthConfigSaveHandler(w http.ResponseWriter, r *http.Request) error {
	// 第一道闸门：受限管理员改绑定关系可把自己映射到超级管理员，必须拒绝
	if err := requireFullAdminForOAuth(r); err != nil {
		return err
	}
	var req oauthSettingsRequest
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	old := loadOAuthSettings()
	// 密钥二选一：显式清空，或留空沿用旧值（避免每次保存都要重填）
	if req.ClearSecret {
		req.ClientSecret = ""
	} else if strings.TrimSpace(req.ClientSecret) == "" {
		req.ClientSecret = old.ClientSecret // 留空 = 保持原密钥，避免每次保存都要重填
	}
	if err := validateOAuthSettings(&req.oauthSettings); err != nil {
		return err
	}
	// 绑定的面板账号必须真实存在，否则启用后会一直登录失败
	if req.BindUsername != "" {
		if u, _ := GetUser(req.BindUsername); u == nil {
			return NewHTTPError(400, "绑定的面板用户不存在："+req.BindUsername)
		}
	}
	// 校验全部通过后才落盘（原子写），并记录审计（不记录密钥）
	if err := saveOAuthSettings(req.oauthSettings); err != nil {
		return err
	}
	AuditRecord("更新 OAuth2 配置", CurrentUser(r).Username(), ClientIP(r),
		"启用:"+strconv.FormatBool(req.Enabled)+" 提供方:"+req.ProviderName)
	return WriteJSON(w, 200, map[string]any{"ok": true, "config": oauthConfigView(req.oauthSettings, r)})
}

// ---------------------------------------------------------------------------
// 登录流程：start → 提供方 → callback → 一次性 ticket → exchange
// ---------------------------------------------------------------------------

// oauthStartHandler 发起 OAuth2 授权：校验入口与配置、生成一次性 state、302 跳转提供方。
//
// redirect 只接受面板内绝对路径（sanitizeOAuthRedirect），用于登录成功后回到原页面。
// 失败行为：不直接返回 JSON 错误，而是带回 oauth_error 跳回面板，便于登录页展示原因。
// 必须在跳转前把 state 存进内存表，回调侧才能一次性校验并防 CSRF。
func oauthStartHandler(w http.ResponseWriter, r *http.Request) error {
	c := loadOAuthSettings()
	redirect := sanitizeOAuthRedirect(r.URL.Query().Get("redirect"))
	// ShunX 安全入口：OAuth 是公开 GET 入口，若不校验入口路径，
	// 不知道安全入口的人可绕过登录页直接访问 /oauth/start 完成登录。
	if !oauthEntryAllowed(r, redirect) {
		return oauthRedirectError(w, r, redirect, "请通过安全入口访问面板后再使用 OAuth2 登录")
	}
	// 配置不完整时提前失败，避免把用户送到第三方再报 redirect_uri/client 错误
	if !c.Enabled || c.ClientID == "" || c.ClientSecret == "" ||
		c.AuthURL == "" || c.TokenURL == "" || c.UserinfoURL == "" {
		return oauthRedirectError(w, r, redirect, "OAuth2 登录未启用或配置不完整")
	}
	if !validOAuthURL(c.AuthURL) {
		return oauthRedirectError(w, r, redirect, "OAuth2 授权地址无效")
	}
	authURL, err := url.Parse(c.AuthURL)
	if err != nil {
		return oauthRedirectError(w, r, redirect, "OAuth2 授权地址无效")
	}
	// 生成随机 state 并登记 TTL 与登录后要回的路径；顺手清理过期的 state/ticket
	state, err := oauthRandomToken(24)
	if err != nil {
		return oauthRedirectError(w, r, redirect, "生成登录状态失败")
	}
	now := time.Now()
	oauthMu.Lock()
	pruneOAuthMaps(now)
	oauthStates[state] = oauthState{Expires: now.Add(oauthStateTTL), Redirect: redirect}
	oauthMu.Unlock()

	// 拼授权 URL：redirect_uri 必须与第三方登记的地址完全一致，否则提供方会拒绝
	q := authURL.Query()
	q.Set("client_id", c.ClientID)
	q.Set("redirect_uri", oauthCallbackURL(r))
	q.Set("response_type", "code")
	if c.Scopes != "" {
		q.Set("scope", c.Scopes)
	}
	q.Set("state", state)
	authURL.RawQuery = q.Encode()
	// 禁止缓存跳转响应，避免 state 被中间层缓存复用
	w.Header().Set("Cache-Control", "no-store")
	http.Redirect(w, r, authURL.String(), http.StatusFound)
	return nil
}

// oauthCallbackHandler 处理第三方回调：校验 state、用 code 换 token、取用户信息、
// 映射本地账号并签发登录态，最后带着一次性 ticket 跳回面板。
//
// 失败行为：所有失败都通过 oauthRedirectError 跳回面板并带 oauth_error，不返回 JSON。
// state 在函数开头就消费掉（无论后续成败），保证同一个 state 不能被重放。
func oauthCallbackHandler(w http.ResponseWriter, r *http.Request) error {
	q := r.URL.Query()
	redirect := sanitizeOAuthRedirect(q.Get("redirect"))
	// 第一步：一次性消费 state（内部会删除），并用发起时保存的跳转路径覆盖 query 参数，
	// 避免攻击者用回调参数把用户重定向到别处
	state, stateOK := takeOAuthState(q.Get("state"))
	if state != nil {
		redirect = state.Redirect
	}
	// 第二步：第三方返回 error（用户取消/拒绝授权）优先报错，此时 state 已被消费
	if errCode := strings.TrimSpace(q.Get("error")); errCode != "" {
		desc := strings.TrimSpace(q.Get("error_description"))
		if desc == "" {
			desc = errCode
		}
		return oauthRedirectError(w, r, redirect, "第三方登录已取消或失败："+desc)
	}
	// 第三步：state 无效/过期视为 CSRF 或超时，直接终止
	if !stateOK {
		return oauthRedirectError(w, r, redirect, "OAuth2 登录状态已失效，请重新发起登录")
	}
	code := strings.TrimSpace(q.Get("code"))
	if code == "" {
		return oauthRedirectError(w, r, redirect, "OAuth2 回调未收到授权码")
	}
	c := loadOAuthSettings()
	if !c.Enabled {
		return oauthRedirectError(w, r, redirect, "OAuth2 登录未启用")
	}
	if !validOAuthURL(c.TokenURL) || !validOAuthURL(c.UserinfoURL) {
		return oauthRedirectError(w, r, redirect, "OAuth2 令牌或用户信息地址无效")
	}

	// 第四步：授权码换 access_token；redirect_uri 必须与 start 时一致
	accessToken, err := oauthExchangeCode(c, code, oauthCallbackURL(r))
	if err != nil {
		log.Printf("graw: OAuth2 换取令牌失败: %v", err)
		return oauthRedirectError(w, r, redirect, "OAuth2 换取令牌失败："+err.Error())
	}
	// 第五步：拉取用户信息（GitHub 可能补查邮箱）
	info, err := oauthFetchUserinfo(c, accessToken)
	if err != nil {
		log.Printf("graw: OAuth2 读取用户信息失败: %v", err)
		return oauthRedirectError(w, r, redirect, "OAuth2 读取用户信息失败："+err.Error())
	}
	// 第六步：把第三方身份映射到本地账号（可能自动创建普通用户）
	username, herr := resolveOAuthUser(c, info)
	if herr != nil {
		return oauthRedirectError(w, r, redirect, herr.Detail)
	}
	user, _ := GetUser(username)
	if user == nil {
		return oauthRedirectError(w, r, redirect, "OAuth2 映射的面板用户不存在")
	}
	// 第七步：安全降级检查——仍待改密 / 默认密码 / 已开两步验证的账号一律禁止 OAuth 登录
	if user.Bool("must_change_password") || IsDefaultPassword(user.PasswordHash()) {
		return oauthRedirectError(w, r, redirect, "该账号仍需修改密码，请先用密码登录并完成改密后再使用 OAuth2 登录")
	}
	// 已开启面板两步验证的账号不允许用 OAuth2 绕过 TOTP：要么用密码 + 验证码登录，
	// 要么由管理员先关闭两步验证（保持既有安全策略不被第三方登录降级）。
	if user.Bool("otp_enabled") {
		return oauthRedirectError(w, r, redirect, "该账号已开启两步验证，请使用密码 + 动态验证码登录")
	}

	// 第八步：签发会话与 JWT（与密码登录一致，便于统一踢线/设备管理）
	ip := ClientIP(r)
	ua := r.Header.Get("User-Agent")
	sid := CreateSession(username, ip, ParseDevice(ua))
	token, err := CreateToken(username, user.Int("token_version"), sid)
	if err != nil {
		return oauthRedirectError(w, r, redirect, "签发登录令牌失败")
	}
	// 第九步：JWT 不进 URL，改存一次性 ticket（2 分钟），由前端 POST /exchange 兑换
	ticket, err := oauthRandomToken(24)
	if err != nil {
		return oauthRedirectError(w, r, redirect, "签发登录票据失败")
	}
	now := time.Now()
	oauthMu.Lock()
	pruneOAuthMaps(now)
	oauthTicketM[ticket] = oauthTicket{
		Expires: now.Add(oauthTicketTTL),
		Token:   token,
		User:    PublicUser(user),
	}
	oauthMu.Unlock()

	AuditRecord("OAuth2 登录成功", username, ip, "提供方:"+c.ProviderName)
	RecordLogin(username, ip, ua, "success", "OAuth2 登录（"+c.ProviderName+"）")
	// 日志前去掉 CR/LF，防止第三方可控名称/用户名/IP 伪造日志行
	log.Printf("graw: 用户 %s 通过 OAuth2（%s）登录成功（IP %s）",
		strings.ReplaceAll(strings.ReplaceAll(username, "\r", ""), "\n", ""),
		strings.ReplaceAll(strings.ReplaceAll(c.ProviderName, "\r", ""), "\n", ""),
		strings.ReplaceAll(strings.ReplaceAll(ip, "\r", ""), "\n", ""))

	// 第十步：把 ticket 拼回发起时的面板内路径并 302；no-store 防止 URL 被缓存
	u, _ := url.Parse(redirect)
	uq := u.Query()
	uq.Set("oauth_ticket", ticket)
	u.RawQuery = uq.Encode()
	w.Header().Set("Cache-Control", "no-store")
	http.Redirect(w, r, u.String(), http.StatusFound)
	return nil
}

// oauthExchangeHandler 用一次性 ticket 兑换 JWT 与用户信息（公开接口，票据本身即凭据）。
// 票据不透明且 24 字节随机，兑换时先删除再判断有效期，保证失败也不能重试（防重放）。
func oauthExchangeHandler(w http.ResponseWriter, r *http.Request) error {
	var req struct {
		Ticket string `json:"ticket"`
	}
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	ticket := strings.TrimSpace(req.Ticket)
	if ticket == "" {
		return NewHTTPError(400, "缺少 OAuth2 登录票据")
	}
	// 先取出并从表中删除（无条件下作废），再做有效期检查；这样兑换失败也无法重试猜测
	oauthMu.Lock()
	item, ok := oauthTicketM[ticket]
	delete(oauthTicketM, ticket) // 一次性：无论成功与否都作废，防止重放
	oauthMu.Unlock()
	if !ok || time.Now().After(item.Expires) {
		return NewHTTPError(400, "OAuth2 登录票据已失效，请重新登录")
	}
	return WriteJSON(w, 200, map[string]any{"token": item.Token, "user": item.User})
}

// ---------------------------------------------------------------------------
// 外部 OAuth2 请求
// ---------------------------------------------------------------------------

// oauthEntryAllowed 校验 OAuth 发起请求是否满足 ShunX 安全入口。
// 浏览器 <a> 跳转无法带自定义头，因此同时接受：
//   - X-ShunX-Entry 头（与密码登录一致，供脚本/客户端使用）；
//   - redirect 查询参数（即登录页当前路径，由前端拼上）。
//
// 未配置安全入口时直接放行。
func oauthEntryAllowed(r *http.Request, redirect string) bool {
	entry := strings.ToLower(strings.Trim(getEntryPath(), "/"))
	if entry == "" {
		return true
	}
	header := strings.ToLower(strings.Trim(strings.TrimSpace(r.Header.Get("X-ShunX-Entry")), "/"))
	if header == entry {
		return true
	}
	path := strings.ToLower(strings.Trim(strings.TrimSpace(redirect), "/"))
	return path == entry
}

// oauthCallbackURL 返回本面板对外暴露的回调地址。
// 由于配置里不保存面板域名，只能从当前请求推导；该值必须与第三方后台登记的
// redirect_uri 完全一致（协议、主机、路径都要一致），否则 start 与换码都会失败。
func oauthCallbackURL(r *http.Request) string {
	return requestBaseURL(r) + "/api/auth/oauth/callback"
}

// requestBaseURL 推导面板对外地址。
// 配置了 GRAW_PUBLIC_URL 时以它为准：反向代理未透传 X-Forwarded-*、或 Host 头被伪造时，
// 回调地址仍必须与第三方后台登记的 redirect_uri 完全一致。
// 未配置时优先取 X-Forwarded-Proto / X-Forwarded-Host 的第一个值，再退回 r.TLS 与 r.Host；
// 此路径下 Host 由请求方控制，影响限于让本次登录失败，公网部署建议显式配置 GRAW_PUBLIC_URL。
func requestBaseURL(r *http.Request) string {
	if pub := GetConfig().PublicURL; pub != "" {
		return pub
	}
	scheme := "http"
	if r.TLS != nil {
		scheme = "https"
	}
	if v := firstHeaderValue(r.Header.Get("X-Forwarded-Proto")); v == "http" || v == "https" {
		scheme = v
	}
	host := r.Host
	if v := firstHeaderValue(r.Header.Get("X-Forwarded-Host")); v != "" {
		host = v
	}
	return scheme + "://" + host
}

// firstHeaderValue 取逗号分隔头字段的第一个值（代理链场景常见 "https, http"）。
func firstHeaderValue(raw string) string {
	if i := strings.IndexByte(raw, ','); i >= 0 {
		raw = raw[:i]
	}
	return strings.TrimSpace(raw)
}

// oauthExchangeCode 用授权码向提供方换取 access_token。
//
// 请求为 application/x-www-form-urlencoded 的 POST，client_secret 放在请求体里
// （授权码模式的标准做法，要求 HTTPS 传输）。响应同时兼容 JSON 与表单编码两种格式。
// 失败行为：非 2xx、提供方返回 error、或响应中没有 access_token 时返回错误，
// 错误文本包含裁剪后的响应体便于排查。
func oauthExchangeCode(c oauthSettings, code, callback string) (string, error) {
	form := url.Values{}
	form.Set("grant_type", "authorization_code")
	form.Set("code", code)
	form.Set("client_id", c.ClientID)
	form.Set("client_secret", c.ClientSecret)
	form.Set("redirect_uri", callback)

	req, err := http.NewRequest(http.MethodPost, c.TokenURL, strings.NewReader(form.Encode()))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "Graw-Panel")
	resp, err := oauthHTTPClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, oauthMaxBodyBytes))
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return "", fmt.Errorf("HTTP %d: %s", resp.StatusCode, clipOAuthText(string(body)))
	}

	// 多数提供方在 Accept: application/json 时返回 JSON；少数只返回表单编码。
	var payload map[string]any
	if json.Unmarshal(body, &payload) == nil {
		if token := oauthFieldValue(payload, "access_token"); token != "" {
			return token, nil
		}
		if e := oauthFieldValue(payload, "error"); e != "" {
			desc := oauthFieldValue(payload, "error_description")
			return "", errors.New(strings.TrimSpace(e + " " + desc))
		}
	}
	if vals, err := url.ParseQuery(string(body)); err == nil {
		if token := vals.Get("access_token"); token != "" {
			return token, nil
		}
		if e := vals.Get("error"); e != "" {
			return "", errors.New(strings.TrimSpace(e + " " + vals.Get("error_description")))
		}
	}
	return "", errors.New("提供方未返回 access_token")
}

// oauthFetchUserinfo 用 access_token 拉取第三方用户信息，返回顶层字段 map。
// GitHub 默认隐藏用户邮箱，因此按 email 映射时额外补查 /user/emails（尽力而为，失败则留空）。
func oauthFetchUserinfo(c oauthSettings, token string) (map[string]any, error) {
	info, err := oauthJSONGET(c.UserinfoURL, token)
	if err != nil {
		return nil, err
	}
	// GitHub 默认隐藏用户邮箱：配置按 email 映射时补查 /user/emails。
	if oauthFieldValue(info, "email") == "" && isGithubAPI(c.UserinfoURL) {
		if email := oauthGithubPrimaryEmail(token); email != "" {
			info["email"] = email
		}
	}
	return info, nil
}

// oauthJSONGET 发起带 Bearer 令牌的 GET 并解析为 JSON 对象。
// 响应体最多读 1MB；非 2xx 或内容不是合法 JSON 对象时返回错误（错误含裁剪后的响应体）。
func oauthJSONGET(rawURL, token string) (map[string]any, error) {
	req, err := http.NewRequest(http.MethodGet, rawURL, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "Graw-Panel")
	resp, err := oauthHTTPClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, oauthMaxBodyBytes))
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, clipOAuthText(string(body)))
	}
	var info map[string]any
	if err := json.Unmarshal(body, &info); err != nil {
		return nil, errors.New("用户信息不是合法 JSON")
	}
	if info == nil {
		return nil, errors.New("用户信息为空")
	}
	return info, nil
}

// oauthGithubPrimaryEmail 补查 GitHub 主邮箱：优先 primary=true 的条目，否则取第一条。
// 任何网络/解析失败都返回空串（调用方按可选信息处理，不阻断登录）。
func oauthGithubPrimaryEmail(token string) string {
	req, err := http.NewRequest(http.MethodGet, "https://api.github.com/user/emails", nil)
	if err != nil {
		return ""
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "Graw-Panel")
	resp, err := oauthHTTPClient.Do(req)
	if err != nil {
		return ""
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return ""
	}
	body, _ := io.ReadAll(io.LimitReader(resp.Body, oauthMaxBodyBytes))
	var list []map[string]any
	if json.Unmarshal(body, &list) != nil {
		return ""
	}
	for _, item := range list {
		if b, _ := item["primary"].(bool); b {
			if e := oauthFieldValue(item, "email"); e != "" {
				return e
			}
		}
	}
	if len(list) > 0 {
		return oauthFieldValue(list[0], "email")
	}
	return ""
}

// isGithubAPI 判断用户信息地址是否指向 api.github.com（用于决定是否补查邮箱）。
func isGithubAPI(rawURL string) bool {
	u, err := url.Parse(rawURL)
	return err == nil && strings.EqualFold(u.Host, "api.github.com")
}

// ---------------------------------------------------------------------------
// 用户映射与工具函数
// ---------------------------------------------------------------------------

// resolveOAuthUser 把第三方用户信息映射为本地面板用户名，按三种策略依次尝试：
//  1. 配置了 bind_username：先校验第三方身份命中 bind_identity，再要求该面板用户存在；
//  2. 扫描用户表，找 oauth_provider + oauth_subject 完全匹配的已绑定用户；
//  3. auto_create 开启时创建 role=user 的普通用户（无模块权限），用户名冲突自动加序号。
//
// 参数 info 为提供方返回的顶层字段；失败时返回 HTTPError（400/403/500）。
// oauthUserMu 串行化整个函数，避免同一第三方账号并发首次登录创建出重复账号。
// 注意：绝不按「同名」直接登录已有账号，这是防止第三方冒充本地管理员的关键。
func resolveOAuthUser(c oauthSettings, info map[string]any) (string, *HTTPError) {
	// 提供方标识取用户信息地址的主机名；subject 取第三方稳定唯一 ID
	provider := oauthProviderKey(c)
	subject := oauthSubject(info)
	if subject == "" {
		return "", NewHTTPError(400, "OAuth2 用户信息缺少唯一标识（id/sub）")
	}
	oauthUserMu.Lock()
	defer oauthUserMu.Unlock()
	// 每次都在锁内重新加载用户表，保证基于最新数据判断绑定与重名
	users, err := LoadUsers()
	if err != nil {
		return "", NewHTTPError(500, "读取用户表失败")
	}
	if users == nil {
		users = Users{}
	}

	// 策略 1：固定绑定面板账号（必须同时配置允许的第三方账号标识，
	// 否则任何能访问该 OAuth 应用的账号都能登录到该面板账号）。
	if c.BindUsername != "" {
		if !oauthIdentityMatches(info, subject, c.BindIdentity) {
			return "", NewHTTPError(403, "当前第三方账号不在允许绑定的列表中")
		}
		if _, ok := users[c.BindUsername]; !ok {
			return "", NewHTTPError(403, "OAuth2 绑定的面板用户不存在："+c.BindUsername)
		}
		return c.BindUsername, nil
	}

	// 策略 2：只认已经保存过 oauth_provider + oauth_subject 的用户
	for name, u := range users {
		if u.Str("oauth_provider") == provider && u.Str("oauth_subject") == subject {
			return name, nil
		}
	}

	// 策略 3：自动创建普通用户（无模块权限），绝不按用户名匹配已有账号
	if !c.AutoCreate {
		return "", NewHTTPError(403, "该 OAuth2 账号尚未绑定面板用户，请先创建账号或启用自动创建")
	}
	// 用户名先按第三方字段生成并收敛字符集，再对已有账号做去重（base-2、base-3…）
	raw := oauthFieldValue(info, c.UsernameField)
	username := sanitizeOAuthUsername(raw, subject)
	base := username
	for i := 2; ; i++ {
		if _, exists := users[username]; !exists {
			break
		}
		username = base + "-" + strconv.Itoa(i)
		if i > 999 {
			return "", NewHTTPError(500, "自动创建 OAuth2 用户失败：重名过多")
		}
	}
	// 随机密码只为占位：用户不知道明文，只能继续通过 OAuth 登录
	pw, err := oauthRandomToken(24)
	if err != nil {
		return "", NewHTTPError(500, "自动创建 OAuth2 用户失败：随机密码生成失败")
	}
	hash, err := HashPassword(pw)
	if err != nil {
		return "", NewHTTPError(500, "自动创建 OAuth2 用户失败")
	}
	users[username] = User{
		"username":             username,
		"password":             hash,
		"role":                 "user",
		"must_change_password": false,
		"token_version":        0,
		"created_at":           float64(time.Now().Unix()),
		"oauth_provider":       provider,
		"oauth_subject":        subject,
	}
	if err := SaveUsers(users); err != nil {
		return "", NewHTTPError(500, "保存 OAuth2 用户失败")
	}
	AuditRecord("自动创建 OAuth2 用户", username, "", "提供方:"+c.ProviderName)
	return username, nil
}

// oauthIdentityMatches 判断第三方身份是否命中管理员配置的允许标识。
// 支持：稳定数值 ID（subject）、login / username / preferred_username、邮箱。
// 不比较 display name，因为显示名不唯一、可随意修改。
func oauthIdentityMatches(info map[string]any, subject, identity string) bool {
	target := strings.ToLower(strings.TrimSpace(identity))
	if target == "" {
		return false
	}
	if strings.ToLower(strings.TrimSpace(subject)) == target {
		return true
	}
	for _, k := range []string{"login", "username", "preferred_username", "email"} {
		if strings.ToLower(oauthFieldValue(info, k)) == target {
			return true
		}
	}
	return false
}

// oauthProviderKey 用用户信息地址的主机名作为绑定标识：
// 比使用「显示名称」稳定，管理员改展示名不会导致已绑定用户失效。
func oauthProviderKey(c oauthSettings) string {
	u, err := url.Parse(c.UserinfoURL)
	if err == nil && u.Host != "" {
		return strings.ToLower(u.Host)
	}
	return "oauth2"
}

// oauthSubject 取提供方返回的稳定唯一 ID：GitHub 为 id，OIDC 通常为 sub，
// 其他提供方兼容 user_id / uid；字段统一转字符串，缺失返回空串。
func oauthSubject(info map[string]any) string {
	for _, k := range []string{"id", "sub", "user_id", "uid"} {
		if v := oauthFieldValue(info, k); v != "" {
			return v
		}
	}
	return ""
}

// oauthFieldValue 读取顶层或点路径字段（如 "user.login"），并统一转成字符串。
// 支持 string / float64 / json.Number / bool；路径不存在或类型不支持时返回空串。
func oauthFieldValue(data map[string]any, field string) string {
	if field == "" {
		return ""
	}
	var cur any = data
	for _, part := range strings.Split(field, ".") {
		m, ok := cur.(map[string]any)
		if !ok {
			return ""
		}
		cur, ok = m[part]
		if !ok {
			return ""
		}
	}
	switch v := cur.(type) {
	case string:
		return strings.TrimSpace(v)
	case float64:
		return strconv.FormatFloat(v, 'f', -1, 64)
	case json.Number:
		return v.String()
	case bool:
		return strconv.FormatBool(v)
	}
	return ""
}

var oauthUsernameBad = regexp.MustCompile(`[^A-Za-z0-9_.-]+`)

// sanitizeOAuthUsername 把第三方用户名收敛到面板允许的账号字符集
// （只留字母数字与 _ . -，其余替换为 -），并保证长度在 2-24 之间；raw 不可用时用 subject 兜底。
// 生成的用户名仍需调用方做重名去重。
func sanitizeOAuthUsername(raw, subject string) string {
	s := oauthUsernameBad.ReplaceAllString(strings.TrimSpace(raw), "-")
	s = strings.Trim(s, "-._")
	if len(s) < 2 {
		s = "oauth-" + oauthUsernameBad.ReplaceAllString(strings.TrimSpace(subject), "-")
		s = strings.Trim(s, "-._")
	}
	if len(s) > 24 {
		s = s[:24]
	}
	s = strings.Trim(s, "-._")
	if len(s) < 2 {
		s = "oauth-user"
	}
	return s
}

// sanitizeOAuthRedirect 把登录后的跳转目标限制为面板内绝对路径，防止开放重定向：
// 空值、非 / 开头、以 // 开头（协议相对 URL）、绝对 URL 或带 Host 的输入统一回退到 "/"。
// 返回值只保留路径部分，原始 query 会被丢弃；调用方需要 query 时自行追加。
func sanitizeOAuthRedirect(raw string) string {
	raw = strings.TrimSpace(raw)
	if raw == "" || !strings.HasPrefix(raw, "/") || strings.HasPrefix(raw, "//") {
		return "/"
	}
	u, err := url.Parse(raw)
	if err != nil || u.IsAbs() || u.Host != "" {
		return "/"
	}
	if u.Path == "" {
		return "/"
	}
	return u.Path
}

// oauthRedirectError 把错误信息以 oauth_error 查询参数带回面板内路径并 302。
// 错误文本会裁剪到 300 字符，且始终先经 sanitizeOAuthRedirect 防止开放重定向；
// 响应带 no-store，避免错误 URL 被缓存或出现在共享缓存里。
func oauthRedirectError(w http.ResponseWriter, r *http.Request, redirect, message string) error {
	u, err := url.Parse(sanitizeOAuthRedirect(redirect))
	if err != nil {
		u, _ = url.Parse("/")
	}
	q := u.Query()
	q.Set("oauth_error", clipOAuthText(message))
	u.RawQuery = q.Encode()
	w.Header().Set("Cache-Control", "no-store")
	http.Redirect(w, r, u.String(), http.StatusFound)
	return nil
}

// clipOAuthText 去除首尾空白并把文本裁剪到最多 300 个字符（按 rune，避免截断多字节字符），
// 用于把第三方返回的错误/响应体安全地展示或写日志。
func clipOAuthText(s string) string {
	s = strings.TrimSpace(s)
	r := []rune(s)
	if len(r) > 300 {
		return string(r[:300]) + "…"
	}
	return s
}

// oauthRandomToken 生成 n 字节随机数的 URL-safe Base64 字符串（无填充），
// 用于 state / ticket / 自动创建用户的占位密码。
// 熵源不可用时返回错误而不是退回纳秒时间戳：可预测的 state / ticket 会直接削弱
// CSRF 防护与登录票据的安全性，宁可让本次登录失败。
func oauthRandomToken(n int) (string, error) {
	buf := make([]byte, n)
	if _, err := rand.Read(buf); err != nil {
		return "", fmt.Errorf("crypto/rand 不可用: %w", err)
	}
	return base64.RawURLEncoding.EncodeToString(buf), nil
}

// takeOAuthState 一次性取出并删除 state：无论是否过期都先删除，返回 nil,false 表示
// 不存在或已过期。删除即作废，保证同一个 state 只能用于一次回调（防 CSRF/重放）。
func takeOAuthState(state string) (*oauthState, bool) {
	if state == "" {
		return nil, false
	}
	oauthMu.Lock()
	defer oauthMu.Unlock()
	item, ok := oauthStates[state]
	if !ok {
		return nil, false
	}
	delete(oauthStates, state)
	if time.Now().After(item.Expires) {
		return nil, false
	}
	return &item, true
}

// pruneOAuthMaps 在持有 oauthMu 时清理两张表的过期项（调用方负责加锁）。
// 另设 5000 条上限：被恶意刷 /oauth/start 时整体重置 state 表，防止内存无界增长，
// 代价只是进行中的登录需要重新发起（state 本来 10 分钟过期）。
func pruneOAuthMaps(now time.Time) {
	for k, v := range oauthStates {
		if now.After(v.Expires) {
			delete(oauthStates, k)
		}
	}
	for k, v := range oauthTicketM {
		if now.After(v.Expires) {
			delete(oauthTicketM, k)
		}
	}
	// 防止恶意刷 /oauth/start 无限制占用内存：状态表超过上限时整体重置，
	// 代价只是正在进行中的 OAuth 登录需要重新发起（状态本来就 10 分钟过期）。
	if len(oauthStates) > 5000 {
		oauthStates = map[string]oauthState{}
	}
}
