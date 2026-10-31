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
	Last        float64 // 最近一次失败时间，用于淘汰长期无活动的记录
}

type globalRec struct {
	Count       int
	IPs         map[string]bool
	LockedUntil float64
	Last        float64
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
	rec.Last = now
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
	g.Last = now
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
	// 限流表按「IP+用户名 / 任意用户名 / 任意 IP」增长，必须定期淘汰：
	// 否则攻击者用海量随机用户名即可让进程内存持续上涨。
	if len(failures)+len(globalFailures)+len(entryFailures) > 2048 {
		pruneThrottleLocked(now)
	}
}

// pruneThrottleLocked 清掉长时间无活动且未处于锁定期的限流记录。
// 调用方必须已持有 throttleMu。
func pruneThrottleLocked(now float64) {
	cut := now - 2*entryWindow
	for k, v := range failures {
		if v.LockedUntil <= now && v.Last < cut {
			delete(failures, k)
		}
	}
	for k, v := range globalFailures {
		if v.LockedUntil <= now && v.Last < cut {
			delete(globalFailures, k)
		}
	}
	for k, hits := range entryFailures {
		// checkEntryThrottle 会把过期记录裁成空切片；空切片与长期无活动的 IP 都可删除。
		if len(hits) == 0 || hits[len(hits)-1] < cut {
			delete(entryFailures, k)
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
	// OAuth2.0 登录（可自定义提供方；内置 GitHub 模板由前端填充配置）
	registerOAuthRoutes(a)
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
	// 只有密码与 2FA 全部通过后才清零失败记录：否则知道密码的攻击者可在每次
	// 尝试间用「正确密码」重置计数，无限暴力枚举 6 位 TOTP 验证码。
	clearFailures(r, req.Username)
	if IsDefaultPassword(user.PasswordHash()) {
		public["must_change_password"] = true
		public["default_password"] = true
	}
	sid, err := CreateSession(user.Username(), ip, ParseDevice(ua))
	if err != nil {
		return NewHTTPError(500, "创建登录会话失败")
	}
	token, err := CreateToken(user.Username(), user.Int("token_version"), sid)
	if err != nil {
		return err
	}
	AuditRecord("登录成功", req.Username, ip, "")
	RecordLogin(req.Username, ip, ua, "success", "")
	// 清除换行后再落日志，避免用户名/来源 IP 伪造日志行（CWE-117）。
	safeUser := strings.ReplaceAll(strings.ReplaceAll(user.Username(), "\r", ""), "\n", "")
	safeIP := strings.ReplaceAll(strings.ReplaceAll(ip, "\r", ""), "\n", "")
	log.Printf("graw: 用户 %s 登录成功（IP %s）", safeUser, safeIP)
	return WriteJSON(w, 200, map[string]any{"token": token, "user": public})
}

func logoutHandler(w http.ResponseWriter, r *http.Request) error {
	u := CurrentUser(r)
	// 只吊销当前设备的会话；其它设备的登录态不应因为一次退出而全部失效。
	revoked := false
	if claims := DecodeToken(BearerToken(r)); claims != nil && claims.Sid != "" {
		revoked = RevokeSession(claims.Sid)
	}
	if !revoked {
		// 找不到当前会话（记录已过期/被清理）时退回全局吊销，保证登出后令牌立即失效。
		if err := BumpTokenVersion(u.Username()); err != nil {
			return err
		}
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
	// 只返回脱敏字段：CurrentUser 是完整用户记录（含 password 哈希与 otp_secret），
	// 直接回传等于把密码哈希/TOTP 密钥交给前端（与 Python _public_user 对齐）。
	return WriteJSON(w, 200, PublicUser(CurrentUser(r)))
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
	if err := validatePasswordStrength(req.NewPassword, u.Username(), true); err != nil {
		return err
	}
	if req.NewPassword == DefaultPassword {
		return NewHTTPError(400, "新密码不能使用默认密码，请更换")
	}
	// 先用缓存校验一次原密码：错误时直接返回，不必为新密码计算 bcrypt。
	if full, _ := GetUser(u.Username()); full != nil && !VerifyPassword(req.OldPassword, full.PasswordHash()) {
		recordFailure(r, u.Username())
		return NewHTTPError(400, "原密码错误")
	}
	hash, err := HashPassword(req.NewPassword)
	if err != nil {
		return err
	}
	// 密码与 token_version 在同一事务内更新：改密后旧令牌立即失效，
	// 也避免与管理员改权限等写操作基于旧快照互相覆盖。
	newVersion := 0
	err = UpdateUsers(func(users Users) error {
		target, ok := users[u.Username()]
		if !ok {
			return NewHTTPError(404, "用户不存在")
		}
		// 锁内复核原密码，防止检查与写入之间被并发改密。
		if !VerifyPassword(req.OldPassword, target.PasswordHash()) {
			return NewHTTPError(400, "原密码错误")
		}
		if VerifyPassword(req.NewPassword, target.PasswordHash()) {
			return NewHTTPError(400, "新密码不能与原密码相同")
		}
		newVersion = target.Int("token_version") + 1
		target["password"] = hash
		target["must_change_password"] = false
		target["token_version"] = newVersion
		return nil
	})
	if err != nil {
		// 只有「原密码错误」计入暴力破解；新密码与原密码相同属于表单问题。
		if he, ok := err.(*HTTPError); ok && he.Status == 400 && he.Detail == "原密码错误" {
			recordFailure(r, u.Username())
		}
		return err
	}
	// 终止其它设备上的会话；当前设备用新 sid 重新签发令牌，避免把自己踢下线。
	RevokeUserSessions(u.Username())
	clearFailures(r, u.Username())
	sid, err := CreateSession(u.Username(), ClientIP(r), ParseDevice(r.Header.Get("User-Agent")))
	if err != nil {
		return NewHTTPError(500, "创建会话失败")
	}
	token, err := CreateToken(u.Username(), newVersion, sid)
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
	hash, err := HashPassword(req.Password)
	if err != nil {
		return err
	}
	// 查重与写入放在同一事务里，避免两个并发创建同名账号时后者覆盖前者。
	if err := UpdateUsers(func(users Users) error {
		if _, exists := users[req.Username]; exists {
			return NewHTTPError(400, "用户已存在")
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
		return nil
	}); err != nil {
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
	// 锁外完成密码校验与 bcrypt 计算，避免占用用户表事务锁。
	var newHash string
	passwordPresent := false
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
		newHash, passwordPresent = hash, true
	}
	changedPassword := false
	// 全部字段修改在同一事务内完成：并发改权限/改密码时不会互相覆盖。
	err := UpdateUsers(func(users Users) error {
		target, ok := users[username]
		if !ok {
			return NewHTTPError(404, "用户不存在")
		}
		superBefore := superAdminCount(users)
		if passwordPresent {
			target["password"] = newHash
			changedPassword = true
			// 改密必须同时递增 token_version：否则旧令牌在会话被清理前仍然有效。
			target["token_version"] = target.Int("token_version") + 1
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
		return nil
	})
	if err != nil {
		return err
	}
	if changedPassword {
		RevokeUserSessions(username)
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
	if err := UpdateUsers(func(users Users) error {
		target, ok := users[username]
		if !ok {
			return NewHTTPError(404, "用户不存在")
		}
		if _, full := UserPerms(target); full && superAdminCount(users) <= 1 {
			return NewHTTPError(400, "必须保留至少一个完整权限的超级管理员")
		}
		delete(users, username)
		return nil
	}); err != nil {
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

// otpSetupHandler 生成/重置两步验证密钥。
// 必须再次输入当前密码：否则会话被劫持后，攻击者可自行绑定新密钥把账号锁死。
func otpSetupHandler(w http.ResponseWriter, r *http.Request) error {
	u := CurrentUser(r)
	var req struct {
		Password string `json:"password"`
	}
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	if req.Password == "" {
		return NewHTTPError(422, "请输入当前密码")
	}
	// 这里会校验密码，必须套用登录同款限流，避免成为绕过登录限流的爆破入口。
	if err := checkThrottle(r, u.Username()); err != nil {
		return err
	}
	secret, err := GenerateOTPSecret()
	if err != nil {
		return err
	}
	if err := UpdateUsers(func(users Users) error {
		full, ok := users[u.Username()]
		if !ok {
			return NewHTTPError(404, "用户不存在")
		}
		if !VerifyPassword(req.Password, full.PasswordHash()) {
			recordFailure(r, u.Username())
			return NewHTTPError(400, "密码错误")
		}
		if full.Bool("otp_enabled") {
			return NewHTTPError(400, "两步验证已启用，如需重置请先关闭")
		}
		full["otp_secret"] = secret
		full["otp_enabled"] = false
		return nil
	}); err != nil {
		return err
	}
	clearFailures(r, u.Username())
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
	// 动态码校验必须限流：6 位数字在无限重试下可被暴力枚举。
	if err := checkThrottle(r, u.Username()); err != nil {
		return err
	}
	if err := UpdateUsers(func(users Users) error {
		full, ok := users[u.Username()]
		if !ok || full.Str("otp_secret") == "" {
			return NewHTTPError(400, "请先生成两步验证密钥")
		}
		if full.Bool("otp_enabled") {
			return NewHTTPError(400, "两步验证已启用")
		}
		if !VerifyTOTP(full.Str("otp_secret"), req.Code) {
			recordFailure(r, u.Username())
			return NewHTTPError(400, "验证码错误")
		}
		full["otp_enabled"] = true
		return nil
	}); err != nil {
		return err
	}
	clearFailures(r, u.Username())
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
	// 关闭 2FA 同样要限流，避免动态码被暴力枚举后降级账号安全。
	if err := checkThrottle(r, u.Username()); err != nil {
		return err
	}
	if err := UpdateUsers(func(users Users) error {
		full, ok := users[u.Username()]
		if !ok || !full.Bool("otp_enabled") {
			return NewHTTPError(400, "两步验证未启用")
		}
		if !VerifyTOTP(full.Str("otp_secret"), req.Code) {
			recordFailure(r, u.Username())
			return NewHTTPError(400, "验证码错误")
		}
		full["otp_enabled"] = false
		full["otp_secret"] = ""
		return nil
	}); err != nil {
		return err
	}
	clearFailures(r, u.Username())
	AuditRecord("关闭两步验证", u.Username(), ClientIP(r), "")
	return WriteJSON(w, 200, map[string]any{"ok": true})
}
