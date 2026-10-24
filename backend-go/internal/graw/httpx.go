package graw

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
)

// handlerFn 是统一业务处理器签名：返回 *HTTPError 时转换为对应状态码响应。
type handlerFn func(http.ResponseWriter, *http.Request) error

type ctxKeyUser struct{}

// App 保存路由表与全局依赖。
type App struct {
	cfg    *Config
	mux    *http.ServeMux
	routes []RouteInfo
}

// RouteInfo 记录已注册路由的鉴权级别（供 Agent 代理前置鉴权映射）。
type RouteInfo struct {
	Method  string
	Pattern string
	Modules []string
	Admin   bool
}

// NewApp 构建应用并注册全部路由。
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

// routeLevel 描述鉴权要求。
type routeLevel struct {
	public    bool
	loginOnly bool // 仅需登录（默认密码账号也可访问，如改密/2FA/会话管理）
	protected bool // 登录 + 非默认密码
	admin     bool // 完整管理员
	perm      []string
	custom    bool // 端点内部自行鉴权
}

func public() routeLevel                { return routeLevel{public: true} }
func loginOnly() routeLevel             { return routeLevel{loginOnly: true} }
func protected() routeLevel             { return routeLevel{protected: true} }
func adminOnly() routeLevel             { return routeLevel{admin: true} }
func perm(modules ...string) routeLevel { return routeLevel{perm: modules} }
func customAuth() routeLevel            { return routeLevel{custom: true} }

// Get/Post/Put/Delete 注册路由。
func (a *App) Get(pattern string, lv routeLevel, fn handlerFn)    { a.route("GET", pattern, lv, fn) }
func (a *App) Post(pattern string, lv routeLevel, fn handlerFn)   { a.route("POST", pattern, lv, fn) }
func (a *App) Put(pattern string, lv routeLevel, fn handlerFn)    { a.route("PUT", pattern, lv, fn) }
func (a *App) Patch(pattern string, lv routeLevel, fn handlerFn)  { a.route("PATCH", pattern, lv, fn) }
func (a *App) Delete(pattern string, lv routeLevel, fn handlerFn) { a.route("DELETE", pattern, lv, fn) }

func (a *App) route(method, pattern string, lv routeLevel, fn handlerFn) {
	a.routes = append(a.routes, RouteInfo{
		Method: method, Pattern: pattern, Modules: lv.perm, Admin: lv.admin,
	})
	a.mux.HandleFunc(method+" "+pattern, func(w http.ResponseWriter, r *http.Request) {
		if lv.protected || lv.loginOnly || lv.admin || len(lv.perm) > 0 {
			u, err := Authenticate(r, lv.protected || lv.admin || len(lv.perm) > 0)
			if err != nil {
				writeErr(w, err)
				return
			}
			if lv.admin && u.Role() != "admin" {
				writeErr(w, NewHTTPError(403, "需要管理员权限"))
				return
			}
			if len(lv.perm) > 0 && !HasPerm(u, lv.perm...) {
				writeErr(w, NewHTTPError(403, "需要 "+strings.Join(lv.perm, "/")+" 模块权限"))
				return
			}
			r = r.WithContext(context.WithValue(r.Context(), ctxKeyUser{}, u))
		}
		if err := fn(w, r); err != nil {
			writeErr(w, err)
		}
	})
}

// wsRoute 注册 WebSocket 路由（鉴权在处理器内部完成）。
func (a *App) wsRoute(pattern string, fn http.HandlerFunc) {
	a.routes = append(a.routes, RouteInfo{Method: "GET", Pattern: pattern})
	a.mux.HandleFunc("GET "+pattern, fn)
}

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
func WriteJSON(w http.ResponseWriter, status int, v any) error {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(v)
	return nil
}

// DecodeJSON 解析请求体 JSON（数字保留为 json.Number，避免大整数精度丢失）。
func DecodeJSON(r *http.Request, v any) error {
	dec := json.NewDecoder(r.Body)
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
func CurrentUser(r *http.Request) User {
	u, _ := r.Context().Value(ctxKeyUser{}).(User)
	return u
}

// ServeHTTP 应用全局中间件链并分发路由。
func (a *App) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// 路径规范化：/api/xx/ 与 /api/xx 等价（兼容 FastAPI 尾斜杠路由）。
	if strings.HasPrefix(r.URL.Path, "/api/") && len(r.URL.Path) > 5 && strings.HasSuffix(r.URL.Path, "/") {
		r.URL.Path = strings.TrimRight(r.URL.Path, "/")
	}

	logging := &statusWriter{ResponseWriter: w, status: 200}
	// 安全响应头（对应 Python 的 security_headers 中间件）。
	logging.Header().Set("Content-Security-Policy",
		"default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "+
			"style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "+
			"font-src 'self' data:; connect-src 'self' ws: wss:; frame-ancestors 'none'; "+
			"base-uri 'self'; form-action 'self'")
	logging.Header().Set("X-Content-Type-Options", "nosniff")
	logging.Header().Set("X-Frame-Options", "DENY")
	logging.Header().Set("Referrer-Policy", "same-origin")

	if !checkBodyLimit(logging, r) {
		return
	}
	headerNode := strings.TrimSpace(r.Header.Get("X-Graw-Node"))
	wsUpgrade := strings.EqualFold(r.Header.Get("Upgrade"), "websocket")
	if !wsUpgrade && headerNode != "" && headerNode != CurrentNodeID() {
		store := loadNodeStore()
		n := store.Nodes[headerNode]
		// 裸节点（本地或未配 Agent 的 SSH）：请求期间临时切换宿主操作目标节点，
		// 等价于 Python 的 node_manager 请求级 contextvars。
		if n != nil && !(str(n["type"]) == "ssh" && agentReady(n)) {
			withNodeScope(headerNode, func() { a.dispatch(logging, r) })
			return
		}
	}
	if wsUpgrade {
		a.dispatch(logging, r)
		return
	}
	readNodeScope(func() { a.dispatch(logging, r) })
}

// dispatch 执行代理、本地能力门控与业务路由分发。
func (a *App) dispatch(w http.ResponseWriter, r *http.Request) {
	// 子节点 Agent 代理（当前节点为已配置 agent 的远端时转发业务请求）。
	// 必须先于 remote_cap 执行，否则 local 类接口会在主面板被 403 拦截，
	// 无法代理到子节点本机执行。
	if a.maybeAgentProxy(w, r) {
		return
	}
	// 远端子节点能力门控（local 类接口在裸 SSH 节点下禁用）。
	if RejectIfLocalRemote(r.URL.Path) {
		WriteJSON(w, 403, map[string]any{"detail": LocalOnlyRejectReason()})
		return
	}
	a.mux.ServeHTTP(w, r)
}

type statusWriter struct {
	http.ResponseWriter
	status int
}

func (s *statusWriter) WriteHeader(code int) {
	s.status = code
	s.ResponseWriter.WriteHeader(code)
}

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

// checkBodyLimit 全局请求体限额：Transfer-Encoding 411、超限 413、
// 多个不一致 Content-Length 400（与 Python 版中间件一致）。
func checkBodyLimit(w http.ResponseWriter, r *http.Request) bool {
	if len(r.TransferEncoding) > 0 {
		WriteJSON(w, 411, map[string]any{"detail": "请求必须声明 Content-Length（不支持 chunked 传输）"})
		return false
	}
	values := r.Header.Values("Content-Length")
	if len(values) == 0 || r.Method == http.MethodGet || r.Method == http.MethodHead {
		return true
	}
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
func (a *App) registerStatic() {
	dist := a.cfg.FrontendDist
	if dist == "" {
		return
	}
	index := filepath.Join(dist, "index.html")
	if !Exists(index) {
		return
	}
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
		if ct := mime.TypeByExtension(filepath.Ext(full)); ct != "" {
			w.Header().Set("Content-Type", ct)
		}
		http.ServeFile(w, r, full)
	})
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
		http.ServeFile(w, r, index)
	})
}
