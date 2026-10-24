package graw

import (
	"encoding/json"
	"log"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode"
)

// ---------------------------------------------------------------------------
// 登录暴力破解防护（进程内限流，与 Python 版语义一致）
// ---------------------------------------------------------------------------
const (
	maxAttempts       = 5
	lockSeconds       = 10 * 60
	globalMinIPs      = 3
	globalMaxAttempts = 30
	globalLockSeconds = 5 * 60
	entryMaxFailures  = 10
	entryWindow       = 10 * 60
)

type failureRec struct {
	Count       int
	LockedUntil float64
}

type globalRec struct {
	Count       int
	IPs         map[string]bool
	LockedUntil float64
}

var (
	throttleMu     sync.Mutex
	failures       = map[string]*failureRec{}
	globalFailures = map[string]*globalRec{}
	entryFailures  = map[string][]float64{}
	dummyHashOnce  sync.Once
	dummyHash      string
)

func throttleKey(r *http.Request, username string) string {
	return ClientIP(r) + "|" + username
}

func checkThrottle(r *http.Request, username string) *HTTPError {
	now := float64(time.Now().Unix())
	throttleMu.Lock()
	rec := failures[throttleKey(r, username)]
	lockedUntil := 0.0
	if rec != nil {
		lockedUntil = rec.LockedUntil
	}
	g := globalFailures[username]
	globalLocked := 0.0
	if g != nil {
		globalLocked = g.LockedUntil
	}
	throttleMu.Unlock()
	if lockedUntil > now {
		mins := int((lockedUntil-now)/60) + 1
		return NewHTTPError(403, "登录失败次数过多，请 "+itoa(mins)+" 分钟后再试")
	}
	if globalLocked > now {
		return NewHTTPError(403, "登录失败次数过多，请稍后再试")
	}
	return nil
}

func recordFailure(r *http.Request, username string) {
	ip := ClientIP(r)
	key := throttleKey(r, username)
	now := float64(time.Now().Unix())
	throttleMu.Lock()
	defer throttleMu.Unlock()
	rec := failures[key]
	if rec == nil {
		rec = &failureRec{}
		failures[key] = rec
	}
	if rec.LockedUntil <= now {
		rec.Count++
		if rec.Count >= maxAttempts {
			rec.Count = 0
			rec.LockedUntil = now + lockSeconds
		}
	}
	g := globalFailures[username]
	if g == nil {
		g = &globalRec{IPs: map[string]bool{}}
		globalFailures[username] = g
	}
	if g.LockedUntil <= now {
		g.Count++
		if len(g.IPs) < globalMaxAttempts*4 {
			g.IPs[ip] = true
		}
		if len(g.IPs) >= globalMinIPs && g.Count >= globalMaxAttempts {
			g.Count = 0
			g.LockedUntil = now + globalLockSeconds
		}
	}
	if len(failures) > 100000 {
		for k, v := range failures {
			if v.LockedUntil <= now && v.Count == 0 {
				delete(failures, k)
			}
		}
	}
}

func clearFailures(r *http.Request, username string) {
	throttleMu.Lock()
	delete(failures, throttleKey(r, username))
	delete(globalFailures, username)
	throttleMu.Unlock()
}

func checkEntryThrottle(r *http.Request) *HTTPError {
	ip := ClientIP(r)
	now := float64(time.Now().Unix())
	throttleMu.Lock()
	hits := entryFailures[ip]
	cut := now - entryWindow
	kept := hits[:0]
	for _, t := range hits {
		if t > cut {
			kept = append(kept, t)
		}
	}
	entryFailures[ip] = kept
	over := len(kept) >= entryMaxFailures
	throttleMu.Unlock()
	if over {
		verifyDummy("graw-entry-throttle")
		return NewHTTPError(401, "用户名或密码错误")
	}
	return nil
}

func recordEntryFailure(r *http.Request) {
	throttleMu.Lock()
	entryFailures[ClientIP(r)] = append(entryFailures[ClientIP(r)], float64(time.Now().Unix()))
	throttleMu.Unlock()
}

func verifyDummy(password string) {
	dummyHashOnce.Do(func() {
		h, _ := HashPassword("graw-timing-equalizer")
		dummyHash = h
	})
	VerifyPassword(password, dummyHash)
}

func itoa(n int) string {
	return strconv.Itoa(n)
}

// ---------------------------------------------------------------------------
// 密码策略
// ---------------------------------------------------------------------------
var usernamePattern = regexp.MustCompile(`^[A-Za-z0-9_.-]+$`)

func validatePasswordStrength(password, username string, strict bool) *HTTPError {
	if len(password) < 8 {
		return NewHTTPError(400, "密码至少 8 位")
	}
	if len(password) > 128 {
		return NewHTTPError(400, "密码不能超过 128 位")
	}
	if len([]byte(password)) > 72 {
		return NewHTTPError(400, "密码过长（超过 72 字节），请缩短")
	}
	if strings.EqualFold(password, username) {
		return NewHTTPError(400, "密码不能与用户名相同")
	}
	if strict {
		hasLetter, hasDigit := false, false
		for _, c := range password {
			if unicode.IsLetter(c) {
				hasLetter = true
			}
			if unicode.IsDigit(c) {
				hasDigit = true
			}
		}
		if !hasLetter || !hasDigit {
			return NewHTTPError(400, "密码必须同时包含字母和数字")
		}
	}
	return nil
}

func normalizePerms(raw any) (any, *HTTPError) {
	if raw == nil {
		return nil, nil
	}
	list, ok := raw.([]any)
	if !ok {
		return nil, NewHTTPError(400, "perms 必须是模块数组")
	}
	picked := map[string]bool{}
	unknown := []string{}
	for _, item := range list {
		s, ok := item.(string)
		if !ok || !moduleSet[s] {
			unknown = append(unknown, toString(item))
			continue
		}
		picked[s] = true
	}
	if len(unknown) > 0 {
		return nil, NewHTTPError(400, "未知模块："+strings.Join(unknown, "、"))
	}
	out := []string{}
	for _, m := range MODULES {
		if picked[m] {
			out = append(out, m)
		}
	}
	return out, nil
}

func toString(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	b, _ := json.Marshal(v)
	return string(b)
}

func superAdminCount(users Users) int {
	n := 0
	for _, u := range users {
		if _, full := UserPerms(u); full {
			n++
		}
	}
	return n
}

// verifyEntry 由 ShunX 模块实现（未启用时直接放行）。
func registerAuthRoutes(a *App) {
	a.Post("/api/auth/login", public(), loginHandler)
	a.Post("/api/auth/logout", loginOnly(), logoutHandler)
	a.Get("/api/auth/sessions", loginOnly(), sessionsHandler)
	a.Post("/api/auth/sessions/{sid}/kick", loginOnly(), kickSessionHandler)
	a.Post("/api/auth/sessions/kick-all", adminOnly(), kickAllHandler)
	a.Post("/api/auth/verify-password", loginOnly(), verifyPasswordHandler)
	a.Get("/api/auth/me", loginOnly(), meHandler)
	a.Post("/api/auth/password", loginOnly(), changePasswordHandler)
	a.Get("/api/auth/users", adminOnly(), listUsersHandler)
	a.Get("/api/auth/modules", adminOnly(), modulesHandler)
	a.Post("/api/auth/users", adminOnly(), createUserHandler)
	a.Put("/api/auth/users/{username}", adminOnly(), updateUserHandler)
	a.Delete("/api/auth/users/{username}", adminOnly(), deleteUserHandler)
	a.Get("/api/auth/2fa/status", loginOnly(), otpStatusHandler)
	a.Post("/api/auth/2fa/setup", loginOnly(), otpSetupHandler)
	a.Post("/api/auth/2fa/enable", loginOnly(), otpEnableHandler)
	a.Post("/api/auth/2fa/disable", loginOnly(), otpDisableHandler)
}

type loginRequest struct {
	Username string `json:"username"`
	Password string `json:"password"`
	OTPCode  string `json:"otp_code"`
}

func loginHandler(w http.ResponseWriter, r *http.Request) error {
	var req loginRequest
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	if req.Username == "" || len(req.Username) > 64 || req.Password == "" || len(req.Password) > 128 {
		return NewHTTPError(422, "用户名或密码格式不正确")
	}
	ip := ClientIP(r)
	ua := r.Header.Get("User-Agent")
	if err := checkThrottle(r, req.Username); err != nil {
		return err
	}
	if err := checkEntryThrottle(r); err != nil {
		return err
	}
	if err := VerifyEntry(r); err != nil {
		recordEntryFailure(r)
		recordFailure(r, req.Username)
		AuditRecord("登录失败", req.Username, ip, "安全入口校验未通过")
		RecordLogin(req.Username, ip, ua, "failed", "安全入口校验未通过")
		return err
	}
	user, _ := GetUser(req.Username)
	if user == nil {
		verifyDummy(req.Password)
		recordFailure(r, req.Username)
		AuditRecord("登录失败", req.Username, ip, "用户不存在")
		RecordLogin(req.Username, ip, ua, "failed", "用户不存在")
		return NewHTTPError(401, "用户名或密码错误")
	}
	if !VerifyPassword(req.Password, user.PasswordHash()) {
		recordFailure(r, req.Username)
		AuditRecord("登录失败", req.Username, ip, "密码错误")
		RecordLogin(req.Username, ip, ua, "failed", "密码错误")
		return NewHTTPError(401, "用户名或密码错误")
	}
	clearFailures(r, req.Username)
	public := PublicUser(user)
	if user.Bool("otp_enabled") {
		code := strings.TrimSpace(req.OTPCode)
		if code == "" {
			AuditRecord("登录待验证", req.Username, ip, "已通过密码，等待 2FA 验证码")
			return WriteJSON(w, 200, map[string]any{
				"otp_required": true, "username": user.Username(), "token": "", "user": nil,
			})
		}
		if !VerifyTOTP(user.Str("otp_secret"), code) {
			recordFailure(r, req.Username)
			AuditRecord("登录失败", req.Username, ip, "2FA 验证码错误")
			RecordLogin(req.Username, ip, ua, "failed", "2FA 验证码错误")
			return NewHTTPError(401, "两步验证码错误")
		}
	}
	if IsDefaultPassword(user.PasswordHash()) {
		public["must_change_password"] = true
		public["default_password"] = true
	}
	sid := CreateSession(user.Username(), ip, ParseDevice(ua))
	token, err := CreateToken(user.Username(), user.Int("token_version"), sid)
	if err != nil {
		return err
	}
	AuditRecord("登录成功", req.Username, ip, "")
	RecordLogin(req.Username, ip, ua, "success", "")
	log.Printf("graw: 用户 %s 登录成功（IP %s）", user.Username(), ip)
	return WriteJSON(w, 200, map[string]any{"token": token, "user": public})
}

func logoutHandler(w http.ResponseWriter, r *http.Request) error {
	u := CurrentUser(r)
	if err := BumpTokenVersion(u.Username()); err != nil {
		return err
	}
	clearFailures(r, u.Username())
	AuditRecord("退出登录", u.Username(), ClientIP(r), "")
	return WriteJSON(w, 200, map[string]any{"ok": true})
}

func sessionsHandler(w http.ResponseWriter, r *http.Request) error {
	u := CurrentUser(r)
	limit := QueryInt(r, "limit", 100)
	var items []SessionItem
	if _, full := UserPerms(u); full {
		items = ListSessions("", limit)
	} else {
		items = ListSessions(u.Username(), limit)
	}
	return WriteJSON(w, 200, map[string]any{"sessions": items})
}

func kickSessionHandler(w http.ResponseWriter, r *http.Request) error {
	u := CurrentUser(r)
	sid := PathParam(r, "sid")
	owner := ""
	for _, s := range ListSessions("", 500) {
		if s.SID == sid {
			owner = s.Username
			break
		}
	}
	if owner == "" {
		return NewHTTPError(404, "会话不存在或已过期")
	}
	if owner != u.Username() {
		if _, full := UserPerms(u); !full {
			return NewHTTPError(403, "只能操作自己的会话")
		}
	}
	if !RevokeSession(sid) {
		return NewHTTPError(404, "会话不存在或已过期")
	}
	AuditRecord("踢出会话", owner, ClientIP(r), "设备会话被 "+u.Username()+" 强制下线")
	return WriteJSON(w, 200, map[string]any{"ok": true})
}

func kickAllHandler(w http.ResponseWriter, r *http.Request) error {
	admin := CurrentUser(r)
	var req struct {
		Username string `json:"username"`
	}
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	target := strings.TrimSpace(req.Username)
	if target == "" {
		return NewHTTPError(400, "请指定要强制下线的用户")
	}
	if u, _ := GetUser(target); u == nil {
		return NewHTTPError(404, "用户不存在")
	}
	if err := BumpTokenVersion(target); err != nil {
		return err
	}
	AuditRecord("强制下线", target, ClientIP(r), "全部设备被 "+admin.Username()+" 强制下线")
	return WriteJSON(w, 200, map[string]any{"ok": true})
}

func verifyPasswordHandler(w http.ResponseWriter, r *http.Request) error {
	u := CurrentUser(r)
	var req struct {
		Password string `json:"password"`
	}
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	if req.Password == "" || len(req.Password) > 128 {
		return NewHTTPError(422, "密码格式不正确")
	}
	if err := checkThrottle(r, u.Username()); err != nil {
		return err
	}
	full, _ := GetUser(u.Username())
	if full == nil || !VerifyPassword(req.Password, full.PasswordHash()) {
		recordFailure(r, u.Username())
		return NewHTTPError(400, "密码错误")
	}
	clearFailures(r, u.Username())
	AuditRecord("二次确认通过", u.Username(), ClientIP(r), "")
	return WriteJSON(w, 200, map[string]any{"ok": true})
}

func meHandler(w http.ResponseWriter, r *http.Request) error {
	return WriteJSON(w, 200, CurrentUser(r))
}

func changePasswordHandler(w http.ResponseWriter, r *http.Request) error {
	u := CurrentUser(r)
	var req struct {
		OldPassword string `json:"old_password"`
		NewPassword string `json:"new_password"`
	}
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	if req.OldPassword == "" || req.NewPassword == "" ||
		len(req.OldPassword) > 128 || len(req.NewPassword) > 128 {
		return NewHTTPError(422, "密码格式不正确")
	}
	if err := checkThrottle(r, u.Username()); err != nil {
		return err
	}
	full, _ := GetUser(u.Username())
	if full == nil || !VerifyPassword(req.OldPassword, full.PasswordHash()) {
		recordFailure(r, u.Username())
		return NewHTTPError(400, "原密码错误")
	}
	if VerifyPassword(req.NewPassword, full.PasswordHash()) {
		return NewHTTPError(400, "新密码不能与原密码相同")
	}
	if err := validatePasswordStrength(req.NewPassword, u.Username(), true); err != nil {
		return err
	}
	if req.NewPassword == DefaultPassword {
		return NewHTTPError(400, "新密码不能使用默认密码，请更换")
	}
	users, err := LoadUsers()
	if err != nil {
		return err
	}
	target, ok := users[u.Username()]
	if !ok {
		return NewHTTPError(404, "用户不存在")
	}
	hash, err := HashPassword(req.NewPassword)
	if err != nil {
		return err
	}
	target["password"] = hash
	target["must_change_password"] = false
	if err := SaveUsers(users); err != nil {
		return err
	}
	if err := BumpTokenVersion(u.Username()); err != nil {
		return err
	}
	clearFailures(r, u.Username())
	users, _ = LoadUsers()
	newTV := 0
	if t, ok := users[u.Username()]; ok {
		newTV = t.Int("token_version")
	}
	token, err := CreateToken(u.Username(), newTV, "")
	if err != nil {
		return err
	}
	AuditRecord("修改密码", u.Username(), ClientIP(r), "")
	return WriteJSON(w, 200, map[string]any{"ok": true, "token": token})
}

func listUsersHandler(w http.ResponseWriter, r *http.Request) error {
	users, err := LoadUsers()
	if err != nil {
		return err
	}
	out := make([]map[string]any, 0, len(users))
	for _, u := range users {
		out = append(out, PublicUser(u))
	}
	return WriteJSON(w, 200, out)
}

func modulesHandler(w http.ResponseWriter, r *http.Request) error {
	return WriteJSON(w, 200, map[string]any{"modules": MODULES})
}

func createUserHandler(w http.ResponseWriter, r *http.Request) error {
	admin := CurrentUser(r)
	var req struct {
		Username string `json:"username"`
		Password string `json:"password"`
		Role     string `json:"role"`
		Perms    any    `json:"perms"`
	}
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	if len(req.Username) < 2 || len(req.Username) > 32 || !usernamePattern.MatchString(req.Username) {
		return NewHTTPError(422, "用户名需为 2-32 位字母、数字、下划线、点或短横线")
	}
	if req.Role == "" {
		req.Role = "user"
	}
	if req.Role != "admin" && req.Role != "user" {
		return NewHTTPError(400, "角色无效")
	}
	if req.Password == DefaultPassword {
		return NewHTTPError(400, "不能将默认密码作为账号密码")
	}
	if err := validatePasswordStrength(req.Password, req.Username, true); err != nil {
		return err
	}
	var perms any
	if req.Role == "admin" {
		p, err := normalizePerms(req.Perms)
		if err != nil {
			return err
		}
		perms = p
	}
	users, err := LoadUsers()
	if err != nil {
		return err
	}
	if users == nil {
		users = Users{}
	}
	if _, exists := users[req.Username]; exists {
		return NewHTTPError(400, "用户已存在")
	}
	hash, err := HashPassword(req.Password)
	if err != nil {
		return err
	}
	users[req.Username] = User{
		"username":             req.Username,
		"password":             hash,
		"role":                 req.Role,
		"perms":                perms,
		"must_change_password": false,
		"token_version":        0,
		"created_at":           float64(time.Now().Unix()),
	}
	if err := SaveUsers(users); err != nil {
		return err
	}
	AuditRecord("创建用户", admin.Username(), ClientIP(r), "目标:"+req.Username+" 角色:"+req.Role)
	return WriteJSON(w, 200, map[string]any{"ok": true})
}

func updateUserHandler(w http.ResponseWriter, r *http.Request) error {
	admin := CurrentUser(r)
	username := PathParam(r, "username")
	var raw map[string]json.RawMessage
	if err := DecodeJSON(r, &raw); err != nil {
		return err
	}
	users, err := LoadUsers()
	if err != nil {
		return err
	}
	target, ok := users[username]
	if !ok {
		return NewHTTPError(404, "用户不存在")
	}
	superBefore := superAdminCount(users)
	changedPassword := false
	if v, present := raw["password"]; present && string(v) != "null" {
		var pw string
		if err := json.Unmarshal(v, &pw); err != nil {
			return NewHTTPError(400, "密码格式错误")
		}
		if pw == DefaultPassword {
			return NewHTTPError(400, "不能将默认密码作为账号密码")
		}
		if err := validatePasswordStrength(pw, username, true); err != nil {
			return err
		}
		hash, err := HashPassword(pw)
		if err != nil {
			return err
		}
		target["password"] = hash
		changedPassword = true
		if v2, ok2 := raw["must_change_password"]; ok2 && string(v2) != "null" {
			var b bool
			_ = json.Unmarshal(v2, &b)
			target["must_change_password"] = b
		} else {
			target["must_change_password"] = true
		}
	}
	if v, present := raw["role"]; present && string(v) != "null" {
		var role string
		_ = json.Unmarshal(v, &role)
		if role != "admin" && role != "user" {
			return NewHTTPError(400, "角色无效")
		}
		target["role"] = role
		if role != "admin" {
			target["perms"] = nil
		}
	}
	if v, present := raw["must_change_password"]; present && string(v) != "null" {
		var b bool
		_ = json.Unmarshal(v, &b)
		target["must_change_password"] = b
	}
	if v, present := raw["perms"]; present {
		var rawPerms any
		if string(v) != "null" {
			if err := json.Unmarshal(v, &rawPerms); err != nil {
				return NewHTTPError(400, "perms 必须是模块数组")
			}
		}
		p, err := normalizePerms(rawPerms)
		if err != nil {
			return err
		}
		if target.Role() == "admin" {
			target["perms"] = p
		} else {
			target["perms"] = nil
		}
	}
	if superBefore >= 1 && superAdminCount(users) < 1 {
		return NewHTTPError(400, "必须保留至少一个完整权限的超级管理员")
	}
	if err := SaveUsers(users); err != nil {
		return err
	}
	if changedPassword {
		_ = BumpTokenVersion(username)
	}
	AuditRecord("更新用户", admin.Username(), ClientIP(r), "目标:"+username)
	return WriteJSON(w, 200, map[string]any{"ok": true})
}

func deleteUserHandler(w http.ResponseWriter, r *http.Request) error {
	admin := CurrentUser(r)
	username := PathParam(r, "username")
	if username == admin.Username() {
		return NewHTTPError(400, "不能删除当前登录账号")
	}
	users, err := LoadUsers()
	if err != nil {
		return err
	}
	target, ok := users[username]
	if !ok {
		return NewHTTPError(404, "用户不存在")
	}
	if _, full := UserPerms(target); full && superAdminCount(users) <= 1 {
		return NewHTTPError(400, "必须保留至少一个完整权限的超级管理员")
	}
	delete(users, username)
	if err := SaveUsers(users); err != nil {
		return err
	}
	AuditRecord("删除用户", admin.Username(), ClientIP(r), "目标:"+username)
	return WriteJSON(w, 200, map[string]any{"ok": true})
}

func otpStatusHandler(w http.ResponseWriter, r *http.Request) error {
	u := CurrentUser(r)
	full, _ := GetUser(u.Username())
	return WriteJSON(w, 200, map[string]any{
		"otp_enabled": full != nil && full.Bool("otp_enabled"),
		"has_secret":  full != nil && full.Str("otp_secret") != "",
	})
}

func otpSetupHandler(w http.ResponseWriter, r *http.Request) error {
	u := CurrentUser(r)
	users, err := LoadUsers()
	if err != nil {
		return err
	}
	full, ok := users[u.Username()]
	if !ok {
		return NewHTTPError(404, "用户不存在")
	}
	if full.Bool("otp_enabled") {
		return NewHTTPError(400, "两步验证已启用，如需重置请先关闭")
	}
	secret := GenerateOTPSecret()
	full["otp_secret"] = secret
	full["otp_enabled"] = false
	if err := SaveUsers(users); err != nil {
		return err
	}
	return WriteJSON(w, 200, map[string]any{
		"secret": secret, "otpauth_uri": OTPAuthURI(secret, u.Username(), "Graw"),
	})
}

func otpEnableHandler(w http.ResponseWriter, r *http.Request) error {
	u := CurrentUser(r)
	var req struct {
		Code string `json:"code"`
	}
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	users, err := LoadUsers()
	if err != nil {
		return err
	}
	full, ok := users[u.Username()]
	if !ok || full.Str("otp_secret") == "" {
		return NewHTTPError(400, "请先生成两步验证密钥")
	}
	if full.Bool("otp_enabled") {
		return NewHTTPError(400, "两步验证已启用")
	}
	if !VerifyTOTP(full.Str("otp_secret"), req.Code) {
		return NewHTTPError(400, "验证码错误")
	}
	full["otp_enabled"] = true
	if err := SaveUsers(users); err != nil {
		return err
	}
	AuditRecord("启用两步验证", u.Username(), ClientIP(r), "")
	return WriteJSON(w, 200, map[string]any{"ok": true})
}

func otpDisableHandler(w http.ResponseWriter, r *http.Request) error {
	u := CurrentUser(r)
	var req struct {
		Code string `json:"code"`
	}
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	users, err := LoadUsers()
	if err != nil {
		return err
	}
	full, ok := users[u.Username()]
	if !ok || !full.Bool("otp_enabled") {
		return NewHTTPError(400, "两步验证未启用")
	}
	if !VerifyTOTP(full.Str("otp_secret"), req.Code) {
		return NewHTTPError(400, "验证码错误")
	}
	full["otp_enabled"] = false
	full["otp_secret"] = ""
	if err := SaveUsers(users); err != nil {
		return err
	}
	AuditRecord("关闭两步验证", u.Username(), ClientIP(r), "")
	return WriteJSON(w, 200, map[string]any{"ok": true})
}
