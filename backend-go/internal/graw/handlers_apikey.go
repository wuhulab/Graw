package graw

import (
	"crypto/rand"
	"encoding/base64"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"

	"golang.org/x/crypto/bcrypt"
)

// API Key（P2）：给第三方/脚本调用面板 API 的长期凭据。
//
// 设计取舍：
//   - 只存 bcrypt 哈希与 11 字符前缀，明文只在创建时返回一次；
//   - scope 复用 MODULES，另加 "*"（全部模块）与 "admin"（面板自身管理接口）；
//   - 可限制来源 IP/CIDR 与到期时间；owner 账号被删除或改密不影响 key 本身，
//     但 key 会因找不到 owner 而失效；
//   - API Key 请求在 route() 里额外做 scope 判定，权限永远不超过 owner 本人。

const apiKeyPrefix = "gk_"
const apiKeyLookupLen = 11

type apiKeyRecord struct {
	ID         string   `json:"id"`
	Name       string   `json:"name"`
	Prefix     string   `json:"prefix"`
	Hash       string   `json:"hash"`
	Owner      string   `json:"owner"`
	Scopes     []string `json:"scopes"`
	AllowedIPs []string `json:"allowed_ips"`
	ExpiresAt  string   `json:"expires_at"`
	Enabled    bool     `json:"enabled"`
	CreatedAt  string   `json:"created_at"`
	LastUsedAt string   `json:"last_used_at"`
}

var apiKeyTouchMu sync.Mutex
var apiKeyLastTouch = map[string]time.Time{}

func apiKeyStorePath() string { return DataPath("api_keys.json") }

func loadAPIKeys() []apiKeyRecord {
	items := []apiKeyRecord{}
	_ = ReadJSON(apiKeyStorePath(), &items)
	return items
}

func saveAPIKeys(items []apiKeyRecord) error {
	CaptureSnapshot("api-keys", "settings", apiKeyStorePath())
	return WriteJSONFile(apiKeyStorePath(), items)
}

// saveAPIKeysRaw 用于「最近使用时间」这类高频小更新：不生成配置快照，
// 否则一个活跃 Key 每分钟都会往 config_snapshots 里塞一份副本。
func saveAPIKeysRaw(items []apiKeyRecord) error {
	return WriteJSONFile(apiKeyStorePath(), items)
}

func apiKeyPublic(item apiKeyRecord) map[string]any {
	return map[string]any{
		"id": item.ID, "name": item.Name, "prefix": item.Prefix, "owner": item.Owner,
		"scopes": item.Scopes, "allowed_ips": item.AllowedIPs, "expires_at": item.ExpiresAt,
		"enabled": item.Enabled, "created_at": item.CreatedAt, "last_used_at": item.LastUsedAt,
	}
}

func randomAPIKey() (plain, prefix string) {
	buf := make([]byte, 32)
	_, _ = rand.Read(buf)
	plain = apiKeyPrefix + base64.RawURLEncoding.EncodeToString(buf)
	if len(plain) < apiKeyLookupLen {
		return plain, plain
	}
	return plain, plain[:apiKeyLookupLen]
}

func normalizeScopes(scopes []string) []string {
	out := []string{}
	seen := map[string]bool{}
	for _, s := range scopes {
		s = strings.TrimSpace(s)
		if s == "" || seen[s] {
			continue
		}
		if s == "*" {
			return []string{"*"}
		}
		if s == "admin" || moduleSet[s] {
			seen[s] = true
			out = append(out, s)
		}
	}
	sortStrings(out)
	return out
}

func apiKeyExpired(item apiKeyRecord) bool {
	if strings.TrimSpace(item.ExpiresAt) == "" {
		return false
	}
	for _, layout := range []string{"2006-01-02 15:04:05", "2006-01-02"} {
		if t, err := time.ParseInLocation(layout, item.ExpiresAt, time.Local); err == nil {
			return time.Now().After(t)
		}
	}
	// 到期时间无法解析时按「已过期」处理，避免脏数据让 Key 永久有效。
	return true
}

// authenticateAPIKey 校验 gk_ 开头的长期凭据，并把 scope 塞进 User 副本。
func authenticateAPIKey(r *http.Request, token string, failClosedOnDefault bool) (User, *HTTPError) {
	if !strings.HasPrefix(token, apiKeyPrefix) {
		return nil, NewHTTPError(401, "令牌无效或已过期")
	}
	prefix := token
	if len(prefix) > apiKeyLookupLen {
		prefix = prefix[:apiKeyLookupLen]
	}
	var matched *apiKeyRecord
	for _, item := range loadAPIKeys() {
		if item.Prefix != prefix {
			continue
		}
		if bcrypt.CompareHashAndPassword([]byte(item.Hash), []byte(token)) == nil {
			copy := item
			matched = &copy
			break
		}
	}
	if matched == nil {
		return nil, NewHTTPError(401, "API Key 无效")
	}
	if !matched.Enabled {
		return nil, NewHTTPError(403, "API Key 已禁用")
	}
	if apiKeyExpired(*matched) {
		return nil, NewHTTPError(403, "API Key 已过期")
	}
	if len(matched.AllowedIPs) > 0 && !ipAllowed(ClientIP(r), matched.AllowedIPs) {
		return nil, NewHTTPError(403, "当前 IP 不在 API Key 授权列表中")
	}
	owner, err := GetUser(matched.Owner)
	if err != nil || owner == nil {
		return nil, NewHTTPError(401, "API Key 所属用户不存在")
	}
	if failClosedOnDefault && IsDefaultPassword(owner.PasswordHash()) {
		return nil, NewHTTPError(403, "必须修改默认密码后才能使用面板 API")
	}
	// 浅拷贝，避免把内部字段写回用户缓存（GetUser 每次返回新 map，这里再保险一次）。
	u := User{}
	for k, v := range owner {
		u[k] = v
	}
	u["_api_scopes"] = matched.Scopes
	u["_api_key_id"] = matched.ID
	touchAPIKey(matched.ID)
	return u, nil
}

func touchAPIKey(id string) {
	apiKeyTouchMu.Lock()
	defer apiKeyTouchMu.Unlock()
	if last, ok := apiKeyLastTouch[id]; ok && time.Since(last) < time.Minute {
		return
	}
	apiKeyLastTouch[id] = time.Now()
	items := loadAPIKeys()
	now := time.Now().Format("2006-01-02 15:04:05")
	changed := false
	for i := range items {
		if items[i].ID == id {
			items[i].LastUsedAt = now
			changed = true
			break
		}
	}
	if changed {
		_ = saveAPIKeysRaw(items)
	}
}

func apiKeyScopeAllows(u User, need string) bool {
	raw, ok := u["_api_scopes"]
	if !ok {
		return true // 普通 JWT 登录，不额外限制
	}
	scopes, _ := raw.([]string)
	for _, s := range scopes {
		if s == "*" || s == need {
			return true
		}
	}
	return false
}

func apiKeyScopeAllowsAny(u User, needs []string) bool {
	if _, ok := u["_api_scopes"]; !ok {
		return true
	}
	for _, need := range needs {
		if apiKeyScopeAllows(u, need) {
			return true
		}
	}
	return false
}

func registerAPIKeyRoutes(a *App) {
	a.Get("/api/apikeys", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		items := loadAPIKeys()
		out := []map[string]any{}
		for _, item := range items {
			out = append(out, apiKeyPublic(item))
		}
		return WriteJSON(w, 200, map[string]any{"keys": out})
	})
	a.Get("/api/apikeys/modules", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"modules": MODULES})
	})
	a.Post("/api/apikeys", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Name        string   `json:"name"`
			Owner       string   `json:"owner"`
			Scopes      []string `json:"scopes"`
			AllowedIPs  []string `json:"allowed_ips"`
			ExpiresDays int      `json:"expires_days"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		name := strings.TrimSpace(req.Name)
		if name == "" || len(name) > 64 {
			return NewHTTPError(400, "Key 名称必填且不超过 64 字")
		}
		ownerName := strings.TrimSpace(req.Owner)
		if ownerName == "" {
			ownerName = CurrentUser(r).Username()
		}
		owner, err := GetUser(ownerName)
		if err != nil || owner == nil {
			return NewHTTPError(400, "所属用户不存在")
		}
		scopes := normalizeScopes(req.Scopes)
		if len(scopes) == 0 {
			return NewHTTPError(400, "请至少选择一个 scope")
		}
		ips := []string{}
		for _, item := range req.AllowedIPs {
			item = strings.TrimSpace(item)
			if item == "" {
				continue
			}
			if strings.Contains(item, "/") {
				if _, _, perr := net.ParseCIDR(item); perr != nil {
					return NewHTTPError(400, "IP/CIDR 格式非法: "+item)
				}
			} else if net.ParseIP(item) == nil {
				return NewHTTPError(400, "IP 格式非法: "+item)
			}
			ips = append(ips, item)
			if len(ips) >= 50 {
				break
			}
		}
		expires := ""
		if req.ExpiresDays > 0 {
			if req.ExpiresDays > 3650 {
				return NewHTTPError(400, "有效期最长 3650 天")
			}
			expires = time.Now().AddDate(0, 0, req.ExpiresDays).Format("2006-01-02 15:04:05")
		}
		plain, prefix := randomAPIKey()
		hash, herr := bcrypt.GenerateFromPassword([]byte(plain), bcrypt.DefaultCost)
		if herr != nil {
			return NewHTTPError(500, "生成 Key 失败")
		}
		item := apiKeyRecord{
			ID: "key_" + randomHex(8), Name: name, Prefix: prefix, Hash: string(hash),
			Owner: ownerName, Scopes: scopes, AllowedIPs: ips, ExpiresAt: expires, Enabled: true,
			CreatedAt: time.Now().Format("2006-01-02 15:04:05"),
		}
		items := loadAPIKeys()
		items = append(items, item)
		if err := saveAPIKeys(items); err != nil {
			return err
		}
		res := apiKeyPublic(item)
		res["key"] = plain
		return WriteJSON(w, 200, res)
	})
	a.Post("/api/apikeys/{key_id}/delete", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		id := strings.TrimSpace(PathParam(r, "key_id"))
		items := loadAPIKeys()
		out := items[:0]
		for _, item := range items {
			if item.ID != id {
				out = append(out, item)
			}
		}
		if err := saveAPIKeys(out); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/apikeys/{key_id}/toggle", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		id := strings.TrimSpace(PathParam(r, "key_id"))
		items := loadAPIKeys()
		found := false
		for i := range items {
			if items[i].ID == id {
				items[i].Enabled = !items[i].Enabled
				found = true
				break
			}
		}
		if !found {
			return NewHTTPError(404, "API Key 不存在")
		}
		if err := saveAPIKeys(items); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
}
