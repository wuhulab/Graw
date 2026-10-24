package graw

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"fmt"
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
type HTTPError struct {
	Status int
	Detail string
}

func (e *HTTPError) Error() string { return e.Detail }

// NewHTTPError 构造业务错误。
func NewHTTPError(status int, detail string) *HTTPError {
	return &HTTPError{Status: status, Detail: detail}
}

// User 使用 map 保存原始字段，保证与 Python 版 users.json 的未知字段兼容。
type User map[string]any

func (u User) Str(key string) string {
	if u == nil {
		return ""
	}
	if v, ok := u[key].(string); ok {
		return v
	}
	return ""
}

func (u User) Bool(key string) bool {
	if u == nil {
		return false
	}
	if v, ok := u[key].(bool); ok {
		return v
	}
	return false
}

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
	usersMu    sync.Mutex
	secretOnce sync.Once
	secretKey  string
	secretErr  error
	defaultPw  sync.Map // hash -> bool
)

// LoadUsers 读取用户表；文件不存在返回 (nil, nil)。
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
func SaveUsers(users Users) error {
	usersMu.Lock()
	defer usersMu.Unlock()
	return WriteJSONFile(DataPath("users.json"), users)
}

// GetUser 读取单个用户（返回拷贝）。
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
func getSecret() string {
	secretOnce.Do(func() {
		path := DataPath("secret.key")
		if b, err := os.ReadFile(path); err == nil {
			s := strings.TrimSpace(string(b))
			if s != "" {
				secretKey = s
				return
			}
		}
		buf := make([]byte, 48)
		if _, err := rand.Read(buf); err != nil {
			secretErr = err
			return
		}
		secretKey = base64.RawURLEncoding.EncodeToString(buf)
		if err := atomicWrite(path, []byte(secretKey), 0o600); err != nil {
			secretErr = err
		}
	})
	return secretKey
}

// HashPassword 生成 bcrypt 哈希（与 Python bcrypt 兼容）。
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
const DefaultPassword = "admin123"

// SeedDefaultUsers 首次启动播种 admin 账号；admin 角色被误改时自动修复。
func SeedDefaultUsers() error {
	users, err := LoadUsers()
	if err != nil {
		return err
	}
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
	if admin, ok := users["admin"]; ok && admin.Role() != "admin" {
		admin["role"] = "admin"
		return SaveUsers(users)
	}
	return nil
}

// PublicUser 返回脱敏用户对象（不含密码哈希），字段与 Python 版一致。
func PublicUser(u User) map[string]any {
	permsVal := any(nil)
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
var MODULES = []string{
	"sites", "database", "docker", "files", "process", "disks", "cron",
	"firewall", "logs", "terminal", "backup", "notify", "appstore",
	"netstorage", "frp", "batch", "gitdeploy", "report", "tamper", "toolbox",
	"ftpusers", "svcmonitor", "healthcheck", "portforward", "netmap", "vpn",
	"loginlog",
}

var moduleSet = func() map[string]bool {
	m := make(map[string]bool, len(MODULES))
	for _, k := range MODULES {
		m[k] = true
	}
	return m
}()

// UserPerms 返回模块白名单与「是否全量权限」。
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
		if sl, ok2 := raw.([]string); ok2 {
			set := map[string]bool{}
			for _, s := range sl {
				if moduleSet[s] {
					set[s] = true
				}
			}
			return set, false
		}
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
type jwtClaims struct {
	TV  int    `json:"tv"`
	Sid string `json:"sid,omitempty"`
	jwt.RegisteredClaims
}

// TTL 令牌有效期（7 天，与 Python 版一致）。
const TokenTTL = 7 * 24 * time.Hour

// CreateToken 签发 JWT。
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
func DecodeToken(token string) *jwtClaims {
	if token == "" {
		return nil
	}
	claims := &jwtClaims{}
	tk, err := jwt.ParseWithClaims(token, claims, func(t *jwt.Token) (any, error) {
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
func VerifyTokenVersion(c *jwtClaims) bool {
	u, err := GetUser(c.Subject)
	if err != nil || u == nil {
		return false
	}
	return c.TV == u.Int("token_version")
}

// BumpTokenVersion 递增 token_version 并吊销该用户全部会话。
func BumpTokenVersion(username string) error {
	users, err := LoadUsers()
	if err != nil {
		return err
	}
	u, ok := users[username]
	if !ok {
		return nil
	}
	u["token_version"] = u.Int("token_version") + 1
	if err := SaveUsers(users); err != nil {
		return err
	}
	RevokeUserSessions(username)
	return nil
}

// BearerToken 从 Authorization 头提取 Bearer 令牌。
func BearerToken(r *http.Request) string {
	auth := strings.TrimSpace(r.Header.Get("Authorization"))
	if len(auth) < 7 || !strings.EqualFold(auth[:7], "bearer ") {
		return ""
	}
	return strings.TrimSpace(auth[7:])
}

// Authenticate 校验 HTTP 请求的 Bearer 令牌，返回脱敏用户。
// failClosedOnDefault 为 true 时，默认密码账号直接 403（对应 require_non_default_password）。
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
		return nil, NewHTTPError(401, "用户不存在")
	}
	if !VerifyTokenVersion(claims) {
		return nil, NewHTTPError(401, "登录状态已失效，请重新登录")
	}
	if !SessionActive(claims.Sid) {
		return nil, NewHTTPError(401, "该设备已被强制下线")
	}
	EnsureOnlineSession(u.Username(), claims.Sid)
	if failClosedOnDefault && IsDefaultPassword(u.PasswordHash()) {
		return nil, NewHTTPError(403, "必须修改默认密码后才能使用面板")
	}
	return u, nil
}

// AuthenticateWS 校验 WebSocket ?token= 参数（返回 nil 表示应关闭连接）。
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
func QueryBool(r *http.Request, name string, def bool) bool {
	v := strings.ToLower(strings.TrimSpace(r.URL.Query().Get(name)))
	if v == "" {
		return def
	}
	return v == "1" || v == "true" || v == "yes" || v == "on"
}
