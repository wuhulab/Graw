package graw

// httpx.go —— HTTP 入口与路由骨架。
//
// 本文件负责三件事：
//  1. 用统一的 handlerFn / routeLevel 注册全部 REST 路由（NewApp），把身份校验从业务代码里剥离；
//  2. ServeHTTP / dispatch 组织全局中间件顺序，串联「安全头 → 请求体限额 → 节点作用域 →
//     Agent 代理 → 远端能力门控 → 业务路由 + 鉴权」；
//  3. 提供 JSON 读写、错误响应、SPA 静态托管等横切能力。
//
// 中间件顺序本身就是安全边界（详见 dispatch）：
// Agent 代理必须先于 remote_cap 执行，否则 local 类接口在主面板就被 403，永远到不了子节点。
//
// 约定与易踩坑：
//   - 同源部署，不开放 CORS，不要在 ServeHTTP 里补 Access-Control-* 头；
//   - 不向客户端回显内部错误细节（见 writeErr），异常只写服务端日志；
//   - WS 路由（wsRoute）不挂全局鉴权，由处理器内部用 ?token= 自行鉴权（浏览器无法带 Bearer 头）；
//   - 新增业务模块必须在 NewApp 里注册，否则请求会落到 404 JSON / SPA 回退。

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"mime"
	"net"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// handlerFn 是统一业务处理器签名：返回 *HTTPError 时转换为对应状态码响应。
type handlerFn func(http.ResponseWriter, *http.Request) error

// ctxKeyUser 是请求上下文中保存「已鉴权用户」的私有 key。
// 用空结构体而非字符串，避免与其他包写入的 context key 冲突。
type ctxKeyUser struct{}

// App 保存路由表与全局依赖。
// mux 为 Go 1.22+ ServeMux（支持 "GET /api/xxx/{id}" 模式）；
// routes 记录每条路由的鉴权级别，供 Agent 代理前置鉴权（proxyPermForPath）复用。
type App struct {
	cfg    *Config
	mux    *http.ServeMux
	routes []RouteInfo
}

// RouteInfo 记录已注册路由的鉴权级别（供 Agent 代理前置鉴权映射）。
// 请求经 Agent 转发到子节点前，主面板会用 Modules/Admin 在本机做一次等价鉴权，
// 防止受限管理员借代理绕过模块权限；因此新路由只要挂对 perm(...) 就自动生效。
// Public/Custom 用于放行「子节点自行鉴权」的端点（公开资源、插件令牌、Agent 密钥），
// 否则这些不带 Bearer 的请求会在主面板被 401，永远到不了子节点。
type RouteInfo struct {
	Method  string
	Pattern string
	Modules []string
	Admin   bool
	Public  bool
	Custom  bool
}

// NewApp 构建应用并注册全部路由。
// 这是唯一的路由注册入口：新增业务模块必须在这里补一行 registerXxxRoutes。
// registerStatic 放在最后，让业务路由优先于静态文件匹配。
func NewApp() *App {
	a := &App{cfg: GetConfig(), mux: http.NewServeMux()}
	registerAuthRoutes(a)
	registerSystemRoutes(a)
	registerNotesRoutes(a)
	registerUIRoutes(a)
	registerCoreRoutes(a)
	registerDockerRoutes(a)
	registerFileRoutes(a)
	registerProcessRoutes(a)
	registerDiskRoutes(a)
	registerTerminalRoutes(a)
	registerDBSRoutes(a)
	registerCronRoutes(a)
	registerLogsRoutes(a)
	registerFirewallRoutes(a)
	registerSiteRoutes(a)
	registerSiteExtraRoutes(a)
	registerSecurityRoutes(a)
	registerAppStoreRoutes(a)
	registerBackupRoutes(a)
	registerNotifyRoutes(a)
	registerNodeRoutes(a)
	registerNetworkRoutes(a)
	registerMiscRoutes(a)
	a.registerStatic()
	return a
}

// routeLevel 描述一条路由的鉴权要求。
// 级别语义：
//   - public：完全公开（登录、健康检查、OAuth 回调等）；
//   - loginOnly：只验登录，默认密码账号也可访问（改密/2FA/会话管理等自救路径）；
//   - protected：登录 + 已修改默认密码（只读信息类接口）；
//   - admin：完整管理员（面板自身安全边界：用户管理/节点/插件/更新等）；
//   - perm：模块级权限，完整管理员恒通过，受限管理员需命中 perms 白名单中的任一模块；
//   - custom：处理器内部自行鉴权（WS ?token=、Agent 成对密钥、插件令牌、webhook 验签），不挂全局依赖。
type routeLevel struct {
	public    bool
	loginOnly bool // 仅需登录（默认密码账号也可访问，如改密/2FA/会话管理）
	protected bool // 登录 + 非默认密码
	admin     bool // 完整管理员
	perm      []string
	custom    bool // 端点内部自行鉴权
}

// public 声明完全公开的路由（登录、健康检查、OAuth 回调、图标等）。
func public() routeLevel { return routeLevel{public: true} }

// loginOnly 声明只需登录的路由，不要求已修改默认密码（保证首次登录能改密）。
func loginOnly() routeLevel { return routeLevel{loginOnly: true} }

// protected 声明「登录且已修改默认密码」即可访问的路由。
func protected() routeLevel { return routeLevel{protected: true} }

// adminOnly 声明仅完整管理员可访问的路由（面板自身安全边界）。
func adminOnly() routeLevel { return routeLevel{admin: true} }

// perm 声明模块级权限路由；modules 取 auth.go 的 MODULES，命中任一即通过。
func perm(modules ...string) routeLevel { return routeLevel{perm: modules} }

// customAuth 表示鉴权由处理器内部完成，route() 不会做任何身份判断。
// 注意：该标记仅用于语义说明，分发逻辑并不读取 custom 字段。
func customAuth() routeLevel { return routeLevel{custom: true} }

// Get 注册 GET 路由；pattern 支持 ServeMux 的 {name} 通配符。
func (a *App) Get(pattern string, lv routeLevel, fn handlerFn) { a.route("GET", pattern, lv, fn) }

// Post 注册 POST 路由。
func (a *App) Post(pattern string, lv routeLevel, fn handlerFn) { a.route("POST", pattern, lv, fn) }

// Put 注册 PUT 路由。
func (a *App) Put(pattern string, lv routeLevel, fn handlerFn) { a.route("PUT", pattern, lv, fn) }

// Patch 注册 PATCH 路由。
func (a *App) Patch(pattern string, lv routeLevel, fn handlerFn) { a.route("PATCH", pattern, lv, fn) }

// Delete 注册 DELETE 路由。
func (a *App) Delete(pattern string, lv routeLevel, fn handlerFn) { a.route("DELETE", pattern, lv, fn) }

// route 是统一的注册实现：把身份校验前移到分发之前，业务 handler 只负责业务错误。
// 鉴权通过后把 User 写入请求 context，处理器用 CurrentUser(r) 取用。
func (a *App) route(method, pattern string, lv routeLevel, fn handlerFn) {
	// 先登记路由元信息：Agent 代理的前置鉴权依赖它做模块权限映射。
	a.routes = append(a.routes, RouteInfo{
		Method: method, Pattern: pattern, Modules: lv.perm, Admin: lv.admin,
		Public: lv.public, Custom: lv.custom,
	})
	a.mux.HandleFunc(method+" "+pattern, func(w http.ResponseWriter, r *http.Request) {
		// 只有声明了鉴权级别的路由才在这里校验；custom 路由完全不进此分支。
		if lv.protected || lv.loginOnly || lv.admin || len(lv.perm) > 0 {
			// protected/admin/perm 要求已改默认密码；loginOnly 放行默认密码账号（否则无法改密）。
			u, err := Authenticate(r, lv.protected || lv.admin || len(lv.perm) > 0)
			if err != nil {
				writeErr(w, err)
				return
			}
			// 完整管理员判定：admin 路由不看 perms 白名单。
			if lv.admin && u.Role() != "admin" {
				writeErr(w, NewHTTPError(403, "需要管理员权限"))
				return
			}
			// 模块权限判定：完整管理员（perms 为空）在 HasPerm 中恒通过。
			if len(lv.perm) > 0 && !HasPerm(u, lv.perm...) {
				writeErr(w, NewHTTPError(403, "需要 "+strings.Join(lv.perm, "/")+" 模块权限"))
				return
			}
			// 注入已鉴权用户，供 CurrentUser 与审计日志使用。
			r = r.WithContext(context.WithValue(r.Context(), ctxKeyUser{}, u))
		}
		// 业务错误统一映射为 JSON，避免每个 handler 重复写响应。
		if err := fn(w, r); err != nil {
			writeErr(w, err)
		}
	})
}

// wsRoute 注册 WebSocket 路由（鉴权在处理器内部完成）。
// 浏览器 WebSocket API 无法设置 Authorization 头，所以这里不能挂 Bearer 鉴权；
// 处理器必须自行校验 ?token=（见 AuthenticateWS），并按需周期性复检会话。
// 登记 routes 只是为了让代理映射知道该路径存在（无鉴权元信息，Modules 为空）。
func (a *App) wsRoute(pattern string, fn http.HandlerFunc) {
	a.routes = append(a.routes, RouteInfo{Method: "GET", Pattern: pattern})
	a.mux.HandleFunc("GET "+pattern, fn)
}

// writeErr 把业务错误映射为 JSON 响应。
// *HTTPError 使用其自带状态码与 detail；其他错误一律 500 且只回通用文案，
// 内部错误（可能含路径、命令输出、凭据片段）仅写服务端日志，不回显给客户端。
func writeErr(w http.ResponseWriter, err error) {
	var he *HTTPError
	if errors.As(err, &he) {
		WriteJSON(w, he.Status, map[string]any{"detail": he.Detail})
		return
	}
	// 不向客户端回显内部异常细节（可能含路径/凭据）。
	log.Printf("graw: handler error: %v", err)
	WriteJSON(w, 500, map[string]any{"detail": "服务器内部错误"})
}

// WriteJSON 输出 JSON 响应（返回 nil 便于 handler 直接 return）。
// 统一 UTF-8 与不转义 HTML：中文/路径原样输出，避免前端拿到 \u003c 之类的转义串。
func WriteJSON(w http.ResponseWriter, status int, v any) error {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(v)
	return nil
}

// DecodeJSON 解析请求体 JSON（数字保留为 json.Number，避免大整数精度丢失）。
// 空 body 返回 422、格式错误返回 400，调用方可直接 return 该错误。
func DecodeJSON(r *http.Request, v any) error {
	dec := json.NewDecoder(r.Body)
	// UseNumber：端口/时间戳等大整数若走 float64 会丢精度，后续回写会错。
	dec.UseNumber()
	if err := dec.Decode(v); err != nil {
		if errors.Is(err, io.EOF) {
			return NewHTTPError(422, "请求体不能为空")
		}
		return NewHTTPError(400, "请求参数格式错误")
	}
	return nil
}

// PathParam 返回路由通配参数（Go 1.22 ServeMux 的 {name} / {name...}）。
func PathParam(r *http.Request, name string) string {
	return r.PathValue(name)
}

// CurrentUser 返回鉴权中间件写入的当前用户。
// 仅在非 custom 路由中可靠；类型断言失败时返回 nil，调用方需容忍空值。
func CurrentUser(r *http.Request) User {
	u, _ := r.Context().Value(ctxKeyUser{}).(User)
	return u
}

// ServeHTTP 应用全局中间件链并分发路由。
// 顺序：路径规范化 → 安全响应头 → 请求体限额 → 节点作用域选择 → dispatch。
func (a *App) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// 路径规范化：/api/xx/ 与 /api/xx 等价（兼容 FastAPI 尾斜杠路由）。
	if strings.HasPrefix(r.URL.Path, "/api/") && len(r.URL.Path) > 5 && strings.HasSuffix(r.URL.Path, "/") {
		r.URL.Path = strings.TrimRight(r.URL.Path, "/")
	}

	// 包一层 statusWriter：记录状态码，同时保留 Flush/Hijack 以便 WS 升级。
	start := time.Now()
	logging := &statusWriter{ResponseWriter: w, status: 200}
	// 访问日志：只记路径不记查询串（WS 令牌在 ?token= 里，不能落日志）。
	defer func() {
		log.Printf("graw: %s %s %d %s %s", r.Method, r.URL.Path, logging.status,
			time.Since(start).Round(time.Millisecond), ClientIP(r))
	}()
	// 安全响应头（对应 Python 的 security_headers 中间件）。
	// CSP 已放行 inline/eval（前端构建产物内联脚本与 xterm 需要），
	// 改动前端注入新 CDN 时必须同步放行，否则会被浏览器直接拦截。
	logging.Header().Set("Content-Security-Policy",
		"default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "+
			"style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "+
			"font-src 'self' data:; connect-src 'self' ws: wss:; frame-ancestors 'none'; "+
			"base-uri 'self'; form-action 'self'")
	logging.Header().Set("X-Content-Type-Options", "nosniff")
	logging.Header().Set("X-Frame-Options", "DENY")
	logging.Header().Set("Referrer-Policy", "same-origin")

	// 请求体限额前置：未认证的大 body 在进业务前就被拒（411/413/400）。
	if !checkBodyLimit(logging, r) {
		return
	}
	// X-Graw-Node 允许单个请求临时切换管理节点（统一面板按窗口聚焦节点下发）。
	headerNode := strings.TrimSpace(r.Header.Get("X-Graw-Node"))
	wsUpgrade := strings.EqualFold(r.Header.Get("Upgrade"), "websocket")
	// 请求体读取超时：只对带 body 的普通请求生效（WS 无 body，不设截止时间）。
	// 处理结束后清空，避免 keep-alive 复用的下一请求继承旧 deadline。
	if !wsUpgrade && r.ContentLength > 0 {
		rc := http.NewResponseController(logging)
		if err := rc.SetReadDeadline(time.Now().Add(bodyReadTimeout(r))); err == nil {
			defer func() { _ = rc.SetReadDeadline(time.Time{}) }()
		}
	}
	if !wsUpgrade && headerNode != "" && headerNode != CurrentNodeID() {
		store := loadNodeStore()
		n := store.Nodes[headerNode]
		// 裸节点（本地或未配 Agent 的 SSH）：请求期间临时切换宿主操作目标节点，
		// 等价于 Python 的 node_manager 请求级 contextvars。
		// 已配 Agent 的 SSH 节点不在此切换：请求会在 dispatch 里被整体代理走。
		if n != nil && !(str(n["type"]) == "ssh" && agentReady(n)) {
			withNodeScope(headerNode, func() { a.dispatch(logging, r) })
			return
		}
	}
	if wsUpgrade {
		// WS 不做节点切换（长连接期间会与后续请求并发读写上下文）；跨节点订阅由处理器自行处理。
		a.dispatch(logging, r)
		return
	}
	// 默认路径：以「节点选择持久化值」为宿主操作目标（读锁允许并发请求）。
	readNodeScope(func() { a.dispatch(logging, r) })
}

// dispatch 执行代理、本地能力门控与业务路由分发。
// 顺序不可调换：Agent 代理在前，remote_cap 门控在后（理由见下）。
func (a *App) dispatch(w http.ResponseWriter, r *http.Request) {
	// 子节点 Agent 代理（当前节点为已配置 agent 的远端时转发业务请求）。
	// 必须先于 remote_cap 执行，否则 local 类接口会在主面板被 403 拦截，
	// 无法代理到子节点本机执行。
	if a.maybeAgentProxy(w, r) {
		return
	}
	// 远端子节点能力门控（local 类接口在裸 SSH 节点下禁用）。
	// 这是纵深防御：已配 Agent 的远端已在上一层被代理走，不会到这里。
	if RejectIfLocalRemote(r.URL.Path) {
		WriteJSON(w, 403, map[string]any{"detail": LocalOnlyRejectReason()})
		return
	}
	// 业务路由 + 路由级鉴权（route() 内完成）。
	a.mux.ServeHTTP(w, r)
}

// statusWriter 记录响应状态码，并把 Flush/Hijack 显式透传给底层 ResponseWriter。
// 嵌入接口不会自动继承可选接口，因此 WebSocket 升级所需的 Hijacker 必须手写。
// status 供 ServeHTTP 末尾的访问日志使用。
type statusWriter struct {
	http.ResponseWriter
	status int
}

// Unwrap 暴露底层 ResponseWriter：http.ResponseController 依赖它设置读写超时。
func (s *statusWriter) Unwrap() http.ResponseWriter { return s.ResponseWriter }

// WriteHeader 记录首次状态码后转交底层实现。
func (s *statusWriter) WriteHeader(code int) {
	s.status = code
	s.ResponseWriter.WriteHeader(code)
}

// Flush 透传流式刷新（SSE/大文件下载/WS 代理需要）。
func (s *statusWriter) Flush() {
	if f, ok := s.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

// Hijack 透传底层连接（WebSocket 升级需要）。
func (s *statusWriter) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	h, ok := s.ResponseWriter.(http.Hijacker)
	if !ok {
		return nil, nil, fmt.Errorf("response writer does not support hijacking")
	}
	return h.Hijack()
}

// bodyReadTimeout 按请求体大小计算读取截止时间：基础 5 分钟 + 每 MB 1 秒，封顶 60 分钟。
// 让慢速 body 攻击在有限时间内被断开，同时给慢速网络下的大文件上传留出足够余量。
func bodyReadTimeout(r *http.Request) time.Duration {
	size := r.ContentLength
	if size < 0 {
		size = 0
	}
	d := 5*time.Minute + time.Duration(size/(1<<20))*time.Second
	if d > time.Hour {
		d = time.Hour
	}
	return d
}

// checkBodyLimit 全局请求体限额：Transfer-Encoding 411、超限 413、
// 多个不一致 Content-Length 400（与 Python 版中间件一致）。
// 返回 false 表示已写出错误响应，调用方必须直接结束请求。
// 限额分两档：普通 JSON 16MB（GRAW_MAX_BODY_MB）、multipart 上传 2GB（GRAW_MAX_UPLOAD_MB）。
func checkBodyLimit(w http.ResponseWriter, r *http.Request) bool {
	// 拒绝 chunked：无法在读取前判断大小，也避免绕过 Content-Length 限额。
	if len(r.TransferEncoding) > 0 {
		WriteJSON(w, 411, map[string]any{"detail": "请求必须声明 Content-Length（不支持 chunked 传输）"})
		return false
	}
	values := r.Header.Values("Content-Length")
	// 未声明长度的请求交给下游读取逻辑处理；GET/HEAD 也一并校验，
	// 避免带超大 body 的非标准请求绕过全局限额（正常 GET 不会带 body，不受影响）。
	if len(values) == 0 {
		return true
	}
	// 多个 Content-Length 且取值不一致：可能是请求走私探测，直接 400。
	uniq := map[string]bool{}
	for _, v := range values {
		uniq[strings.TrimSpace(v)] = true
	}
	if len(uniq) > 1 {
		WriteJSON(w, 400, map[string]any{"detail": "Content-Length 头不一致"})
		return false
	}
	cl, err := strconv.ParseInt(values[0], 10, 64)
	if err != nil || cl < 0 {
		WriteJSON(w, 400, map[string]any{"detail": "Content-Length 非法"})
		return false
	}
	// multipart 上传走较大的第二档限额；其余（JSON/表单）走普通限额。
	limit := GetConfig().MaxBodyBytes
	if strings.HasPrefix(strings.ToLower(r.Header.Get("Content-Type")), "multipart/") {
		limit = GetConfig().MaxUploadByte
	}
	if cl > limit {
		WriteJSON(w, 413, map[string]any{"detail": fmt.Sprintf("请求体过大（上限 %d MB）", limit/(1024*1024))})
		return false
	}
	return true
}

// registerStatic 托管前端构建产物并实现 SPA 回退。
// 未配置 FrontendDist 或缺少 index.html 时静默跳过（纯 API 部署）。
// 路径拼接后必须校验仍位于 dist 内，防止 ../ 穿越读取任意文件。
func (a *App) registerStatic() {
	dist := a.cfg.FrontendDist
	if dist == "" {
		return
	}
	index := filepath.Join(dist, "index.html")
	if !Exists(index) {
		return
	}
	// /assets/ 走静态文件，带路径穿越校验；不存在的资源直接 404，不回退 index.html。
	a.mux.HandleFunc("/assets/", func(w http.ResponseWriter, r *http.Request) {
		rel := strings.TrimPrefix(path.Clean(r.URL.Path), "/")
		// cleanHostView 既做路径归一化，也是 CodeQL 认可的路径穿越净化点。
		full := cleanHostView(filepath.Join(dist, filepath.FromSlash(rel)))
		if !strings.HasPrefix(full, dist+string(os.PathSeparator)) && full != dist {
			http.NotFound(w, r)
			return
		}
		if st, err := os.Stat(full); err != nil || st.IsDir() {
			http.NotFound(w, r)
			return
		}
		// 显式设置 Content-Type，避免 ServeFile 依赖扩展名推断失败导致浏览器误判。
		if ct := mime.TypeByExtension(filepath.Ext(full)); ct != "" {
			w.Header().Set("Content-Type", ct)
		}
		http.ServeFile(w, r, full)
	})
	// 根路径：/api/* 未命中业务路由时返回 JSON 404（而不是 index.html）；
	// 其余路径优先匹配 dist 内真实文件，否则回退 index.html 交给前端路由。
	a.mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/api/") {
			WriteJSON(w, 404, map[string]any{"detail": "Not Found"})
			return
		}
		if r.URL.Path != "/" {
			rel := path.Clean(strings.TrimPrefix(r.URL.Path, "/"))
			full := cleanHostView(filepath.Join(dist, filepath.FromSlash(rel)))
			if strings.HasPrefix(full, dist+string(os.PathSeparator)) && Exists(full) && !IsDir(full) {
				http.ServeFile(w, r, full)
				return
			}
		}
		// SPA 回退：前端路由（如 /sites、/docker）由 Vue 自行解析。
		http.ServeFile(w, r, index)
	})
}
