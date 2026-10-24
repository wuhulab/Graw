package graw

import (
	"bufio"
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"golang.org/x/crypto/ssh"
)

// 多节点 / 子节点 Agent：节点存储、SSH 连接池、成对密钥鉴权、Agent 隧道代理。

const (
	localNodeID      = "local"
	defaultAgentPort = 8000
	agentTSWindow    = 300
)

var (
	nodeStoreMu sync.Mutex
	sshHostRe   = regexp.MustCompile(`^[A-Za-z0-9]([A-Za-z0-9.:\-]*[A-Za-z0-9])?$`)
	sshUserRe   = regexp.MustCompile(`^[A-Za-z0-9_]([A-Za-z0-9_.\-]*[A-Za-z0-9_\-])?$`)
)

type nodeStore struct {
	Version int                       `json:"version"`
	Current string                    `json:"current"`
	Nodes   map[string]map[string]any `json:"nodes"`
}

func defaultNodeStore() nodeStore {
	return nodeStore{
		Version: 1, Current: localNodeID,
		Nodes: map[string]map[string]any{
			localNodeID: {"id": localNodeID, "name": "本机", "type": "local"},
		},
	}
}

var (
	nodeCacheMu    sync.RWMutex
	nodeCache      nodeStore
	nodeCacheAt    time.Time
	nodeCacheValid bool
)

func loadNodeStoreFresh() nodeStore {
	var store nodeStore
	if err := ReadJSON(DataPath("nodes.json"), &store); err != nil || store.Nodes == nil {
		return defaultNodeStore()
	}
	if _, ok := store.Nodes[localNodeID]; !ok {
		store.Nodes[localNodeID] = map[string]any{"id": localNodeID, "name": "本机", "type": "local"}
	}
	if store.Current == "" {
		store.Current = localNodeID
	}
	return store
}

// loadNodeStore 读取节点表（2 秒缓存，避免文件/指标循环里反复解析 JSON）。
func loadNodeStore() nodeStore {
	nodeCacheMu.RLock()
	if nodeCacheValid && time.Since(nodeCacheAt) < 2*time.Second {
		store := nodeCache
		nodeCacheMu.RUnlock()
		return store
	}
	nodeCacheMu.RUnlock()
	store := loadNodeStoreFresh()
	nodeCacheMu.Lock()
	nodeCache = store
	nodeCacheAt = time.Now()
	nodeCacheValid = true
	nodeCacheMu.Unlock()
	return store
}

func saveNodeStore(store nodeStore) error {
	nodeStoreMu.Lock()
	defer nodeStoreMu.Unlock()
	if err := WriteJSONFile(DataPath("nodes.json"), store); err != nil {
		return err
	}
	nodeCacheMu.Lock()
	nodeCache = store
	nodeCacheAt = time.Now()
	nodeCacheValid = true
	nodeCacheMu.Unlock()
	return nil
}

func publicNode(id string, node map[string]any) map[string]any {
	pub := map[string]any{
		"id": id, "name": defaultString(str(node["name"]), id), "type": str(node["type"]),
		"group": str(node["group"]), "tags": toStringSlice(node["tags"]),
	}
	if str(node["type"]) == "ssh" {
		pub["host"] = str(node["host"])
		pub["port"] = func() int {
			if p := int(int64Of(node["port"])); p > 0 {
				return p
			}
			return 22
		}()
		pub["user"] = str(node["user"])
		pub["auth"] = defaultString(str(node["auth"]), "password")
		pub["has_password"] = str(node["password"]) != ""
		pub["key_path"] = str(node["key_path"])
		pub["agent_enabled"] = str(node["agent_key"]) != "" && str(node["agent_secret"]) != ""
		pub["agent_port"] = func() int {
			if p := int(int64Of(node["agent_port"])); p > 0 {
				return p
			}
			return defaultAgentPort
		}()
	}
	return pub
}

// CurrentNodeID 返回全局当前节点 ID（节点选择持久化值）。
func CurrentNodeID() string {
	return loadNodeStore().Current
}

// RequestNodeID 返回请求级节点（X-Graw-Node 优先，否则宿主操作解析后的当前节点）。
func RequestNodeID(r *http.Request) string {
	if v := strings.TrimSpace(r.Header.Get("X-Graw-Node")); v != "" {
		return v
	}
	return resolvedNodeID()
}

// IsRemoteCurrent 当前（或请求级）节点是否为远程 SSH 节点。
func IsRemoteCurrent() bool {
	n := opsNode()
	return n != nil && str(n["type"]) == "ssh"
}

func currentNode() map[string]any {
	return opsNode()
}

func agentReady(node map[string]any) bool {
	return node != nil && str(node["type"]) == "ssh" &&
		str(node["agent_key"]) != "" && str(node["agent_secret"]) != ""
}

// --- 子节点 Agent 收取模式配置 ---

type agentConfig struct {
	Enabled        bool   `json:"enabled"`
	Key            string `json:"key"`
	Secret         string `json:"secret"`
	Role           string `json:"role"`
	SecretRevealed bool   `json:"secret_revealed"`
}

func loadAgentConfig() agentConfig {
	cfg := agentConfig{Role: "admin", SecretRevealed: true}
	_ = ReadJSON(DataPath("agent.json"), &cfg)
	if cfg.Key == "" || cfg.Secret == "" {
		envKey := strings.TrimSpace(os.Getenv("GRAW_AGENT_KEY"))
		envSecret := strings.TrimSpace(os.Getenv("GRAW_AGENT_SECRET"))
		if envKey != "" && envSecret != "" {
			return agentConfig{Enabled: true, Key: envKey, Secret: envSecret, Role: "admin", SecretRevealed: true}
		}
		return agentConfig{Role: "admin", SecretRevealed: true}
	}
	return cfg
}

func agentEnabled() bool {
	cfg := loadAgentConfig()
	return cfg.Enabled && cfg.Key != "" && cfg.Secret != ""
}

func agentPublicStatus() map[string]any {
	cfg := loadAgentConfig()
	enabled := cfg.Enabled && cfg.Key != "" && cfg.Secret != ""
	return map[string]any{
		"enabled": enabled, "role": "admin", "key": cfg.Key,
		"has_secret": cfg.Secret != "",
		"can_reveal": enabled && cfg.Secret != "" && !cfg.SecretRevealed,
		"ts_window":  agentTSWindow,
	}
}

var agentNonceMu sync.Mutex
var agentUsedNonces = map[string]int64{}

func agentSignature(secret, key string, ts int64, nonce string) string {
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(fmt.Sprintf("%s|%d|%s", key, ts, nonce)))
	return hex.EncodeToString(mac.Sum(nil))
}

func registerAgentRoutes(a *App) {
	a.Get("/api/agent/issue", customAuth(), func(w http.ResponseWriter, r *http.Request) error {
		if !agentEnabled() {
			return NewHTTPError(404, "Agent API 未启用")
		}
		q := r.URL.Query()
		tsStr, nonce, sig := q.Get("ts"), q.Get("nonce"), q.Get("sig")
		providedKey := r.Header.Get("X-Graw-Agent-Key")
		if tsStr == "" || nonce == "" || sig == "" {
			return NewHTTPError(400, "缺少 ts/nonce/sig")
		}
		ts, err := strconvParseInt(tsStr)
		if err != nil {
			return NewHTTPError(400, "ts 非法")
		}
		cfg := loadAgentConfig()
		if !hmac.Equal([]byte(providedKey), []byte(cfg.Key)) {
			return NewHTTPError(401, "Agent 凭证校验失败")
		}
		now := time.Now().Unix()
		if abs64(now-ts) > agentTSWindow || len(nonce) > 128 {
			return NewHTTPError(401, "Agent 凭证校验失败")
		}
		expect := agentSignature(cfg.Secret, providedKey, ts, nonce)
		if !hmac.Equal([]byte(expect), []byte(sig)) {
			return NewHTTPError(401, "Agent 凭证校验失败")
		}
		agentNonceMu.Lock()
		key := providedKey + "|" + nonce
		if _, seen := agentUsedNonces[key]; seen {
			agentNonceMu.Unlock()
			return NewHTTPError(401, "Agent 凭证校验失败")
		}
		agentUsedNonces[key] = now
		if len(agentUsedNonces) > 10000 {
			for k, t := range agentUsedNonces {
				if now-t > agentTSWindow {
					delete(agentUsedNonces, k)
				}
			}
		}
		agentNonceMu.Unlock()
		if err := ensureAgentUser(); err != nil {
			return err
		}
		user, _ := GetUser("agent")
		tv := 0
		if user != nil {
			tv = user.Int("token_version")
		}
		token, err := CreateToken("agent", tv, "")
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{
			"token": token, "role": "admin", "expires_in": int(TokenTTL.Seconds()), "username": "agent",
		})
	})
	a.Get("/api/agent/cfg", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, agentPublicStatus())
	})
	a.Put("/api/agent/cfg", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Enabled bool   `json:"enabled"`
			Key     string `json:"key"`
			Secret  string `json:"secret"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		cur := loadAgentConfig()
		key := strings.TrimSpace(req.Key)
		if key == "" {
			key = cur.Key
		}
		secret := strings.TrimSpace(req.Secret)
		secretChanged := false
		if secret != "" {
			secretChanged = true
		} else if req.Enabled {
			secret = cur.Secret
		}
		if req.Enabled && (key == "" || secret == "") {
			return NewHTTPError(400, "未配置访问 key/校验 secret，无法启用")
		}
		cfg := agentConfig{Enabled: req.Enabled, Key: key, Secret: secret, Role: "admin"}
		if req.Enabled {
			cfg.SecretRevealed = !secretChanged && cur.SecretRevealed
		} else {
			cfg.Secret = ""
			cfg.SecretRevealed = true
		}
		if err := WriteJSONFile(DataPath("agent.json"), cfg); err != nil {
			return err
		}
		return WriteJSON(w, 200, agentPublicStatus())
	})
	a.Post("/api/agent/reveal-secret", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		cfg := loadAgentConfig()
		if !(cfg.Enabled && cfg.Secret != "" && !cfg.SecretRevealed) {
			return WriteJSON(w, 200, map[string]any{"secret": "", "revealed": true})
		}
		cfg.SecretRevealed = true
		_ = WriteJSONFile(DataPath("agent.json"), cfg)
		return WriteJSON(w, 200, map[string]any{"secret": cfg.Secret, "revealed": false, "role": adminRole})
	})
}

const adminRole = "admin"

func ensureAgentUser() error {
	users, err := LoadUsers()
	if err != nil {
		return err
	}
	if users == nil {
		users = Users{}
	}
	if u, ok := users["agent"]; ok && u.Role() == "admin" {
		return nil
	}
	pw := randomHex(64)
	hash, err := HashPassword(pw)
	if err != nil {
		return err
	}
	users["agent"] = User{
		"username": "agent", "password": hash, "role": "admin",
		"must_change_password": false, "created_at": float64(time.Now().Unix()),
	}
	return SaveUsers(users)
}

func strconvParseInt(s string) (int64, error) {
	var n int64
	if _, err := fmt.Sscanf(strings.TrimSpace(s), "%d", &n); err != nil {
		return 0, err
	}
	return n, nil
}

func abs64(n int64) int64 {
	if n < 0 {
		return -n
	}
	return n
}

// --- SSH 客户端与连接池 ---

var (
	sshPoolMu sync.Mutex
	sshPool   = map[string]*ssh.Client{}
)

func nodeKey(node map[string]any) string {
	return fmt.Sprintf("%s:%d:%s", str(node["host"]), int(int64Of(node["port"])), str(node["user"]))
}

func sshConnect(node map[string]any, timeout time.Duration) (*ssh.Client, error) {
	host := str(node["host"])
	port := int(int64Of(node["port"]))
	if port == 0 {
		port = 22
	}
	if !sshHostRe.MatchString(host) || strings.HasPrefix(host, "-") {
		return nil, fmt.Errorf("节点主机地址非法")
	}
	user := str(node["user"])
	if !sshUserRe.MatchString(user) {
		return nil, fmt.Errorf("节点用户名非法")
	}
	auths := []ssh.AuthMethod{}
	if str(node["auth"]) == "key" && str(node["key_path"]) != "" {
		keyPath := str(node["key_path"])
		candidates := []string{keyPath, HostPath(keyPath), DataPath(keyPath)}
		var keyData []byte
		var readErr error
		for _, cand := range candidates {
			if cand == "" {
				continue
			}
			if keyData, readErr = os.ReadFile(cand); readErr == nil {
				break
			}
		}
		if readErr != nil {
			return nil, fmt.Errorf("读取 SSH 私钥失败: %v", readErr)
		}
		signer, err := ssh.ParsePrivateKey(keyData)
		if err != nil {
			return nil, fmt.Errorf("解析 SSH 私钥失败: %v", err)
		}
		auths = append(auths, ssh.PublicKeys(signer))
	} else {
		auths = append(auths, ssh.Password(str(node["password"])))
	}
	addr := net.JoinHostPort(host, itoa(port))
	raw, err := net.DialTimeout("tcp", addr, timeout)
	if err != nil {
		return nil, err
	}
	_ = raw.SetDeadline(time.Now().Add(timeout))
	cfg := &ssh.ClientConfig{
		User: user, Auth: auths, Timeout: timeout,
		HostKeyCallback: sshHostKeyCallback(addr),
	}
	conn, chans, reqs, err := ssh.NewClientConn(raw, addr, cfg)
	if err != nil {
		raw.Close()
		return nil, err
	}
	return ssh.NewClient(conn, chans, reqs), nil
}

var knownHostsMu sync.Mutex

// sshHostKeyCallback 实现 TOFU：首次记录指纹，后续必须一致。
func sshHostKeyCallback(addr string) ssh.HostKeyCallback {
	return func(hostname string, remote net.Addr, key ssh.PublicKey) error {
		fp := ssh.FingerprintSHA256(key)
		path := DataPath("ssh_known_hosts.json")
		knownHostsMu.Lock()
		defer knownHostsMu.Unlock()
		known := map[string]string{}
		_ = ReadJSON(path, &known)
		if prev, ok := known[addr]; ok {
			if prev != fp {
				return fmt.Errorf("SSH 主机密钥已变更（%s），可能存在中间人攻击；如确认安全请删除 data/ssh_known_hosts.json 中对应记录", addr)
			}
			return nil
		}
		known[addr] = fp
		_ = WriteJSONFile(path, known)
		return nil
	}
}

func pooledSSH(node map[string]any, timeout time.Duration) (*ssh.Client, error) {
	key := nodeKey(node)
	sshPoolMu.Lock()
	if c, ok := sshPool[key]; ok {
		_, _, err := c.SendRequest("keepalive@graw", true, nil)
		if err == nil {
			sshPoolMu.Unlock()
			return c, nil
		}
		_ = c.Close()
		delete(sshPool, key)
	}
	sshPoolMu.Unlock()
	c, err := sshConnect(node, timeout)
	if err != nil {
		return nil, err
	}
	sshPoolMu.Lock()
	if old, ok := sshPool[key]; ok {
		_ = old.Close()
	}
	sshPool[key] = c
	sshPoolMu.Unlock()
	return c, nil
}

func dropSSHPool(node map[string]any) {
	key := nodeKey(node)
	sshPoolMu.Lock()
	if c, ok := sshPool[key]; ok {
		_ = c.Close()
		delete(sshPool, key)
	}
	sshPoolMu.Unlock()
}

// forgetSSHHostKey 删除节点对应的 TOFU 主机密钥记录（节点重装/换钥后重新信任）。
func forgetSSHHostKey(node map[string]any) {
	port := int(int64Of(node["port"]))
	if port == 0 {
		port = 22
	}
	addr := net.JoinHostPort(str(node["host"]), itoa(port))
	knownHostsMu.Lock()
	defer knownHostsMu.Unlock()
	known := map[string]string{}
	_ = ReadJSON(DataPath("ssh_known_hosts.json"), &known)
	delete(known, addr)
	_ = WriteJSONFile(DataPath("ssh_known_hosts.json"), known)
}

// --- Agent 隧道 HTTP ---

var (
	agentTokenMu sync.Mutex
	agentTokens  = map[string]struct {
		Token string
		Exp   time.Time
	}{}
)

func agentToken(node map[string]any, client *ssh.Client) (string, error) {
	key := nodeKey(node)
	agentTokenMu.Lock()
	if cached, ok := agentTokens[key]; ok && time.Until(cached.Exp) > 5*time.Minute {
		agentTokenMu.Unlock()
		return cached.Token, nil
	}
	agentTokenMu.Unlock()
	port := int(int64Of(node["agent_port"]))
	if port == 0 {
		port = defaultAgentPort
	}
	now := time.Now().Unix()
	nonce := randomHex(8)
	sig := agentSignature(str(node["agent_secret"]), str(node["agent_key"]), now, nonce)
	path := fmt.Sprintf("/api/agent/issue?ts=%d&nonce=%s&sig=%s", now, nonce, sig)
	status, _, body, err := httpOverSSH(client, port, http.MethodGet, path,
		map[string]string{"X-Graw-Agent-Key": str(node["agent_key"])}, nil)
	if err != nil {
		return "", err
	}
	var payload struct {
		Token     string `json:"token"`
		ExpiresIn int64  `json:"expires_in"`
	}
	if status != 200 || json.Unmarshal(body, &payload) != nil || payload.Token == "" {
		return "", fmt.Errorf("Agent 换取 token 失败: %d %s", status, string(body))
	}
	if payload.ExpiresIn <= 0 {
		payload.ExpiresIn = 7 * 24 * 3600
	}
	agentTokenMu.Lock()
	agentTokens[key] = struct {
		Token string
		Exp   time.Time
	}{Token: payload.Token, Exp: time.Now().Add(time.Duration(payload.ExpiresIn) * time.Second)}
	agentTokenMu.Unlock()
	return payload.Token, nil
}

func httpOverSSH(client *ssh.Client, port int, method, path string, headers map[string]string, body []byte) (int, http.Header, []byte, error) {
	conn, err := client.Dial("tcp", net.JoinHostPort("127.0.0.1", itoa(port)))
	if err != nil {
		return 0, nil, nil, err
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(60 * time.Second))
	req := fmt.Sprintf("%s %s HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n", method, path)
	if body != nil {
		req += fmt.Sprintf("Content-Length: %d\r\n", len(body))
	}
	for k, v := range headers {
		req += k + ": " + v + "\r\n"
	}
	req += "\r\n"
	if _, err := conn.Write(append([]byte(req), body...)); err != nil {
		return 0, nil, nil, err
	}
	httpReq := &http.Request{Method: method}
	resp, err := http.ReadResponse(bufio.NewReader(conn), httpReq)
	if err != nil {
		return 0, nil, nil, err
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, resp.Header, respBody, nil
}

func agentProxyRequest(node map[string]any, r *http.Request, rawBody []byte) (*http.Response, error) {
	client, err := pooledSSH(node, 15*time.Second)
	if err != nil {
		return nil, err
	}
	token, err := agentToken(node, client)
	if err != nil {
		return nil, err
	}
	port := int(int64Of(node["agent_port"]))
	if port == 0 {
		port = defaultAgentPort
	}
	path := r.URL.RequestURI()
	headers := map[string]string{"Authorization": "Bearer " + token}
	if ct := r.Header.Get("Content-Type"); ct != "" && len(rawBody) > 0 {
		headers["Content-Type"] = ct
	}
	if accept := r.Header.Get("Accept"); accept != "" {
		headers["Accept"] = accept
	}
	status, hdr, respBody, err := httpOverSSH(client, port, r.Method, path, headers, rawBody)
	if err != nil {
		return nil, err
	}
	return &http.Response{
		StatusCode: status, Header: hdr,
		Body: io.NopCloser(bytes.NewReader(respBody)),
	}, nil
}

// --- 代理前置鉴权 ---

var proxyUserSafe = map[string][]string{
	"/api/system": {"GET"},
	"/api/notes":  {"GET"},
	"/api/tamper": {"GET"},
}

var agentProxyExclude = []string{
	"/api/auth", "/api/nodes", "/api/terminal", "/api/agent", "/api/ui",
	"/api/shunx", "/api/health", "/api/batch", "/api/gitdeploy", "/api/portforward", "/api/vpn",
}

var localOnlyPrefixes = []string{
	"/api/sites", "/api/databases", "/api/cron", "/api/ssl", "/api/protection",
	"/api/runtime", "/api/tasks", "/api/appstore", "/api/backup", "/api/netstorage",
	"/api/notify", "/api/uptime", "/api/webstats", "/api/rewrite", "/api/sitesopts",
	"/api/waf", "/api/tamper", "/api/phpversions", "/api/ftpusers", "/api/sshkeys",
	"/api/certcheck", "/api/panelbackup", "/api/update", "/api/webmode", "/api/loginlog",
	"/api/netmap", "/api/plugins", "/api/op",
}

func shouldAgentProxy(path string) bool {
	p := strings.TrimRight(path, "/")
	if !strings.HasPrefix(p, "/api/") {
		return false
	}
	for _, ex := range agentProxyExclude {
		if p == ex || strings.HasPrefix(p, ex+"/") {
			return false
		}
	}
	return true
}

func isLocalOnlyPath(path string) bool {
	for _, p := range localOnlyPrefixes {
		if path == p || strings.HasPrefix(path, p+"/") {
			return true
		}
	}
	return false
}

// RejectIfLocalRemote 当当前节点为远端且路径为 local 类时返回 true。
func RejectIfLocalRemote(path string) bool {
	return IsRemoteCurrent() && isLocalOnlyPath(path)
}

func proxyReadonlyAllow(path, method string) bool {
	for prefix, methods := range proxyUserSafe {
		if path == prefix || strings.HasPrefix(path, prefix+"/") {
			for _, m := range methods {
				if method == m {
					return true
				}
			}
			return false
		}
	}
	return false
}

func (a *App) proxyPermForPath(path string) []string {
	parts := strings.Split(path, "/")
	if len(parts) < 3 {
		return nil
	}
	prefix := "/" + parts[1] + "/" + parts[2]
	for _, rt := range a.routes {
		if !strings.HasPrefix(rt.Pattern, prefix) {
			continue
		}
		if len(rt.Modules) > 0 {
			return rt.Modules
		}
	}
	return nil
}

func (a *App) proxyAuthGuard(r *http.Request, path string) *HTTPError {
	u, err := Authenticate(r, true)
	if err != nil {
		return err
	}
	if u.Role() != "admin" {
		if !proxyReadonlyAllow(path, r.Method) {
			return NewHTTPError(403, "需要管理员权限")
		}
		return nil
	}
	if set, full := UserPerms(u); !full {
		if mods := a.proxyPermForPath(path); len(mods) > 0 {
			allowed := false
			for _, m := range mods {
				if set[m] {
					allowed = true
					break
				}
			}
			if !allowed {
				return NewHTTPError(403, "需要 "+strings.Join(mods, "/")+" 模块权限")
			}
			return nil
		}
		if !proxyReadonlyAllow(path, r.Method) {
			return NewHTTPError(403, "需要管理员权限")
		}
	}
	return nil
}

// maybeAgentProxy 在「当前节点为已配置 Agent 的远端」时转发请求。
func (a *App) maybeAgentProxy(w http.ResponseWriter, r *http.Request) bool {
	if strings.EqualFold(r.Header.Get("Upgrade"), "websocket") {
		return false
	}
	path := r.URL.Path
	if !shouldAgentProxy(path) {
		return false
	}
	store := loadNodeStore()
	nodeID := RequestNodeID(r)
	node, ok := store.Nodes[nodeID]
	if !ok {
		node = store.Nodes[localNodeID]
	}
	if str(node["type"]) != "ssh" {
		return false
	}
	if !agentReady(node) {
		// 裸 SSH 节点：host 类接口（文件/进程/磁盘/系统/日志/防火墙/终端等）
		// 已通过 hostops 的远程原语在本机透明执行；local 类接口由 remote_cap 中间件拒绝。
		return false
	}
	if err := a.proxyAuthGuard(r, path); err != nil {
		writeErr(w, err)
		return true
	}
	rawBody, _ := io.ReadAll(io.LimitReader(r.Body, GetConfig().MaxUploadByte+1))
	resp, err := agentProxyRequest(node, r, rawBody)
	if err != nil {
		WriteJSON(w, 502, map[string]any{"detail": "Agent 代理失败，请检查子节点 Agent 状态"})
		return true
	}
	defer resp.Body.Close()
	ct := resp.Header.Get("Content-Type")
	if ct == "" {
		ct = "application/json; charset=utf-8"
	}
	w.Header().Set("Content-Type", ct)
	w.WriteHeader(resp.StatusCode)
	_, _ = io.Copy(w, resp.Body)
	return true
}

// bridgeAgentWS 把浏览器 WebSocket 桥接到子节点 Agent 的同名 WS 端点。
// 隧道（SSH direct-tcpip）承载一次 WebSocket 握手与帧级双向转发，
// 子节点侧使用 agent JWT（管理员权限）鉴权。
func bridgeAgentWS(w http.ResponseWriter, r *http.Request, node map[string]any, targetPath string) {
	browser, err := upgradeWS(w, r)
	if err != nil {
		return
	}
	defer browser.Close()
	client, err := pooledSSH(node, 15*time.Second)
	if err != nil {
		_ = browser.WriteText([]byte("\r\n[Agent 连接失败] " + err.Error() + "\r\n"))
		return
	}
	token, err := agentToken(node, client)
	if err != nil {
		_ = browser.WriteText([]byte("\r\n[Agent 鉴权失败] " + err.Error() + "\r\n"))
		return
	}
	port := int(int64Of(node["agent_port"]))
	if port == 0 {
		port = defaultAgentPort
	}
	tunnel, err := client.Dial("tcp", net.JoinHostPort("127.0.0.1", itoa(port)))
	if err != nil {
		_ = browser.WriteText([]byte("\r\n[Agent 隧道不可用] " + err.Error() + "\r\n"))
		return
	}
	defer tunnel.Close()

	u, err := url.Parse("ws://127.0.0.1:" + itoa(port) + targetPath)
	if err != nil {
		return
	}
	q := u.Query()
	q.Set("token", token)
	u.RawQuery = q.Encode()
	child, resp, err := websocket.NewClient(tunnel, u, nil, 4096, 4096)
	if err != nil {
		msg := err.Error()
		if resp != nil {
			msg = fmt.Sprintf("HTTP %d", resp.StatusCode)
			_ = resp.Body.Close()
		}
		_ = browser.WriteText([]byte("\r\n[子节点 WebSocket 连接失败] " + msg + "\r\n"))
		return
	}
	defer child.Close()

	done := make(chan struct{})
	go func() {
		defer close(done)
		defer browser.Close()
		for {
			mt, data, err := child.ReadMessage()
			if err != nil {
				return
			}
			if mt == websocket.TextMessage {
				if browser.WriteText(data) != nil {
					return
				}
			} else {
				if browser.WriteBinary(data) != nil {
					return
				}
			}
		}
	}()
	for {
		mt, data, err := browser.ReadMessage()
		if err != nil {
			break
		}
		if err := child.WriteMessage(mt, data); err != nil {
			break
		}
	}
	_ = child.Close()
	_ = browser.Close()
	<-done
}

// --- 节点路由 ---

func registerNodesAndAgentRoutes(a *App) {
	a.Get("/api/nodes", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		store := loadNodeStore()
		nodes := []map[string]any{}
		ids := []string{}
		for id := range store.Nodes {
			ids = append(ids, id)
		}
		sortStrings(ids)
		for _, id := range ids {
			nodes = append(nodes, publicNode(id, store.Nodes[id]))
		}
		return WriteJSON(w, 200, map[string]any{"nodes": nodes, "current": store.Current})
	})
	a.Get("/api/nodes/current", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		store := loadNodeStore()
		node := store.Nodes[store.Current]
		var info map[string]any
		if str(node["type"]) == "ssh" {
			info = map[string]any{
				"id": str(node["id"]), "name": str(node["name"]), "type": "ssh",
				"host":         str(node["host"]),
				"host_display": str(node["user"]) + "@" + str(node["host"]) + ":" + itoa(int(int64Of(node["port"]))),
			}
		} else {
			info = map[string]any{"id": store.Current, "name": "本机", "type": "local"}
		}
		return WriteJSON(w, 200, map[string]any{"current": store.Current, "node": info})
	})
	a.Post("/api/nodes", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		var req nodeInput
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		node, err := upsertNode("", req)
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, publicNode(str(node["id"]), node))
	})
	a.Put("/api/nodes/{node_id}", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		var req nodeInput
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		node, err := upsertNode(PathParam(r, "node_id"), req)
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, publicNode(str(node["id"]), node))
	})
	a.Delete("/api/nodes/{node_id}", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "node_id")
		if id == localNodeID {
			return NewHTTPError(400, "本地节点不可删除")
		}
		store := loadNodeStoreFresh()
		node, ok := store.Nodes[id]
		if !ok {
			return NewHTTPError(404, "节点不存在")
		}
		dropSSHPool(node)
		forgetSSHHostKey(node)
		delete(store.Nodes, id)
		if store.Current == id {
			store.Current = localNodeID
		}
		if err := saveNodeStore(store); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/nodes/{node_id}/test", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "node_id")
		node := loadNodeStore().Nodes[id]
		if node == nil {
			return NewHTTPError(404, "节点不存在")
		}
		if str(node["type"]) != "ssh" {
			return WriteJSON(w, 200, map[string]any{"node_id": id, "ok": true, "latency_ms": 0})
		}
		start := time.Now()
		client, err := sshConnect(node, 10*time.Second)
		if err != nil {
			return WriteJSON(w, 200, map[string]any{
				"node_id": id, "ok": false, "error": err.Error(),
				"latency_ms": time.Since(start).Milliseconds(),
			})
		}
		client.Close()
		return WriteJSON(w, 200, map[string]any{
			"node_id": id, "ok": true, "latency_ms": time.Since(start).Milliseconds(),
		})
	})
	a.Post("/api/nodes/current", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			NodeID string `json:"node_id"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		store := loadNodeStoreFresh()
		node, ok := store.Nodes[req.NodeID]
		if !ok {
			return NewHTTPError(404, "节点不存在")
		}
		store.Current = req.NodeID
		if err := saveNodeStore(store); err != nil {
			return err
		}
		info := map[string]any{"id": req.NodeID, "name": str(node["name"]), "type": str(node["type"])}
		if str(node["type"]) == "ssh" {
			info["host"] = str(node["host"])
		}
		return WriteJSON(w, 200, map[string]any{"current": req.NodeID, "node": info})
	})
	a.Post("/api/nodes/{node_id}/meta", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "node_id")
		var req struct {
			Group string   `json:"group"`
			Tags  []string `json:"tags"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		store := loadNodeStoreFresh()
		node, ok := store.Nodes[id]
		if !ok {
			return NewHTTPError(404, "节点不存在")
		}
		node["group"] = strings.TrimSpace(req.Group)
		tags := []string{}
		for _, t := range req.Tags {
			if s := strings.TrimSpace(t); s != "" && len(tags) < 12 {
				tags = append(tags, s)
			}
		}
		node["tags"] = tags
		if err := saveNodeStore(store); err != nil {
			return err
		}
		return WriteJSON(w, 200, publicNode(id, node))
	})
	a.Get("/api/nodes/overview", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		store := loadNodeStore()
		results := []map[string]any{}
		for id, node := range store.Nodes {
			pub := publicNode(id, node)
			base := map[string]any{
				"id": id, "name": pub["name"], "type": pub["type"], "host": pub["host"],
				"group": pub["group"], "tags": pub["tags"], "agent_enabled": pub["agent_enabled"],
				"online": true, "latency_ms": 0, "error": "",
			}
			if str(node["type"]) != "ssh" {
				o := metricsHub.overview()
				base["cpu"] = o["cpu"]
				base["memory"] = o["memory"]
				base["storage"] = o["storage"]
				base["load"] = o["load"]
				info := metricsHub.info()
				base["uptime_seconds"] = info["uptime_seconds"]
				base["hostname"] = info["hostname"]
				results = append(results, base)
				continue
			}
			start := time.Now()
			client, err := pooledSSH(node, 10*time.Second)
			if err != nil {
				base["online"] = false
				base["error"] = err.Error()
				results = append(results, base)
				continue
			}
			session, err := client.NewSession()
			if err != nil {
				base["online"] = false
				base["error"] = err.Error()
				results = append(results, base)
				continue
			}
			out, err := session.CombinedOutput(remoteOverviewScript)
			session.Close()
			if err != nil {
				base["online"] = false
				base["error"] = err.Error()
				results = append(results, base)
				continue
			}
			base["latency_ms"] = time.Since(start).Milliseconds()
			for k, v := range parseRemoteOverview(string(out)) {
				base[k] = v
			}
			results = append(results, base)
		}
		return WriteJSON(w, 200, map[string]any{
			"nodes": results, "generated_at": time.Now().UnixMilli(),
		})
	})
}

type nodeInput struct {
	ID           string   `json:"id"`
	Name         string   `json:"name"`
	Host         string   `json:"host"`
	Port         int      `json:"port"`
	User         string   `json:"user"`
	Auth         string   `json:"auth"`
	Password     string   `json:"password"`
	KeyPath      string   `json:"key_path"`
	AgentPort    int      `json:"agent_port"`
	AgentKey     string   `json:"agent_key"`
	AgentSecret  string   `json:"agent_secret"`
	AgentEnabled *bool    `json:"agent_enabled"`
	Group        *string  `json:"group"`
	Tags         []string `json:"tags"`
}

func upsertNode(id string, req nodeInput) (map[string]any, error) {
	if !sshHostRe.MatchString(req.Host) || strings.HasPrefix(req.Host, "-") {
		return nil, NewHTTPError(400, "主机地址非法")
	}
	if !sshUserRe.MatchString(req.User) {
		return nil, NewHTTPError(400, "用户名非法")
	}
	if req.Port == 0 {
		req.Port = 22
	}
	if req.Auth == "" {
		req.Auth = "password"
	}
	if req.Auth != "password" && req.Auth != "key" {
		return nil, NewHTTPError(400, "认证方式非法")
	}
	store := loadNodeStoreFresh()
	if id == "" {
		id = "node_" + randomHex(8)
	}
	old := store.Nodes[id]
	node := map[string]any{
		"id": id, "name": defaultString(req.Name, req.Host), "type": "ssh",
		"host": req.Host, "port": req.Port, "user": req.User, "auth": req.Auth,
		"password": req.Password, "key_path": req.KeyPath,
		"agent_port": func() int {
			if req.AgentPort > 0 {
				return req.AgentPort
			}
			return defaultAgentPort
		}(),
		"agent_key": req.AgentKey, "agent_secret": req.AgentSecret,
	}
	if old != nil {
		if req.Password == "" {
			node["password"] = old["password"]
		}
		if req.AgentSecret == "" {
			node["agent_secret"] = old["agent_secret"]
		}
		node["group"] = old["group"]
		node["tags"] = old["tags"]
	}
	if req.AgentEnabled != nil && !*req.AgentEnabled {
		node["agent_key"] = ""
		node["agent_secret"] = ""
	}
	if req.Group != nil {
		node["group"] = strings.TrimSpace(*req.Group)
	}
	if req.Tags != nil {
		tags := []string{}
		for _, t := range req.Tags {
			if s := strings.TrimSpace(t); s != "" && len(tags) < 12 {
				tags = append(tags, s)
			}
		}
		node["tags"] = tags
	}
	store.Nodes[id] = node
	if err := saveNodeStore(store); err != nil {
		return nil, err
	}
	// 节点凭据变化：清空连接池与 token 缓存。
	dropSSHPool(node)
	agentTokenMu.Lock()
	delete(agentTokens, nodeKey(node))
	agentTokenMu.Unlock()
	return node, nil
}

func sortStrings(v []string) {
	for i := 1; i < len(v); i++ {
		for j := i; j > 0 && v[j] < v[j-1]; j-- {
			v[j], v[j-1] = v[j-1], v[j]
		}
	}
}

const remoteOverviewScript = `
echo "===BLOCKSTAT0==="; head -1 /proc/stat 2>/dev/null
sleep 0.12
echo "===BLOCKSTAT1==="; head -1 /proc/stat 2>/dev/null
echo "===BLOCKMEM==="; grep -E '^(MemTotal|MemAvailable):' /proc/meminfo 2>/dev/null
echo "===BLOCKDF==="; df -kP / 2>/dev/null | tail -1
echo "===BLOCKLOAD==="; cat /proc/loadavg 2>/dev/null
echo "===BLOCKNPROC==="; nproc 2>/dev/null
echo "===BLOCKBTIME==="; grep '^btime' /proc/stat 2>/dev/null
echo "===BLOCKHOST==="; hostname 2>/dev/null
`

func parseRemoteOverview(raw string) map[string]any {
	blocks := map[string]string{}
	cur := ""
	buf := []string{}
	for _, line := range strings.Split(raw, "\n") {
		if strings.HasPrefix(line, "===BLOCK") && strings.HasSuffix(line, "===") {
			if cur != "" {
				blocks[cur] = strings.TrimSpace(strings.Join(buf, "\n"))
			}
			cur = strings.Trim(strings.TrimPrefix(line, "===BLOCK"), "=")
			buf = []string{}
			continue
		}
		buf = append(buf, line)
	}
	if cur != "" {
		blocks[cur] = strings.TrimSpace(strings.Join(buf, "\n"))
	}
	parseStat := func(s string) (int64, int64) {
		f := strings.Fields(s)
		if len(f) < 5 {
			return 0, 0
		}
		total, idle := int64(0), int64(0)
		for i, x := range f[1:] {
			var n int64
			fmt.Sscanf(x, "%d", &n)
			total += n
			if i == 3 {
				idle = n
			}
		}
		return total, idle
	}
	t0, i0 := parseStat(blocks["STAT0"])
	t1, i1 := parseStat(blocks["STAT1"])
	cpu := 0.0
	if t1 > t0 {
		cpu = 100 * (1 - float64(i1-i0)/float64(max64(t1-t0, 1)))
	}
	memTotal, memAvail := int64(0), int64(0)
	for _, line := range strings.Split(blocks["MEM"], "\n") {
		if strings.Contains(line, "MemTotal:") {
			fmt.Sscanf(strings.Fields(line)[1], "%d", &memTotal)
		}
		if strings.Contains(line, "MemAvailable:") {
			fmt.Sscanf(strings.Fields(line)[1], "%d", &memAvail)
		}
	}
	memTotal *= 1024
	memAvail *= 1024
	memUsed := memTotal - memAvail
	memPct := 0.0
	if memTotal > 0 {
		memPct = float64(memUsed) / float64(memTotal) * 100
	}
	storage := map[string]any{"percent": 0.0, "total": int64(0), "used": int64(0), "free": int64(0)}
	df := strings.Fields(blocks["DF"])
	if len(df) >= 5 {
		var total, used, avail int64
		fmt.Sscanf(df[1], "%d", &total)
		fmt.Sscanf(df[2], "%d", &used)
		fmt.Sscanf(df[3], "%d", &avail)
		pct := 0.0
		if total > 0 {
			pct = float64(used) / float64(total) * 100
		}
		storage = map[string]any{
			"percent": round1(pct), "total": total * 1024, "used": used * 1024, "free": avail * 1024,
		}
	}
	load1, load5, load15 := 0.0, 0.0, 0.0
	ld := strings.Fields(blocks["LOAD"])
	if len(ld) >= 3 {
		fmt.Sscanf(ld[0], "%f", &load1)
		fmt.Sscanf(ld[1], "%f", &load5)
		fmt.Sscanf(ld[2], "%f", &load15)
	}
	ncores := int64(1)
	fmt.Sscanf(strings.TrimSpace(blocks["NPROC"]), "%d", &ncores)
	if ncores <= 0 {
		ncores = 1
	}
	btime := int64(0)
	for _, line := range strings.Split(blocks["BTIME"], "\n") {
		f := strings.Fields(line)
		if len(f) >= 2 && f[0] == "btime" {
			fmt.Sscanf(f[1], "%d", &btime)
		}
	}
	uptime := int64(0)
	if btime > 0 {
		uptime = time.Now().Unix() - btime
	}
	return map[string]any{
		"cpu": round1(cpu),
		"memory": map[string]any{
			"percent": round1(memPct), "total": memTotal, "used": memUsed, "available": memAvail,
		},
		"storage": storage,
		"load": map[string]any{
			"percent": round1(minFloat(100, load1/mathMax64(ncores, 1)*100)),
			"load1":   round2(load1), "load5": round2(load5), "load15": round2(load15),
		},
		"uptime_seconds": uptime, "hostname": strings.TrimSpace(blocks["HOST"]),
	}
}

func max64(a, b int64) int64 {
	if a > b {
		return a
	}
	return b
}
func mathMax64(a, b int64) float64 { return float64(max64(a, b)) }
func minFloat(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}
