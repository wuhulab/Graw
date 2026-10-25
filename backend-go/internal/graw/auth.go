package graw

// auth.go —— 用户模型、密码/JWT 鉴权与请求级身份解析。
//
// 一次 HTTP 请求的鉴权链路（Authenticate）：
//
//	Authorization: Bearer <jwt> → DecodeToken 验签与过期 → GetUser 查用户
//	→ token_version 校验（改密/踢出后旧令牌立即失效）→ SessionActive 校验会话未吊销
//	→ 刷新在线时间 → 默认密码账号按路由级别拦截（403）。
//
// 安全约束与约定：
//   - JWT 只接受 HS256，密钥持久化在 data/secret.key（0600），首次启动自动生成；密钥丢失等于全员下线；
//   - 密码使用 bcrypt（与 Python 旧版互通）；默认密码 admin123 以哈希写入 users.json，改密后自然失效；
//   - User 以 map 保存原始字段，保证兼容 Python 旧版写入的未知字段，不要改成强类型结构；
//   - token_version 是「全局吊销开关」：BumpTokenVersion 递增即让该用户全部旧令牌失效；
//   - 浏览器无法给 WebSocket 设置 Authorization 头，WS 改用 ?token= 查询参数（AuthenticateWS）。

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"golang.org/x/crypto/bcrypt"
)

// HTTPError 是可携带 HTTP 状态码的业务错误（对应 FastAPI 的 HTTPException）。
// handler 返回它时由 writeErr 直接映射为 {detail: ...} 响应；其余错误统一 500。
type HTTPError struct {
	Status int
	Detail string
}

// Error 实现 error 接口，Detail 即客户端可见文案。
// 注意：Detail 会原样回显给客户端，不要放内部路径/命令输出/凭据。
func (e *HTTPError) Error() string { return e.Detail }

// NewHTTPError 构造业务错误。
func NewHTTPError(status int, detail string) *HTTPError {
	return &HTTPError{Status: status, Detail: detail}
}

// User 使用 map 保存原始字段，保证与 Python 版 users.json 的未知字段兼容。
// 读写时统一走下面的 Str/Bool/Int/Float 访问器，避免到处做类型断言。
type User map[string]any

// Str 读取字符串字段；字段缺失或类型不符返回空串（不 panic）。
func (u User) Str(key string) string {
	if u == nil {
		return ""
	}
	if v, ok := u[key].(string); ok {
		return v
	}
	return ""
}

// Bool 读取布尔字段；字段缺失或类型不符返回 false。
func (u User) Bool(key string) bool {
	if u == nil {
		return false
	}
	if v, ok := u[key].(bool); ok {
		return v
	}
	return false
}

// Int 读取整数字段，兼容 JSON 解析出的 float64/json.Number 以及 int/int64。
func (u User) Int(key string) int {
	if u == nil {
		return 0
	}
	switch v := u[key].(type) {
	case float64:
		return int(v)
	case int:
		return v
	case json.Number:
		n, _ := v.Int64()
		return int(n)
	case int64:
		return int(v)
	}
	return 0
}

// Float 读取浮点字段（时间戳等），兼容 json.Number/int/int64。
func (u User) Float(key string) float64 {
	if u == nil {
		return 0
	}
	switch v := u[key].(type) {
	case float64:
		return v
	case json.Number:
		f, _ := v.Float64()
		return f
	case int:
		return float64(v)
	case int64:
		return float64(v)
	}
	return 0
}

// Username 返回用户名。
func (u User) Username() string { return u.Str("username") }

// Role 返回角色（默认 user）。
// 只有 role == "admin" 才进入管理员分支；perms 只对管理员生效。
func (u User) Role() string {
	r := u.Str("role")
	if r == "" {
		return "user"
	}
	return r
}

// PasswordHash 返回 bcrypt 密码哈希。
func (u User) PasswordHash() string { return u.Str("password") }

// Copy 返回深拷贝，避免就地修改污染读取结果。
// LoadUsers 每次重新解析文件，但调用方可能改字段后再保存，拷贝是防止串写的唯一手段。
func (u User) Copy() User {
	if u == nil {
		return nil
	}
	b, _ := json.Marshal(u)
	var out User
	_ = json.Unmarshal(b, &out)
	return out
}

// Users 是用户表的顶层映射。
type Users map[string]User

var (
	usersMu    sync.Mutex // 串行化 users.json 写入，避免并发 SaveUsers 互相覆盖（读-改-写并不原子）
	secretOnce sync.Once  // JWT 密钥只读取/生成一次
	secretKey  string     // 进程内缓存的 HS256 密钥
	secretErr  error      // 密钥生成/落盘失败原因（失败时 getSecret 已写日志）
	defaultPw  sync.Map   // bcrypt hash -> 是否为默认密码，避免每请求做 bcrypt 比对
)

// LoadUsers 读取用户表；文件不存在返回 (nil, nil)。
// 返回 (nil, nil) 表示「首次启动、尚未播种」，调用方不要把它当错误。
func LoadUsers() (Users, error) {
	var users Users
	err := ReadJSON(DataPath("users.json"), &users)
	if IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return Users{}, err
	}
	if users == nil {
		users = Users{}
	}
	return users, nil
}

// SaveUsers 原子保存用户表。
// 内部持 usersMu，调用方不要在同一临界区里再调用（sync.Mutex 不可重入）。
func SaveUsers(users Users) error {
	usersMu.Lock()
	defer usersMu.Unlock()
	return WriteJSONFile(DataPath("users.json"), users)
}

// GetUser 读取单个用户（返回拷贝）。
// 用户不存在返回 (nil, nil)；返回的 User 可安全修改。
func GetUser(username string) (User, error) {
	users, err := LoadUsers()
	if err != nil {
		return nil, err
	}
	if users == nil {
		return nil, nil
	}
	u, ok := users[username]
	if !ok {
		return nil, nil
	}
	return u.Copy(), nil
}

// getSecret 读取/生成 JWT 签名密钥（data/secret.key，权限 0600）。
// 已有非空文件则复用（重启后令牌仍有效）；否则生成 48 字节随机数并落盘。
// 生成失败时 secretKey 为空，会导致签发/校验失败——这是有意的 fail-closed。
func getSecret() string {
	secretOnce.Do(func() {
		path := DataPath("secret.key")
		// 已有非空密钥：直接复用，保证重启后已签发令牌仍然有效。
		if b, err := os.ReadFile(path); err == nil {
			s := strings.TrimSpace(string(b))
			if s != "" {
				secretKey = s
				return
			}
		}
		// 首次运行（或文件为空）：生成 48 字节随机密钥并原子落盘，权限 0600。
		buf := make([]byte, 48)
		if _, err := rand.Read(buf); err != nil {
			secretErr = err
			// 熵源不可用时无法签发/校验令牌，必须留痕（否则表现为无提示的全员登录失败）。
			log.Printf("graw: 生成 JWT 签名密钥失败: %v", err)
			return
		}
		secretKey = base64.RawURLEncoding.EncodeToString(buf)
		if err := atomicWrite(path, []byte(secretKey), 0o600); err != nil {
			secretErr = err
			// 落盘失败时本次进程仍可用内存密钥，但重启后会换新密钥、旧令牌全部失效。
			log.Printf("graw: 落盘 JWT 签名密钥失败（重启后已登录会话将失效）: %v", err)
		}
	})
	return secretKey
}

// HashPassword 生成 bcrypt 哈希（与 Python bcrypt 兼容）。
// bcrypt 只取前 72 字节，超长密码先截断，避免 GenerateFromPassword 直接报错。
func HashPassword(password string) (string, error) {
	pw := password
	if len(pw) > 72 {
		pw = pw[:72]
	}
	b, err := bcrypt.GenerateFromPassword([]byte(pw), bcrypt.DefaultCost)
	if err != nil {
		return "", err
	}
	return string(b), nil
}

// VerifyPassword 校验密码；任何异常均返回 false。
// 空哈希直接 false（避免「账号无密码即放行」）；同样按 72 字节截断以匹配 HashPassword。
func VerifyPassword(password, hash string) bool {
	if hash == "" {
		return false
	}
	pw := password
	if len(pw) > 72 {
		pw = pw[:72]
	}
	return bcrypt.CompareHashAndPassword([]byte(hash), []byte(pw)) == nil
}

// IsDefaultPassword 判断密码哈希是否对应默认密码（带缓存，避免每请求 bcrypt）。
// 鉴权热路径会调用它，所以结果按 hash 缓存；改密后哈希变化自然命中新键。
func IsDefaultPassword(hash string) bool {
	if hash == "" {
		return false
	}
	if v, ok := defaultPw.Load(hash); ok {
		return v.(bool)
	}
	result := bcrypt.CompareHashAndPassword([]byte(hash), []byte(DefaultPassword)) == nil
	defaultPw.Store(hash, result)
	return result
}

// DefaultPassword 默认管理员密码（ShunX 保护会强制修改）。
// 该常量只用于识别「默认密码哈希」，不要在任何接口里回显。
const DefaultPassword = "admin123"

// SeedDefaultUsers 首次启动播种 admin 账号；admin 角色被误改时自动修复。
// 文件已存在且 admin 角色正常时什么都不做，因此可安全地在每次启动调用。
func SeedDefaultUsers() error {
	users, err := LoadUsers()
	if err != nil {
		return err
	}
	// 首次启动：创建 admin / admin123，并标记 must_change_password 强制改密。
	if users == nil {
		hash, err := HashPassword(DefaultPassword)
		if err != nil {
			return err
		}
		users = Users{
			"admin": User{
				"username":             "admin",
				"password":             hash,
				"role":                 "admin",
				"must_change_password": true,
				"created_at":           float64(time.Now().Unix()),
			},
		}
		return SaveUsers(users)
	}
	// 兜底修复：admin 账号被误降级为普通用户时强制恢复管理员，
	// 否则面板可能失去唯一的完整管理员入口。
	if admin, ok := users["admin"]; ok && admin.Role() != "admin" {
		admin["role"] = "admin"
		return SaveUsers(users)
	}
	return nil
}

// PublicUser 返回脱敏用户对象（不含密码哈希），字段与 Python 版一致。
// 登录响应与用户列表都用它；新增敏感字段时必须在此显式过滤，不要直接返回 User。
func PublicUser(u User) map[string]any {
	permsVal := any(nil)
	// perms 只在显式存在时返回：nil 表示「完整管理员」，[] 表示「受限管理员但无任何模块」。
	if p, present := u["perms"]; present && p != nil {
		switch v := p.(type) {
		case []any:
			out := make([]string, 0, len(v))
			for _, item := range v {
				if s, ok := item.(string); ok {
					out = append(out, s)
				}
			}
			permsVal = out
		case []string:
			permsVal = v
		default:
			permsVal = []string{}
		}
	}
	return map[string]any{
		"username":             u.Username(),
		"role":                 u.Role(),
		"must_change_password": u.Bool("must_change_password"),
		"created_at":           u.Float("created_at"),
		"token_version":        u.Int("token_version"),
		"otp_enabled":          u.Bool("otp_enabled"),
		"perms":                permsVal,
	}
}

// MODULES 模块权限 key（与前端入口、路由 PERM 保持一致）。
// 这是权限 key 的唯一来源：新增模块必须同时改这里、路由的 perm("xxx") 与前端 shortcut 的 perm 字段。
var MODULES = []string{
	"sites", "database", "docker", "files", "process", "disks", "cron",
	"firewall", "logs", "terminal", "backup", "notify", "appstore",
	"netstorage", "frp", "batch", "gitdeploy", "report", "tamper", "toolbox",
	"ftpusers", "svcmonitor", "healthcheck", "portforward", "netmap", "vpn",
	"loginlog",
}

// moduleSet 是 MODULES 的集合形式，用于 O(1) 过滤用户 perms 里的未知 key。
var moduleSet = func() map[string]bool {
	m := make(map[string]bool, len(MODULES))
	for _, k := range MODULES {
		m[k] = true
	}
	return m
}()

// UserPerms 返回模块白名单与「是否全量权限」。
// 语义：非管理员恒 (空集, false)；管理员且 perms 缺失/null 视为全量（full=true）；
// 其余按白名单过滤，未知 key 静默忽略（防止写坏 perms 造成越权）。
func UserPerms(u User) (map[string]bool, bool) {
	if u == nil || u.Role() != "admin" {
		return map[string]bool{}, false
	}
	raw, present := u["perms"]
	if !present || raw == nil {
		return nil, true
	}
	list, ok := raw.([]any)
	if !ok {
		// 用户表经不同路径解析后可能是 []string；同样按白名单过滤。
		if sl, ok2 := raw.([]string); ok2 {
			set := map[string]bool{}
			for _, s := range sl {
				if moduleSet[s] {
					set[s] = true
				}
			}
			return set, false
		}
		// 类型异常时按「无任何模块权限」保守处理（fail-closed）。
		return map[string]bool{}, false
	}
	set := map[string]bool{}
	for _, item := range list {
		if s, ok := item.(string); ok && moduleSet[s] {
			set[s] = true
		}
	}
	return set, false
}

// HasPerm 判断用户是否拥有任一模块权限（全量权限恒真）。
// 多模块参数是「或」关系；需要「与」时必须由调用方逐个判断。
func HasPerm(u User, modules ...string) bool {
	set, full := UserPerms(u)
	if full {
		return true
	}
	for _, m := range modules {
		if set[m] {
			return true
		}
	}
	return false
}

// jwtClaims 与 Python 版令牌负载保持一致：sub / tv / iat / exp / sid。
// tv 是用户 token_version 快照，sid 指向在线会话；任一不匹配即视为登录态失效。
type jwtClaims struct {
	TV  int    `json:"tv"`
	Sid string `json:"sid,omitempty"`
	jwt.RegisteredClaims
}

// TTL 令牌有效期（7 天，与 Python 版一致）。
const TokenTTL = 7 * 24 * time.Hour

// CreateToken 签发 JWT。
// username 写入 sub，tokenVersion 写入 tv，sid 绑定本次在线会话；
// 签名失败（密钥生成异常）时返回错误，由登录接口回 500。
func CreateToken(username string, tokenVersion int, sid string) (string, error) {
	now := time.Now()
	claims := jwtClaims{
		TV:  tokenVersion,
		Sid: sid,
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   username,
			IssuedAt:  jwt.NewNumericDate(now),
			ExpiresAt: jwt.NewNumericDate(now.Add(TokenTTL)),
		},
	}
	t := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	return t.SignedString([]byte(getSecret()))
}

// DecodeToken 校验并解析 JWT（HS256+exp），失败返回 nil。
// 显式限定 HS256：拒绝 alg=none 与算法混淆攻击（不能只依赖库的默认行为）。
func DecodeToken(token string) *jwtClaims {
	if token == "" {
		return nil
	}
	claims := &jwtClaims{}
	tk, err := jwt.ParseWithClaims(token, claims, func(t *jwt.Token) (any, error) {
		// 双重校验签名算法，任何非 HS256 一律拒绝。
		if t.Method.Alg() != "HS256" {
			return nil, fmt.Errorf("unexpected signing method")
		}
		return []byte(getSecret()), nil
	}, jwt.WithValidMethods([]string{"HS256"}))
	if err != nil || !tk.Valid {
		return nil
	}
	return claims
}

// VerifyTokenVersion 校验令牌版本号是否与用户当前版本一致。
// 改密 / 强制下线都会递增 token_version，从而让已签发的旧令牌立即失效。
func VerifyTokenVersion(c *jwtClaims) bool {
	u, err := GetUser(c.Subject)
	if err != nil || u == nil {
		return false
	}
	return c.TV == u.Int("token_version")
}

// BumpTokenVersion 递增 token_version 并吊销该用户全部会话。
// 用户不存在时静默返回 nil（幂等，便于登出/踢人重复调用）。
func BumpTokenVersion(username string) error {
	// 整个「读-改-写」必须与 SaveUsers 共用 usersMu：否则并发登出/踢人可能
	// 基于同一份旧快照递增，后写者覆盖前者，令牌版本只加了一次。
	usersMu.Lock()
	users, err := LoadUsers()
	if err != nil {
		usersMu.Unlock()
		return err
	}
	u, ok := users[username]
	if !ok {
		usersMu.Unlock()
		return nil
	}
	u["token_version"] = u.Int("token_version") + 1
	err = WriteJSONFile(DataPath("users.json"), users)
	usersMu.Unlock()
	if err != nil {
		return err
	}
	// 同时清掉在线会话记录，避免「在线列表」残留已失效设备。
	RevokeUserSessions(username)
	return nil
}

// BearerToken 从 Authorization 头提取 Bearer 令牌。
// 前缀大小写不敏感；格式不符返回空串，由调用方转 401。
func BearerToken(r *http.Request) string {
	auth := strings.TrimSpace(r.Header.Get("Authorization"))
	if len(auth) < 7 || !strings.EqualFold(auth[:7], "bearer ") {
		return ""
	}
	return strings.TrimSpace(auth[7:])
}

// Authenticate 校验 HTTP 请求的 Bearer 令牌，返回脱敏用户。
// failClosedOnDefault 为 true 时，默认密码账号直接 403（对应 require_non_default_password）。
// 失败一律返回 401/403 的 *HTTPError，调用方可直接 writeErr。
func Authenticate(r *http.Request, failClosedOnDefault bool) (User, *HTTPError) {
	token := BearerToken(r)
	if token == "" {
		return nil, NewHTTPError(401, "未认证")
	}
	claims := DecodeToken(token)
	if claims == nil {
		return nil, NewHTTPError(401, "令牌无效或已过期")
	}
	u, err := GetUser(claims.Subject)
	if err != nil || u == nil {
		// 用户已被删除时给出与「无效令牌」可区分的文案，但不泄露其他信息。
		return nil, NewHTTPError(401, "用户不存在")
	}
	// 版本号校验：改密、登出、管理员强制下线后旧令牌全部失效。
	if !VerifyTokenVersion(claims) {
		return nil, NewHTTPError(401, "登录状态已失效，请重新登录")
	}
	// 会话校验：被踢出的单台设备即使令牌未过期也要拒绝。
	if !SessionActive(claims.Sid) {
		return nil, NewHTTPError(401, "该设备已被强制下线")
	}
	// 刷新在线时间（内部 60 秒节流，不会每个请求都写盘）。
	EnsureOnlineSession(u.Username(), claims.Sid)
	// 默认密码只允许走 loginOnly 路由（改密/2FA/会话自救），其余一律 403。
	if failClosedOnDefault && IsDefaultPassword(u.PasswordHash()) {
		return nil, NewHTTPError(403, "必须修改默认密码后才能使用面板")
	}
	return u, nil
}

// AuthenticateWS 校验 WebSocket ?token= 参数（返回 nil 表示应关闭连接）。
// 浏览器 WebSocket 无法设置 Authorization 头，只能把令牌放在查询串里；
// 代价是可能落入访问日志，因此不要记录完整 URL。
// 与 Authenticate 的差异：不检查默认密码（由各 WS 处理器自行判断），失败时返回 nil。
func AuthenticateWS(r *http.Request) User {
	token := r.URL.Query().Get("token")
	if token == "" {
		return nil
	}
	claims := DecodeToken(token)
	if claims == nil {
		return nil
	}
	u, err := GetUser(claims.Subject)
	if err != nil || u == nil {
		return nil
	}
	if !VerifyTokenVersion(claims) || !SessionActive(claims.Sid) {
		return nil
	}
	EnsureOnlineSession(u.Username(), claims.Sid)
	return u
}

// WSStillValid 复检长连接会话是否仍有效（改密/踢出后应断开）。
// 长连接只在建立时鉴权一次，必须由处理器周期性调用它，否则吊销对已建连接无效。
func WSStillValid(token string) bool {
	claims := DecodeToken(token)
	if claims == nil {
		return false
	}
	if !VerifyTokenVersion(claims) {
		return false
	}
	return SessionActive(claims.Sid)
}

// ClientIP 获取客户端真实 IP（按可信代理层数解析 X-Forwarded-For）。
// ProxyDepth（TRUSTED_PROXY_DEPTH）表示前置可信代理的层数：取右起第 depth 个地址，
// 左侧内容由客户端伪造不可信。depth<=0 时直接使用 TCP 对端地址。
func ClientIP(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		host = r.RemoteAddr
	}
	depth := GetConfig().ProxyDepth
	if depth > 0 {
		xff := strings.TrimSpace(r.Header.Get("X-Forwarded-For"))
		if xff != "" {
			hops := []string{}
			for _, h := range strings.Split(xff, ",") {
				if s := strings.TrimSpace(h); s != "" {
					hops = append(hops, s)
				}
			}
			// 只信任靠近服务端一侧的 depth 个跳数，避免伪造头影响登录日志/限流。
			if len(hops) >= depth {
				return hops[len(hops)-depth]
			}
		}
	}
	if host == "" {
		return "unknown"
	}
	return host
}

// QueryInt 解析整数查询参数（非法或缺失时返回 def）。
func QueryInt(r *http.Request, name string, def int) int {
	v := r.URL.Query().Get(name)
	if v == "" {
		return def
	}
	n, err := strconv.Atoi(v)
	if err != nil {
		return def
	}
	return n
}

// QueryFloat 解析浮点查询参数。
func QueryFloat(r *http.Request, name string, def float64) float64 {
	v := r.URL.Query().Get(name)
	if v == "" {
		return def
	}
	f, err := strconv.ParseFloat(v, 64)
	if err != nil {
		return def
	}
	return f
}

// QueryBool 解析布尔查询参数。
// 仅 1/true/yes/on 视为真，其余（含非法值）返回 def，避免 "0" 被误判为 true。
func QueryBool(r *http.Request, name string, def bool) bool {
	v := strings.ToLower(strings.TrimSpace(r.URL.Query().Get(name)))
	if v == "" {
		return def
	}
	return v == "1" || v == "true" || v == "yes" || v == "on"
}
