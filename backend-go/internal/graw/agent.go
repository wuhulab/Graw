package graw

// agent.go — 多节点 / 子节点 Agent 的核心实现：节点表、SSH 连接池、成对密钥鉴权、Agent 隧道代理。
//
// 职责：
//   - 节点表：data/nodes.json（当前节点 + 各 SSH 节点的连接凭据、Agent 端口与成对密钥），带 2 秒读缓存。
//   - 机器间鉴权：子节点暴露 /api/agent/issue，用 HMAC-SHA256(key|ts|nonce) + 时间窗 + nonce 去重
//     换取管理员 JWT；/api/agent/cfg 由登录管理员管理「收取模式」。
//   - 隧道：主面板经 SSH direct-tcpip 连到子节点 127.0.0.1:agent_port，
//     HTTP 走自实现的流式 openAgentConn（proxyToAgent），WebSocket 走 bridgeAgentWS；
//     httpOverSSH 只保留给响应体很小的内部调用（/api/agent/issue）。
//
// 请求流向（httpx.go: ServeHTTP → dispatch）：
//   - HTTP：maybeAgentProxy 先在主面板做与业务路由等价的鉴权，再经隧道流式转发；
//     返回 true 表示请求已处理完。
//   - WebSocket：升级请求不经 maybeAgentProxy（无法 Hijack），由 system/terminal/tamper/docker
//     的 WS 处理器显式调用 bridgeAgentWS 桥接。
//
// 安全边界：
//   - agentProxyExclude 内的前缀（auth/nodes/terminal/agent/ui/shunx/health/batch/gitdeploy/
//     portforward/vpn）永不经隧道代理，避免把面板自身能力或必须主面板执行的任务下放到子节点。
//   - 裸 SSH 节点（未配 Agent）不代理：host 类操作由 hostops.go 远程原语执行，
//     local-only 前缀由 RejectIfLocalRemote 拒绝。
//   - 回前端的节点信息必须走 publicNode 脱敏：password / agent_secret 绝不外泄。
//   - 代理前置鉴权按 RouteInfo 区分 public/custom 路由：这两类不带 Bearer，
//     强制 Authenticate 会让子节点图标、插件令牌接口在当前节点为 Agent 时全部 401。
//
// 易踩坑：
//   - 节点凭据变更后必须 dropSSHPool 并清 agentTokens，否则旧连接 / 旧 token 仍然有效。
//   - SSH 主机密钥采用 TOFU（data/ssh_known_hosts.json）；节点重装或换钥需 forgetSSHHostKey。
//   - agentTokens 只在剩余有效期 > 5 分钟时复用，避免临界过期导致子节点 401。

import (
	"bufio"
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"golang.org/x/crypto/ssh"
)

// 节点 / Agent 协议常量：localNodeID 是本机伪节点 ID；defaultAgentPort 是子节点 Agent
// 默认监听端口；agentTSWindow 是 /api/agent/issue 的签名时间窗（秒），同时用于 nonce 清理。
const (
	localNodeID      = "local"
	defaultAgentPort = 8000
)

// agentTSWindowSeconds 返回 Agent 签名时间窗（秒），支持 GRAW_AGENT_TS_WINDOW 覆盖。
// 允许 30-3600 秒：过小会让时钟偏差正常的节点验签失败，过大则放大重放窗口。
func agentTSWindowSeconds() int64 {
	raw := strings.TrimSpace(os.Getenv("GRAW_AGENT_TS_WINDOW"))
	if raw == "" {
		return 300
	}
	n, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || n < 30 || n > 3600 {
		return 300
	}
	return n
}

// nodeStoreMu 串行化 nodes.json 的写入；sshHostRe / sshUserRe 在建立连接与保存节点前做白名单校验
// （并拒绝 '-' 开头的主机名，防止被当作 ssh 命令行参数注入）。
var (
	nodeStoreMu sync.Mutex
	sshHostRe   = regexp.MustCompile(`^[A-Za-z0-9]([A-Za-z0-9.:\-]*[A-Za-z0-9])?$`)
	sshUserRe   = regexp.MustCompile(`^[A-Za-z0-9_]([A-Za-z0-9_.\-]*[A-Za-z0-9_\-])?$`)
)

// nodeStore 是 data/nodes.json 的内存模型：Version 为格式版本，Current 为全局当前节点，
// Nodes 用 map 保存原始字段，以兼容 Python 旧版多出的自定义键。
type nodeStore struct {
	Version int                       `json:"version"`
	Current string                    `json:"current"`
	Nodes   map[string]map[string]any `json:"nodes"`
}

// defaultNodeStore 返回只含本机节点的初始节点表，用于文件缺失/损坏时的兜底，
// 保证读取方永远拿到可用结构。
func defaultNodeStore() nodeStore {
	// 本机节点始终存在，保证 Current 永远有落点。
	return nodeStore{
		Version: 1, Current: localNodeID,
		Nodes: map[string]map[string]any{
			localNodeID: {"id": localNodeID, "name": "本机", "type": "local"},
		},
	}
}

// 节点表读缓存：2 秒 TTL，避免指标循环与每个请求都解析一次 JSON；
// 写入路径 saveNodeStore 会同步刷新缓存，避免读到旧数据。
var (
	nodeCacheMu    sync.RWMutex
	nodeCache      nodeStore
	nodeCacheAt    time.Time
	nodeCacheValid bool
)

// loadNodeStoreFresh 直接从磁盘读取节点表，并补齐本机节点与空的 Current 字段。
// 读取失败时回落到默认节点表（不返回错误，节点功能不应因此整体不可用）。
// 调用方若准备「读-改-写」，必须用它而不是带缓存的 loadNodeStore。
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
	// 2 秒内直接返回缓存，覆盖指标循环的高频读取。
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

// saveNodeStore 原子写入 nodes.json（WriteJSONFile 内部临时文件 + rename），
// 成功后同步刷新读缓存；失败返回错误且不改缓存。
func saveNodeStore(store nodeStore) error {
	nodeStoreMu.Lock()
	defer nodeStoreMu.Unlock()
	// 写盘期间持锁，防止并发 upsert 互相覆盖。
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

// publicNode 把内部节点结构脱敏成前端可见字段：绝不返回 password / agent_secret，
// 只暴露 has_password、agent_enabled 之类的布尔状态；端口缺失时补默认值。
func publicNode(id string, node map[string]any) map[string]any {
	// 基础字段对所有节点类型都返回。
	pub := map[string]any{
		"id": id, "name": defaultString(str(node["name"]), id), "type": str(node["type"]),
		"group": str(node["group"]), "tags": toStringSlice(node["tags"]),
	}
	if str(node["type"]) == "ssh" {
		// SSH 节点额外暴露连接信息；敏感字段只给「是否已配置」。
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
		// 只有成对 key/secret 都存在才算 Agent 可用。
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

// CurrentNodeID 返回全局当前节点 ID（节点选择持久化值，不含请求级覆盖）。
func CurrentNodeID() string {
	return loadNodeStore().Current
}

// RequestNodeID 返回请求级节点：X-Graw-Node 头优先（统一面板按窗口聚焦节点下发），
// 否则回落到宿主操作解析后的当前节点（可能已被 withNodeScope 覆盖）。
func RequestNodeID(r *http.Request) string {
	if v := strings.TrimSpace(r.Header.Get("X-Graw-Node")); v != "" {
		return v
	}
	return resolvedNodeID()
}

// IsRemoteCurrent 当前（或请求级覆盖后）节点是否为远程 SSH 节点。
// 宿主操作层用它决定走本地 os 还是远程 SSH 原语。
func IsRemoteCurrent() bool {
	n := opsNode()
	return n != nil && str(n["type"]) == "ssh"
}

// currentNode 返回当前生效的节点结构（含请求级覆盖），供 hostops.go 的远程原语使用。
func currentNode() map[string]any {
	return opsNode()
}

// agentReady 判断节点是否已配置 Agent 隧道（SSH 节点且成对 key/secret 齐全）。
// 为 false 的 SSH 节点按「裸 SSH」处理：不代理 HTTP，只能通过远程原语操作宿主机。
func agentReady(node map[string]any) bool {
	return node != nil && str(node["type"]) == "ssh" &&
		str(node["agent_key"]) != "" && str(node["agent_secret"]) != ""
}

// --- 子节点 Agent 收取模式配置 ---

// agentConfig 对应 data/agent.json：子节点「收取模式」开关、成对 key/secret、角色，
// 以及 SecretRevealed 标记（secret 已在管理界面展示过就不再自动回显）。
type agentConfig struct {
	Enabled        bool   `json:"enabled"`
	Key            string `json:"key"`
	Secret         string `json:"secret"`
	Role           string `json:"role"`
	SecretRevealed bool   `json:"secret_revealed"`
}

// loadAgentConfig 读取本地 Agent 配置；文件缺失或 key/secret 不完整时回落到环境变量
// GRAW_AGENT_KEY / GRAW_AGENT_SECRET（两者齐全才视为启用），否则返回未启用的空配置。
// 读取失败有意忽略：未配置 Agent 是正常状态。
func loadAgentConfig() agentConfig {
	cfg := agentConfig{Role: "admin", SecretRevealed: true}
	_ = ReadJSON(DataPath("agent.json"), &cfg)
	if cfg.Key == "" || cfg.Secret == "" {
		// 配置文件里没有成对密钥时，才考虑环境变量（兼容容器化一次性注入）。
		envKey := strings.TrimSpace(os.Getenv("GRAW_AGENT_KEY"))
		envSecret := strings.TrimSpace(os.Getenv("GRAW_AGENT_SECRET"))
		if envKey != "" && envSecret != "" {
			return agentConfig{Enabled: true, Key: envKey, Secret: envSecret, Role: "admin", SecretRevealed: true}
		}
		return agentConfig{Role: "admin", SecretRevealed: true}
	}
	return cfg
}

// agentEnabled 判断本机是否以子节点身份接收主面板请求（开关打开且 key/secret 齐全）。
func agentEnabled() bool {
	cfg := loadAgentConfig()
	return cfg.Enabled && cfg.Key != "" && cfg.Secret != ""
}

// agentPublicStatus 返回可回前端的 Agent 状态：只给 has_secret 布尔值，绝不回显 secret 明文。
func agentPublicStatus() map[string]any {
	cfg := loadAgentConfig()
	enabled := cfg.Enabled && cfg.Key != "" && cfg.Secret != ""
	return map[string]any{
		"enabled": enabled, "role": defaultString(cfg.Role, "admin"), "key": cfg.Key,
		"has_secret": cfg.Secret != "",
		// 只有启用且尚未回显过时才允许前端请求 reveal-secret。
		"can_reveal": enabled && cfg.Secret != "" && !cfg.SecretRevealed,
		"ts_window":  agentTSWindowSeconds(),
	}
}

// agentUsedNonces 记录已用 nonce（key|nonce → 首次时间），窗口内重放直接拒绝；
// 超过 1 万条时顺带清理过期项，防止 map 无界增长。
var agentNonceMu sync.Mutex
var agentUsedNonces = map[string]int64{}

// agentSignature 计算请求签名 HMAC-SHA256(secret, "key|ts|nonce")；
// 主面板（agentToken）与子节点（/api/agent/issue）共用同一算法，改一处必须同步另一处。
func agentSignature(secret, key string, ts int64, nonce string) string {
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(fmt.Sprintf("%s|%d|%s", key, ts, nonce)))
	return hex.EncodeToString(mac.Sum(nil))
}

// registerAgentRoutes 注册 Agent 相关端点：
//   - GET  /api/agent/issue：子节点用成对密钥换管理员 JWT（机器间调用，customAuth 自行验签）；
//   - GET  /api/agent/cfg：读取脱敏后的收取模式；
//   - PUT  /api/agent/cfg：管理员修改开关与密钥；
//   - POST /api/agent/reveal-secret：管理员显式查看 secret（仅首次）。
//
// 注意 /issue 不挂登录依赖：子节点身份靠 HMAC 签名证明，而非面板用户。
func registerAgentRoutes(a *App) {
	a.Get("/api/agent/issue", customAuth(), func(w http.ResponseWriter, r *http.Request) error {
		// 未开启收取模式时返回 404：不向未授权方暴露端点存在。
		if !agentEnabled() {
			return NewHTTPError(404, "Agent API 未启用")
		}
		q := r.URL.Query()
		tsStr, nonce, sig := q.Get("ts"), q.Get("nonce"), q.Get("sig")
		providedKey := r.Header.Get("X-Graw-Agent-Key")
		// 签名三要素缺一不可，先做廉价校验再进入密码学计算。
		if tsStr == "" || nonce == "" || sig == "" {
			return NewHTTPError(400, "缺少 ts/nonce/sig")
		}
		ts, err := strconvParseInt(tsStr)
		if err != nil {
			return NewHTTPError(400, "ts 非法")
		}
		cfg := loadAgentConfig()
		// 用常量时间比较 key，避免按字节比较泄露匹配长度。
		if !hmac.Equal([]byte(providedKey), []byte(cfg.Key)) {
			return NewHTTPError(401, "Agent 凭证校验失败")
		}
		now := time.Now().Unix()
		// 时间窗限制签名可重放的时间；nonce 长度限制防止超长 key 撑爆内存。
		if abs64(now-ts) > agentTSWindowSeconds() || len(nonce) > 128 {
			return NewHTTPError(401, "Agent 凭证校验失败")
		}
		expect := agentSignature(cfg.Secret, providedKey, ts, nonce)
		// 验签失败与 key 错误统一文案，不给探测者区分线索。
		if !hmac.Equal([]byte(expect), []byte(sig)) {
			return NewHTTPError(401, "Agent 凭证校验失败")
		}
		agentNonceMu.Lock()
		key := providedKey + "|" + nonce
		if _, seen := agentUsedNonces[key]; seen {
			agentNonceMu.Unlock()
			return NewHTTPError(401, "Agent 凭证校验失败")
		}
		// 先登记 nonce 再签发，保证同一 nonce 只能用一次（防重放）。
		agentUsedNonces[key] = now
		if len(agentUsedNonces) > 10000 {
			// 顺带清理超出时间窗的历史 nonce，避免 map 无界增长。
			for k, t := range agentUsedNonces {
				if now-t > agentTSWindowSeconds() {
					delete(agentUsedNonces, k)
				}
			}
		}
		agentNonceMu.Unlock()
		// 确保存在 agent 机器账号；它的 token_version 决定已签发 JWT 是否失效。
		if err := ensureAgentUser(); err != nil {
			return err
		}
		user, _ := GetUser("agent")
		tv := 0
		if user != nil {
			tv = user.Int("token_version")
		}
		// 以 agent 账号签发管理员 JWT；管理员改密/踢下线后该 token 会自然失效。
		// 必须带 sid：空 sid 的令牌会被 SessionActive 拒绝，隧道代理将全部 401。
		sid, err := CreateSession("agent", ClientIP(r), "agent tunnel")
		if err != nil {
			return NewHTTPError(500, "创建 Agent 会话失败")
		}
		token, err := CreateToken("agent", tv, sid)
		if err != nil {
			return err
		}
		// expires_in 供主面板安排 token 刷新。
		return WriteJSON(w, 200, map[string]any{
			"token": token, "role": "admin", "expires_in": int(TokenTTL.Seconds()), "username": "agent",
		})
	})
	a.Get("/api/agent/cfg", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		// 只返回脱敏状态（has_secret），不回显 secret。
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
		// key 留空表示沿用旧值，便于前端只切换开关。
		if key == "" {
			key = cur.Key
		}
		secret := strings.TrimSpace(req.Secret)
		secretChanged := false
		if secret != "" {
			// 显式填写 secret 视为轮换密钥。
			secretChanged = true
		} else if req.Enabled {
			// 未填写且仍启用：复用旧 secret。
			secret = cur.Secret
		}
		// 启用前必须两端都有值，避免落盘一份半配置。
		if req.Enabled && (key == "" || secret == "") {
			return NewHTTPError(400, "未配置访问 key/校验 secret，无法启用")
		}
		cfg := agentConfig{Enabled: req.Enabled, Key: key, Secret: secret, Role: "admin"}
		if req.Enabled {
			// 沿用「是否已回显」状态：轮换 secret 后重新允许查看一次。
			cfg.SecretRevealed = !secretChanged && cur.SecretRevealed
		} else {
			// 关闭时清空 secret 并重置回显标记，减少静态凭据留存。
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
		// 未启用或已回显过时返回空串（幂等），避免 secret 被反复取出。
		if !(cfg.Enabled && cfg.Secret != "" && !cfg.SecretRevealed) {
			return WriteJSON(w, 200, map[string]any{"secret": "", "revealed": true})
		}
		// 首次回显后立刻落盘标记，后续 GET /cfg 只给 has_secret。
		cfg.SecretRevealed = true
		_ = WriteJSONFile(DataPath("agent.json"), cfg)
		return WriteJSON(w, 200, map[string]any{"secret": cfg.Secret, "revealed": false, "role": adminRole})
	})
}

// adminRole 是 Agent 机器账号固定使用的角色名（隧道请求需要完整管理员能力）。
const adminRole = "admin"

// ensureAgentUser 确保 users.json 中存在 agent 机器账号（admin 角色、随机密码、
// 不需要改密），供 /api/agent/issue 签发 JWT 使用。已存在且是管理员时不做任何改动。
func ensureAgentUser() error {
	return UpdateUsers(func(users Users) error {
		// 已存在管理员 agent 账号直接复用（可能已有 token_version 等状态）。
		if u, ok := users["agent"]; ok && u.Role() == "admin" {
			return errSkipUsersWrite
		}
		// 随机密码无人知晓，只作占位，禁止用于交互登录。
		hash, err := HashPassword(randomHex(64))
		if err != nil {
			return err
		}
		users["agent"] = User{
			"username": "agent", "password": hash, "role": "admin",
			"must_change_password": false, "created_at": float64(time.Now().Unix()),
		}
		return nil
	})
}

// strconvParseInt 解析十进制时间戳（宽松 Sscanf，容忍前后空白）。
func strconvParseInt(s string) (int64, error) {
	var n int64
	if _, err := fmt.Sscanf(strings.TrimSpace(s), "%d", &n); err != nil {
		return 0, err
	}
	return n, nil
}

// abs64 返回 int64 绝对值，用于时间窗偏差比较。
func abs64(n int64) int64 {
	if n < 0 {
		return -n
	}
	return n
}

// --- SSH 客户端与连接池 ---

// sshPool 以 host:port:user 为键缓存 SSH 客户端，复用握手（慢链路上每次握手都很贵）；
// 复用前必须做 keepalive 探活，死连接直接丢弃重连。连接归池所有，调用方不要 Close。
var (
	sshPoolMu sync.Mutex
	sshPool   = map[string]*ssh.Client{}
)

// nodeKey 生成连接池键（host:port:user）；不含密码，避免凭据进入缓存键。
func nodeKey(node map[string]any) string {
	return fmt.Sprintf("%s:%d:%s", str(node["host"]), int(int64Of(node["port"])), str(node["user"]))
}

// sshConnect 建立一条新的 SSH 连接（不走连接池），用于节点连通性测试与池内重连。
// host/user 先做白名单校验（并拒绝 '-' 开头，防止被当成 ssh 参数），
// 再按 auth=key/password 选择认证方式；主机密钥走 TOFU。
// 失败返回错误，调用方决定如何提示（测试接口会把错误原样回给前端）。
func sshConnect(node map[string]any, timeout time.Duration) (*ssh.Client, error) {
	host := str(node["host"])
	port := int(int64Of(node["port"]))
	if port == 0 {
		port = 22
	}
	// 主机名白名单：拦掉非法字符与 '-' 开头（避免命令参数注入）。
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
		// 私钥路径依次尝试：原样 / HOST_ROOT 映射 / data 目录，兼容「本机直跑、容器部署、密钥随面板存放」三种情况。
		candidates := []string{keyPath, HostPath(keyPath), DataPath(keyPath)}
		var keyData []byte
		var readErr error
		for _, cand := range candidates {
			if cand == "" {
				continue
			}
			abs, absErr := filepath.Abs(cand)
			if absErr != nil {
				continue
			}
			// cleanHostView 把宿主机视角路径映射到容器内可访问路径。
			if keyData, readErr = os.ReadFile(cleanHostView(abs)); readErr == nil {
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
		// 默认走密码认证（auth=key 但缺 key_path 时也回落到密码）。
		auths = append(auths, ssh.Password(str(node["password"])))
	}
	addr := net.JoinHostPort(host, itoa(port))
	// TCP 握手先受 timeout 约束；协商完成前保留 deadline，避免半开连接卡住。
	raw, err := net.DialTimeout("tcp", addr, timeout)
	if err != nil {
		return nil, err
	}
	_ = raw.SetDeadline(time.Now().Add(timeout))
	cfg := &ssh.ClientConfig{
		User: user, Auth: auths, Timeout: timeout,
		// TOFU：首次连接记录指纹，后续不一致即拒绝（防中间人）。
		HostKeyCallback: sshHostKeyCallback(addr),
	}
	conn, chans, reqs, err := ssh.NewClientConn(raw, addr, cfg)
	if err != nil {
		raw.Close()
		return nil, err
	}
	return ssh.NewClient(conn, chans, reqs), nil
}

// knownHostsMu 串行化 data/ssh_known_hosts.json 的读写（TOFU 指纹表）。
var knownHostsMu sync.Mutex

// sshHostKeyCallback 实现 TOFU：首次记录指纹，后续必须一致；不一致报中间人风险并提示人工清理记录。
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
		// 首次见到该 host:port：记录指纹后才放行。
		known[addr] = fp
		_ = WriteJSONFile(path, known)
		return nil
	}
}

// pooledSSH 返回可用的 SSH 客户端：优先复用池内连接，用 keepalive 探活，失效则关闭重连；
// 新建成功后替换池内旧连接。并发由 sshPoolMu 串行化。
// 返回的连接归连接池所有，调用方不要主动 Close（节点删除/凭据变更时由 dropSSHPool 关闭）。
func pooledSSH(node map[string]any, timeout time.Duration) (*ssh.Client, error) {
	key := nodeKey(node)
	sshPoolMu.Lock()
	if c, ok := sshPool[key]; ok {
		// keepalive 请求成功说明链路仍可用，直接复用。
		_, _, err := c.SendRequest("keepalive@graw", true, nil)
		if err == nil {
			sshPoolMu.Unlock()
			return c, nil
		}
		// 探活失败：关闭并移除，随后重连。
		_ = c.Close()
		delete(sshPool, key)
	}
	sshPoolMu.Unlock()
	c, err := sshConnect(node, timeout)
	if err != nil {
		return nil, err
	}
	sshPoolMu.Lock()
	// 并发下可能有别的协程刚放入新连接，替换前先关掉旧的，避免泄漏。
	if old, ok := sshPool[key]; ok {
		_ = old.Close()
	}
	sshPool[key] = c
	sshPoolMu.Unlock()
	return c, nil
}

// dropSSHPool 关闭并移除节点连接池条目；凭据变更或删除节点时必须调用，
// 否则旧凭据建立的连接会继续可用。
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
	// 按 host:port 删除；下次连接会重新走 TOFU 记录。
	delete(known, addr)
	_ = WriteJSONFile(DataPath("ssh_known_hosts.json"), known)
}

// --- Agent 隧道 HTTP ---

// agentTokens 缓存子节点签发的 JWT（按节点键），只在剩余有效期 > 5 分钟时复用；
// 节点凭据变更时由 upsertNode 清空。
var (
	agentTokenMu sync.Mutex
	agentTokens  = map[string]struct {
		Token string
		Exp   time.Time
	}{}
)

// agentToken 取得（必要时刷新）节点 Agent 的 JWT：用节点保存的成对密钥签名，
// 经 SSH 隧道请求子节点 /api/agent/issue。返回错误表示子节点未启用、签名被拒或隧道不可用。
// 主面板与子节点时钟偏差需落在 agentTSWindow（300s）内，否则会验签失败。
func agentToken(node map[string]any, client *ssh.Client) (string, error) {
	key := nodeKey(node)
	agentTokenMu.Lock()
	// 剩余有效期不足 5 分钟不复用，避免请求进行到一半 token 过期。
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
	// 签名算法与子节点 /api/agent/issue 的校验逻辑必须完全一致。
	sig := agentSignature(str(node["agent_secret"]), str(node["agent_key"]), now, nonce)
	path := fmt.Sprintf("/api/agent/issue?ts=%d&nonce=%s&sig=%s", now, nonce, sig)
	// 请求经 SSH 隧道打到子节点 127.0.0.1，不经过公网；Agent 只监听回环地址。
	status, _, body, err := httpOverSSH(client, port, http.MethodGet, path,
		map[string]string{"X-Graw-Agent-Key": str(node["agent_key"])}, nil)
	if err != nil {
		return "", err
	}
	var payload struct {
		Token     string `json:"token"`
		ExpiresIn int64  `json:"expires_in"`
	}
	// 非 200、JSON 解析失败或空 token 一律视为鉴权失败，不继续代理。
	if status != 200 || json.Unmarshal(body, &payload) != nil || payload.Token == "" {
		return "", fmt.Errorf("Agent 换取 token 失败: %d %s", status, string(body))
	}
	if payload.ExpiresIn <= 0 {
		// 子节点没给过期时间，无法判断安全的缓存窗口：直接返回但不缓存。
		// 按固定 7 天缓存一个实际可能更短效的 token，会在其过期后持续 401。
		return payload.Token, nil
	}
	agentTokenMu.Lock()
	agentTokens[key] = struct {
		Token string
		Exp   time.Time
	}{Token: payload.Token, Exp: time.Now().Add(time.Duration(payload.ExpiresIn) * time.Second)}
	agentTokenMu.Unlock()
	return payload.Token, nil
}

// httpOverSSH 在已有 SSH 连接上开一条 direct-tcpip 通道，手写最小 HTTP/1.0 请求到子节点
// 127.0.0.1:port，返回状态码/响应头/响应体。
// 仅用于响应体较小的内部调用（如 /api/agent/issue）；业务代理请走 proxyToAgent 的流式实现，
// 不能把大文件整包读进内存。
func httpOverSSH(client *ssh.Client, port int, method, path string, headers map[string]string, body []byte) (int, http.Header, []byte, error) {
	conn, reader, err := openAgentConn(client, port, method, path, headers, bytes.NewReader(body), int64(len(body)))
	if err != nil {
		return 0, nil, nil, err
	}
	defer conn.Close()
	// 用标准库解析响应，兼容 chunked / Content-Length 等 framing。
	resp, err := http.ReadResponse(reader, &http.Request{Method: method})
	if err != nil {
		return 0, nil, nil, err
	}
	defer resp.Body.Close()
	// 必须把读错误传出去：截断的 JSON 不能被当成成功响应。
	respBody, err := io.ReadAll(&agentIdleReader{conn: conn, body: resp.Body, idle: 60 * time.Second})
	if err != nil {
		return 0, nil, nil, err
	}
	return resp.StatusCode, resp.Header, respBody, nil
}

// openAgentConn 建立隧道并写完请求头/请求体；返回的连接由调用方 Close。
// body 为 nil 或 bodyLen<=0 时不发送 Content-Length（对应无请求体的 GET）。
func openAgentConn(client *ssh.Client, port int, method, path string, headers map[string]string, body io.Reader, bodyLen int64) (net.Conn, *bufio.Reader, error) {
	// SSH 通道内再连子节点回环地址，Agent 只监听本机，不暴露到外网。
	conn, err := client.Dial("tcp", net.JoinHostPort("127.0.0.1", itoa(port)))
	if err != nil {
		return nil, nil, err
	}
	// 写阶段按请求体大小放宽：基础 60 秒 + 每 MB 1 秒，封顶 30 分钟。
	// 固定 60 秒会让经隧道转发的大上传在中途被 deadline 截断。
	writeTimeout := 60*time.Second + time.Duration(bodyLen/(1<<20))*time.Second
	if writeTimeout > 30*time.Minute {
		writeTimeout = 30 * time.Minute
	}
	_ = conn.SetWriteDeadline(time.Now().Add(writeTimeout))
	// 手工拼请求行：net/http 的 Transport 无法复用这条自定义连接。
	var req strings.Builder
	req.WriteString(method + " " + path + " HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n")
	if bodyLen > 0 {
		req.WriteString("Content-Length: " + itoa(int(bodyLen)) + "\r\n")
	}
	for k, v := range headers {
		req.WriteString(k + ": " + v + "\r\n")
	}
	req.WriteString("\r\n")
	if _, err := io.WriteString(conn, req.String()); err != nil {
		_ = conn.Close()
		return nil, nil, err
	}
	if body != nil && bodyLen > 0 {
		// 流式转发请求体：内存占用恒定，不受上传大小影响。
		if _, err := io.Copy(conn, body); err != nil {
			_ = conn.Close()
			return nil, nil, err
		}
	}
	// 写阶段结束：清掉写超时，改设响应头读取超时。
	_ = conn.SetWriteDeadline(time.Time{})
	_ = conn.SetReadDeadline(time.Now().Add(60 * time.Second))
	return conn, bufio.NewReader(conn), nil
}

// agentIdleReader 在每次读之前刷新连接读截止时间：大响应可以超过单次 60 秒，
// 但连接空闲超过 idle 仍会被断开，避免半开连接长期占用。
type agentIdleReader struct {
	conn net.Conn
	body io.Reader
	idle time.Duration
}

// Read 先续期再读，截止时间按「空闲」而非「总时长」计算。
func (r *agentIdleReader) Read(p []byte) (int, error) {
	_ = r.conn.SetReadDeadline(time.Now().Add(r.idle))
	return r.body.Read(p)
}

// agentProxyRequestURI 返回真正发给子节点的请求 URI。
//
// 兼容旧版子节点（≤1.5.3 的 FastAPI 实现）：备忘录路由被注册成 /api/notes/，
// 不带尾斜杠会直接 404（其余接口反过来只认无尾斜杠，不能统一追斜杠）。
// 新版 Go 子节点会在 ServeHTTP 里把 /api/notes/ 归一为 /api/notes，
// 所以这里补斜杠对新旧两代节点都能命中。
func agentProxyRequestURI(r *http.Request) string {
	if r.URL.Path == "/api/notes" {
		if r.URL.RawQuery != "" {
			return "/api/notes/?" + r.URL.RawQuery
		}
		return "/api/notes/"
	}
	return r.URL.RequestURI()
}

// proxyToAgent 把请求经 SSH 隧道转发到子节点，并把响应流式写回浏览器。
// 上传直接流式读取 r.Body，下载边读边写，内存占用与文件大小无关。
// 只透传 Content-Type / Accept 与 Authorization（agent JWT），
// 不转发浏览器原始 token，避免把用户凭据留在子节点。
func proxyToAgent(w http.ResponseWriter, r *http.Request, node map[string]any) {
	// 复用池内 SSH，避免每个业务请求都重新握手。
	client, err := pooledSSH(node, 15*time.Second)
	if err != nil {
		WriteJSON(w, 502, map[string]any{"detail": "Agent 代理失败，请检查子节点 Agent 状态"})
		return
	}
	// 换取（或复用缓存中的）子节点 agent JWT。
	token, err := agentToken(node, client)
	if err != nil {
		WriteJSON(w, 502, map[string]any{"detail": "Agent 代理失败，请检查子节点 Agent 状态"})
		return
	}
	port := int(int64Of(node["agent_port"]))
	if port == 0 {
		port = defaultAgentPort
	}
	headers := map[string]string{"Authorization": "Bearer " + token}
	if ct := r.Header.Get("Content-Type"); ct != "" && r.ContentLength > 0 {
		headers["Content-Type"] = ct
	}
	if accept := r.Header.Get("Accept"); accept != "" {
		headers["Accept"] = accept
	}
	// 请求体直接流式转发；Content-Length 已由全局中间件校验过，不会出现 chunked。
	var body io.Reader
	bodyLen := r.ContentLength
	if bodyLen > 0 {
		body = r.Body
	}
	// 用 RequestURI 保留 query，子节点路由才能命中。
	conn, reader, err := openAgentConn(client, port, r.Method, agentProxyRequestURI(r), headers, body, bodyLen)
	if err != nil {
		WriteJSON(w, 502, map[string]any{"detail": "Agent 代理失败，请检查子节点 Agent 状态"})
		return
	}
	defer conn.Close()
	resp, err := http.ReadResponse(reader, &http.Request{Method: r.Method})
	if err != nil {
		WriteJSON(w, 502, map[string]any{"detail": "Agent 代理失败，请检查子节点 Agent 状态"})
		return
	}
	defer resp.Body.Close()
	// 只回写与内容展示/下载进度相关的头，避免把子节点的安全头/Set-Cookie 混进主面板响应。
	for _, name := range []string{"Content-Type", "Content-Disposition", "Content-Length", "Content-Encoding", "Cache-Control", "ETag", "Last-Modified"} {
		if v := resp.Header.Get(name); v != "" {
			w.Header().Set(name, v)
		}
	}
	if w.Header().Get("Content-Type") == "" {
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
	}
	w.WriteHeader(resp.StatusCode)
	// 流式复制：子节点空闲超过 60 秒或连接中断都会让 io.Copy 返回错误。
	// 响应头已发出，无法再改状态码，只能中止连接（ErrAbortHandler），
	// 绝不能把截断内容当完整响应静默返回。
	if _, err := io.Copy(w, &agentIdleReader{conn: conn, body: resp.Body, idle: 60 * time.Second}); err != nil {
		log.Printf("graw: Agent 代理响应中断: %v", err)
		panic(http.ErrAbortHandler)
	}
}

// --- 代理前置鉴权 ---

// proxyUserSafe 是「受限管理员也可经隧道访问」的公共只读端点白名单（按方法匹配），
// 作为无模块权限时的兜底；与 Python 版一致，新增公共只读接口才考虑加入。
var proxyUserSafe = map[string][]string{
	"/api/system": {"GET"},
	"/api/notes":  {"GET"},
	"/api/tamper": {"GET"},
}

// agentProxyExclude 列出永不经 Agent 隧道代理的前缀：
//   - 面板自身：auth / nodes / ui / shunx / agent / health；
//   - 必须主面板执行：batch / gitdeploy / portforward / vpn；
//   - WebSocket 终端：terminal（需要单独的 WS 桥接）。
//
// 改动此表会直接改变远端能力边界，务必同步 AGENTS.md 与文档。
var agentProxyExclude = []string{
	"/api/auth", "/api/nodes", "/api/terminal", "/api/agent", "/api/ui",
	"/api/shunx", "/api/health", "/api/batch", "/api/gitdeploy", "/api/portforward", "/api/vpn",
}

// localOnlyPrefixes 是远端（裸 SSH 节点）不可用的「本机专属」接口：
// 站点/数据库/证书/备份等管理的是主面板所在宿主机的环境，转发到子节点没有意义且会造成越权。
// 新增面板自身管理类接口时必须在此登记，否则裸 SSH 节点会放行到无意义的本机数据上。
var localOnlyPrefixes = []string{
	"/api/sites", "/api/databases", "/api/cron", "/api/ssl", "/api/protection",
	"/api/runtime", "/api/tasks", "/api/appstore", "/api/backup", "/api/netstorage",
	"/api/notify", "/api/uptime", "/api/webstats", "/api/rewrite", "/api/sitesopts",
	"/api/waf", "/api/tamper", "/api/phpversions", "/api/ftpusers", "/api/sshkeys",
	"/api/certcheck", "/api/panelbackup", "/api/update", "/api/webmode", "/api/loginlog",
	"/api/netmap", "/api/plugins", "/api/op",
}

// shouldAgentProxy 判断路径是否允许走 Agent 隧道：必须是 /api/ 前缀且不在排除表内。
// 先去掉尾部斜杠做归一，避免 /api/nodes/ 之类绕过排除判断。
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

// isLocalOnlyPath 判断路径是否属于本机专属接口（前缀匹配）。
func isLocalOnlyPath(path string) bool {
	for _, p := range localOnlyPrefixes {
		if path == p || strings.HasPrefix(path, p+"/") {
			return true
		}
	}
	return false
}

// RejectIfLocalRemote 当当前节点为远端且路径为 local 类时返回 true，
// 调用方（httpx.dispatch）应据此返回 403。仅对裸 SSH 节点生效：
// 配了 Agent 的远端请求已在 maybeAgentProxy 被转发，不会走到这里。
func RejectIfLocalRemote(path string) bool {
	return IsRemoteCurrent() && isLocalOnlyPath(path)
}

// proxyReadonlyAllow 判断受限管理员是否可按该方法访问公共只读端点（proxyUserSafe）。
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

// routeInfoFor 按方法与路径查找已注册路由的鉴权元信息。
// 返回 nil 表示主面板没有该路由（可能是子节点独有接口）。
func (a *App) routeInfoFor(method, path string) *RouteInfo {
	clean := strings.TrimRight(path, "/")
	if clean == "" {
		clean = "/"
	}
	for i := range a.routes {
		rt := &a.routes[i]
		if rt.Method != method {
			continue
		}
		if matchRoutePattern(rt.Pattern, clean) {
			return rt
		}
	}
	return nil
}

// matchRoutePattern 比较注册模式与请求路径：{name} 匹配单段，{name...} 匹配剩余所有段。
// 只用于鉴权元信息查找，不参与实际路由分发（分发由 ServeMux 负责）。
func matchRoutePattern(pattern, path string) bool {
	if pattern == path {
		return true
	}
	ps := strings.Split(strings.Trim(pattern, "/"), "/")
	xs := strings.Split(strings.Trim(path, "/"), "/")
	for i, p := range ps {
		if strings.HasPrefix(p, "{") && strings.HasSuffix(p, "...}") {
			// 通配剩余段（可为空），必须位于模式末尾。
			return i == len(ps)-1 && len(xs) >= i
		}
		if i >= len(xs) {
			return false
		}
		if strings.HasPrefix(p, "{") && strings.HasSuffix(p, "}") {
			if xs[i] == "" {
				return false
			}
			continue
		}
		if p != xs[i] {
			return false
		}
	}
	return len(ps) == len(xs)
}

// proxyAuthGuard 在转发前于主面板做与业务路由等价的鉴权。
// Why：子节点 Agent 以管理员权限执行请求，若不在本机先校验，受限用户可借隧道越权。
// 规则：public/custom 端点交由子节点自行鉴权；其余未登录/默认密码拒绝；
// 完整管理员放行；受限管理员要求命中该路由声明的模块权限，否则只允许公共只读白名单。
func (a *App) proxyAuthGuard(r *http.Request, path string) *HTTPError {
	info := a.routeInfoFor(r.Method, path)
	// 公开端点（应用图标等）与自定义鉴权端点（插件令牌 / Agent 密钥）不依赖 Bearer，
	// 不能在这里用 Authenticate 拦掉，否则它们在当前节点为 Agent 节点时会全部 401。
	if info != nil && (info.Public || info.Custom) {
		return nil
	}
	// failClosedOnDefault=true：默认密码账号不允许经隧道操作子节点。
	u, err := Authenticate(r, true)
	if err != nil {
		return err
	}
	if u.Role() != "admin" {
		// 非管理员只放行公共只读端点，其余一律 403。
		if !proxyReadonlyAllow(path, r.Method) {
			return NewHTTPError(403, "需要管理员权限")
		}
		return nil
	}
	if set, full := UserPerms(u); !full {
		// 受限管理员：按该路由声明的模块权限判定；仅管理员路由直接拒绝。
		if info != nil && info.Admin {
			return NewHTTPError(403, "需要管理员权限")
		}
		if info != nil && len(info.Modules) > 0 {
			for _, m := range info.Modules {
				if set[m] {
					return nil
				}
			}
			return NewHTTPError(403, "需要 "+strings.Join(info.Modules, "/")+" 模块权限")
		}
		// 路由未声明模块权限：退回公共只读白名单，宁严勿松。
		if !proxyReadonlyAllow(path, r.Method) {
			return NewHTTPError(403, "需要管理员权限")
		}
	}
	return nil
}

// maybeAgentProxy 在「当前（或请求级）节点是已配置 Agent 的远程 SSH 节点」时，
// 把业务请求经隧道转发到子节点。返回 true 表示请求已被处理（成功或已写错误），
// 调用方不应继续分发；返回 false 表示按本机 / 裸 SSH 路径继续处理。
//
// 顺序很重要：必须先于 remote_cap（RejectIfLocalRemote）执行，否则 local 类接口
// 会在主面板就被 403，无法代理到子节点执行。
func (a *App) maybeAgentProxy(w http.ResponseWriter, r *http.Request) bool {
	// WebSocket 升级不代理：需要 Hijack，交由各 WS 处理器显式 bridgeAgentWS。
	if strings.EqualFold(r.Header.Get("Upgrade"), "websocket") {
		return false
	}
	path := r.URL.Path
	// 排除表内的前缀永不经隧道（面板自身 / 必须主面板执行）。
	if !shouldAgentProxy(path) {
		return false
	}
	store := loadNodeStore()
	nodeID := RequestNodeID(r)
	node, ok := store.Nodes[nodeID]
	if !ok {
		// 未知节点回落到本机，避免拼错的 X-Graw-Node 把请求发到意外主机。
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
	// 先在本机完成等价鉴权再代理，防止受限用户借隧道越权到子节点的管理员接口。
	if err := a.proxyAuthGuard(r, path); err != nil {
		writeErr(w, err)
		return true
	}
	// 流式转发：大上传/下载不再整包驻留内存，失败统一 502，
	// 避免把子节点内部错误细节（含地址/凭据线索）暴露给前端。
	proxyToAgent(w, r, node)
	return true
}

// bridgeAgentWS 把浏览器 WebSocket 桥接到子节点 Agent 的同名 WS 端点。
// 隧道（SSH direct-tcpip）承载一次 WebSocket 握手与帧级双向转发，
// 子节点侧使用 agent JWT（管理员权限）鉴权。
// 调用方（system/terminal/tamper/docker 的 WS 处理器）已完成本机用户鉴权；
// 桥接失败时用文本帧告知浏览器并关闭，不返回 HTTP 错误（此时连接已升级）。
func bridgeAgentWS(w http.ResponseWriter, r *http.Request, node map[string]any, targetPath string) {
	// 先把浏览器侧升级为 WebSocket；失败直接结束（upgradeWS 已写响应）。
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
	// 拿子节点 agent JWT，子节点 WS 用 ?token= 鉴权（浏览器 token 无法透传）。
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

	// 交给 gorilla 的 URL 只是握手目标；实际连接走 SSH 通道（tunnel）。
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
			// 握手失败时尽量给出 HTTP 状态码，方便前端提示。
			msg = fmt.Sprintf("HTTP %d", resp.StatusCode)
			_ = resp.Body.Close()
		}
		_ = browser.WriteText([]byte("\r\n[子节点 WebSocket 连接失败] " + msg + "\r\n"))
		return
	}
	defer child.Close()

	// 子节点 → 浏览器 单向转发；任一方向出错即关闭两端，让主循环也退出。
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
	// 浏览器 → 子节点 转发；浏览器断开即退出。
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
	// 等上行 goroutine 退出，避免泄漏。
	<-done
}

// --- 节点路由 ---

// registerNodesAndAgentRoutes 注册 /api/nodes 下的 HTTP 端点（全部管理员）：
// 节点 CRUD、当前节点切换/查询、元数据（分组/标签）、连通性测试与批量概览。
// 真正的代理与隧道在 maybeAgentProxy / bridgeAgentWS，这里只管理与观测节点。
func registerNodesAndAgentRoutes(a *App) {
	a.Get("/api/nodes", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		store := loadNodeStore()
		nodes := []map[string]any{}
		ids := []string{}
		for id := range store.Nodes {
			ids = append(ids, id)
		}
		// 按 ID 排序输出，便于前端稳定展示（map 遍历顺序随机）。
		sortStrings(ids)
		for _, id := range ids {
			// publicNode 负责脱敏，绝不回传 password/agent_secret。
			nodes = append(nodes, publicNode(id, store.Nodes[id]))
		}
		return WriteJSON(w, 200, map[string]any{"nodes": nodes, "current": store.Current})
	})
	a.Get("/api/nodes/current", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		store := loadNodeStore()
		node := store.Nodes[store.Current]
		var info map[string]any
		if str(node["type"]) == "ssh" {
			// 展示用串只拼 user@host:port，不含任何凭据。
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
		// upsertNode 统一做输入校验、凭据继承与缓存失效。
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
		// 先断开连接池并清 TOFU 记录，避免删除后旧连接/旧信任残留。
		dropSSHPool(node)
		forgetSSHHostKey(node)
		delete(store.Nodes, id)
		if store.Current == id {
			// 删的是当前节点：回落到本机，避免全局 Current 指向不存在的节点。
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
			// 本机节点无需连接测试，直接 ok。
			return WriteJSON(w, 200, map[string]any{"node_id": id, "ok": true, "latency_ms": 0})
		}
		start := time.Now()
		// 测试用短连接且不入池，避免把不通的节点塞进连接池。
		client, err := sshConnect(node, 10*time.Second)
		if err != nil {
			// 连接失败也返回 200 + ok:false，让前端展示具体错误。
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
		// 切换的是持久化的全局当前节点；请求级 X-Graw-Node 覆盖不会落盘。
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
		// 分组/标签只做展示分类，顺手限制数量与空串，避免元数据无限膨胀。
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
				// 本机节点直接用指标中心快照，不额外采集。
				o := metricsHub.overview()
				base["cpu"] = o["cpu"]
				base["memory"] = o["memory"]
				base["storage"] = o["storage"]
				base["load"] = o["load"]
				info := metricsHub.info()
				base["uptime_seconds"] = info["uptime_seconds"]
				base["hostname"] = info["hostname"]
				base["version"] = SelfAppVersion()
				results = append(results, base)
				continue
			}
			// 远程节点逐台采集；单台失败只标记该节点 offline，不影响其它节点。
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
			// 一次 SSH 执行采集脚本，拿到 CPU/内存/磁盘/负载/运行时长。
			out, err := session.CombinedOutput(remoteOverviewScript)
			session.Close()
			if err != nil {
				base["online"] = false
				base["error"] = err.Error()
				results = append(results, base)
				continue
			}
			// 延迟含 SSH 往返，作为节点健康度参考。
			base["latency_ms"] = time.Since(start).Milliseconds()
			for k, v := range parseRemoteOverview(string(out)) {
				base[k] = v
			}
			// 版本号经 Agent 隧道读取；裸 SSH 节点或旧版无该接口时为空，
			// 由前端提示「早期版本，请及时更新」。
			base["version"] = remotePanelVersion(client, node)
			results = append(results, base)
		}
		return WriteJSON(w, 200, map[string]any{
			"nodes": results, "generated_at": time.Now().UnixMilli(),
		})
	})
}

// nodeInput 是节点新增/修改请求体；AgentEnabled 用指针区分「未提交」与「显式关闭」。
// 敏感字段（password / agent_secret）留空表示沿用旧值，前端从不回显明文。
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

// upsertNode 新增或更新 SSH 节点：校验主机/用户名/认证方式；未填的 password 与
// agent_secret 沿用旧值（前端不回显敏感字段），显式关闭 Agent 时清空成对密钥。
// 成功后清空该节点的 SSH 连接池与 token 缓存，使新凭据立即生效。
// id 为空表示新建（自动生成 node_<random>）。
func upsertNode(id string, req nodeInput) (map[string]any, error) {
	// 与 sshConnect 同一套白名单，避免保存时通过、连接时才报错的割裂体验。
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
	// 只允许两种认证方式，避免落盘无法解释的配置。
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
		// 更新时敏感字段留空 = 沿用旧值；group/tags 也先继承，稍后按需覆盖。
		if req.Password == "" {
			node["password"] = old["password"]
		}
		// agent_key 与 agent_secret 是成对凭据，留空时必须各自沿用旧值；
		// 只继承 secret 会让前端留空提交时把原本启用的 Agent 静默降级为未启用。
		if req.AgentKey == "" {
			node["agent_key"] = old["agent_key"]
		}
		if req.AgentSecret == "" {
			node["agent_secret"] = old["agent_secret"]
		}
		node["group"] = old["group"]
		node["tags"] = old["tags"]
	}
	if req.AgentEnabled != nil && !*req.AgentEnabled {
		// 显式关闭 Agent：成对密钥一并清空，避免残留可用凭据。
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
	// 节点凭据变化：清空连接池与 token 缓存，确保新凭据立即生效。
	// 旧键也要清理：host/user/port 变化后 nodeKey 会变，只删新键会让旧连接长期滞留在池里。
	if old != nil {
		dropSSHPool(old)
	}
	dropSSHPool(node)
	agentTokenMu.Lock()
	delete(agentTokens, nodeKey(node))
	if old != nil {
		delete(agentTokens, nodeKey(old))
	}
	agentTokenMu.Unlock()
	return node, nil
}

// sortStrings 对字符串切片做插入排序（节点数量很小，避免为此引入额外依赖）。
func sortStrings(v []string) {
	for i := 1; i < len(v); i++ {
		for j := i; j > 0 && v[j] < v[j-1]; j-- {
			v[j], v[j-1] = v[j-1], v[j]
		}
	}
}

// nodeAgentPort 返回节点 Agent 监听端口；未配置时回落到默认端口。
func nodeAgentPort(node map[string]any) int {
	if p := int(int64Of(node["agent_port"])); p > 0 {
		return p
	}
	return defaultAgentPort
}

// remotePanelVersion 经 Agent 隧道读取子节点 /api/health 中的面板版本。
//
// 仅在节点启用 Agent 时可用：裸 SSH 节点、旧版没有该接口或请求失败都返回空串，
// 前端据此显示「早期版本，请及时更新」，不把未知版本误判为正常。
func remotePanelVersion(client *ssh.Client, node map[string]any) string {
	if !agentReady(node) {
		return ""
	}
	status, _, body, err := httpOverSSH(client, nodeAgentPort(node), http.MethodGet, "/api/health", nil, nil)
	if err != nil || status != http.StatusOK {
		return ""
	}
	var payload struct {
		Version string `json:"version"`
	}
	if json.Unmarshal(body, &payload) != nil {
		return ""
	}
	return strings.TrimSpace(payload.Version)
}

// remoteOverviewScript 是远程概览采集脚本：用 ===BLOCKxxx=== 标记分隔各段，
// 便于 parseRemoteOverview 解析；两次 /proc/stat 间隔 0.12s 估算 CPU 使用率。
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

// parseRemoteOverview 解析 remoteOverviewScript 的带标记输出，换算成与本地指标中心
// 一致的字段（cpu 百分比、内存/磁盘字节数、load 百分比、运行时长、hostname）。
// 解析失败时对应字段取 0，不返回错误——概览页允许部分指标缺失。
func parseRemoteOverview(raw string) map[string]any {
	// 先按 ===BLOCKxxx=== 分段收集文本。
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
	// parseStat 汇总 /proc/stat 首行的 total 与 idle 计数。
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
	// CPU：用两次采样的 total/idle 差值估算使用率。
	t0, i0 := parseStat(blocks["STAT0"])
	t1, i1 := parseStat(blocks["STAT1"])
	cpu := 0.0
	if t1 > t0 {
		cpu = 100 * (1 - float64(i1-i0)/float64(max64(t1-t0, 1)))
	}
	// 内存：MemAvailable 比 MemFree 更接近真实可用量；/proc/meminfo 单位为 KB。
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
	// 磁盘：df -kP 输出单位为 KB，统一乘 1024 转字节。
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
	// 负载：load1 相对核数换算成百分比，与本地指标中心字段保持一致。
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
	// 运行时长：由 /proc/stat 的 btime（启动时刻）推算。
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

// max64 返回两个 int64 中较大者。
func max64(a, b int64) int64 {
	if a > b {
		return a
	}
	return b
}

// mathMax64 返回两个 int64 中较大者的 float64 形式（用于负载百分比计算）。
func mathMax64(a, b int64) float64 { return float64(max64(a, b)) }

// minFloat 返回两个 float64 中较小者（负载百分比封顶 100）。
func minFloat(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}
