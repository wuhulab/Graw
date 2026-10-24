package graw

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"
)

// 内网映射 / Frp 内网穿透 / VPN 转发策略 / 站点 Git 自动部署。

// --- 内网映射 ---

func loadNetmap() map[string]any {
	data := map[string]any{"version": 1, "items": []any{}}
	_ = ReadJSON(DataPath("netmap.json"), &data)
	if _, ok := data["items"]; !ok {
		data["items"] = []any{}
	}
	return data
}

func netmapItems(data map[string]any) []map[string]any {
	out := []map[string]any{}
	if arr, ok := data["items"].([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok {
				out = append(out, m)
			}
		}
	}
	return out
}

func validateNetmapURL(raw string) (string, *HTTPError) {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		return "", NewHTTPError(400, "目标地址必须为 http(s)://host[:port][/path]")
	}
	return u.String(), nil
}

func findNetmapItem(id string) map[string]any {
	for _, item := range netmapItems(loadNetmap()) {
		if str(item["id"]) == id {
			return item
		}
	}
	return nil
}

func registerNetmapRoutes(a *App) {
	a.Get("/api/netmap", perm("netmap"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"items": netmapItems(loadNetmap())})
	})
	a.Post("/api/netmap", perm("netmap"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Name string `json:"name"`
			URL  string `json:"url"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		target, err := validateNetmapURL(req.URL)
		if err != nil {
			return err
		}
		item := map[string]any{
			"id": randomHex(10), "name": defaultString(strings.TrimSpace(req.Name), target),
			"url": target, "created_at": time.Now().Unix(),
		}
		data := loadNetmap()
		data["items"] = append(netmapItems(data), item)
		if err := WriteJSONFile(DataPath("netmap.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "item": item})
	})
	a.Put("/api/netmap/{iid}", perm("netmap"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "iid")
		var req struct {
			Name string `json:"name"`
			URL  string `json:"url"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		target, err := validateNetmapURL(req.URL)
		if err != nil {
			return err
		}
		data := loadNetmap()
		items := netmapItems(data)
		var item map[string]any
		for _, it := range items {
			if str(it["id"]) == id {
				it["name"] = defaultString(strings.TrimSpace(req.Name), target)
				it["url"] = target
				item = it
			}
		}
		if item == nil {
			return NewHTTPError(404, "映射条目不存在")
		}
		data["items"] = items
		if err := WriteJSONFile(DataPath("netmap.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "item": item})
	})
	a.Delete("/api/netmap/{iid}", perm("netmap"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "iid")
		data := loadNetmap()
		items := netmapItems(data)
		kept := []map[string]any{}
		for _, it := range items {
			if str(it["id"]) != id {
				kept = append(kept, it)
			}
		}
		if len(kept) == len(items) {
			return NewHTTPError(404, "映射条目不存在")
		}
		data["items"] = kept
		if err := WriteJSONFile(DataPath("netmap.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Get("/api/netmap/proxy/{item_id}", customAuth(), netmapProxyHandler)
	a.Get("/api/netmap/proxy/{item_id}/{sub_path...}", customAuth(), netmapProxyHandler)
	a.Post("/api/netmap/proxy/{item_id}", customAuth(), netmapProxyHandler)
	a.Post("/api/netmap/proxy/{item_id}/{sub_path...}", customAuth(), netmapProxyHandler)
	a.Put("/api/netmap/proxy/{item_id}", customAuth(), netmapProxyHandler)
	a.Put("/api/netmap/proxy/{item_id}/{sub_path...}", customAuth(), netmapProxyHandler)
	a.Delete("/api/netmap/proxy/{item_id}", customAuth(), netmapProxyHandler)
	a.Delete("/api/netmap/proxy/{item_id}/{sub_path...}", customAuth(), netmapProxyHandler)
	a.Patch("/api/netmap/proxy/{item_id}", customAuth(), netmapProxyHandler)
	a.Patch("/api/netmap/proxy/{item_id}/{sub_path...}", customAuth(), netmapProxyHandler)
}

func netmapProxyHandler(w http.ResponseWriter, r *http.Request) error {
	// iframe 场景无法携带 Authorization 头：允许 ?__graw_token= 查询参数。
	u, authErr := Authenticate(r, true)
	if authErr != nil {
		token := r.URL.Query().Get("__graw_token")
		if token == "" {
			return WriteJSON(w, 401, map[string]any{"detail": "未登录或令牌无效"})
		}
		claims := DecodeToken(token)
		if claims == nil {
			return WriteJSON(w, 401, map[string]any{"detail": "未登录或令牌无效"})
		}
		full, err := GetUser(claims.Subject)
		if err != nil || full == nil || !VerifyTokenVersion(claims) || !SessionActive(claims.Sid) ||
			IsDefaultPassword(full.PasswordHash()) {
			return WriteJSON(w, 401, map[string]any{"detail": "未登录或令牌无效"})
		}
		u = full
	}
	if !HasPerm(u, "netmap") {
		return WriteJSON(w, 403, map[string]any{"detail": "需要 netmap 模块权限"})
	}
	item := findNetmapItem(PathParam(r, "item_id"))
	if item == nil {
		return WriteJSON(w, 404, map[string]any{"detail": "映射条目不存在"})
	}
	base, err := url.Parse(str(item["url"]))
	if err != nil {
		return WriteJSON(w, 502, map[string]any{"detail": "目标地址非法"})
	}
	sub := PathParam(r, "sub_path")
	base.Path = strings.TrimRight(base.Path, "/") + "/" + strings.TrimLeft(sub, "/")
	base.RawQuery = r.URL.RawQuery
	var body io.Reader
	if r.Body != nil && r.Method != http.MethodGet && r.Method != http.MethodHead {
		data, _ := io.ReadAll(io.LimitReader(r.Body, 16<<20))
		body = bytes.NewReader(data)
	}
	req, err := http.NewRequest(r.Method, base.String(), body)
	if err != nil {
		return WriteJSON(w, 502, map[string]any{"detail": "构造代理请求失败"})
	}
	for _, k := range []string{"Content-Type", "Accept", "Accept-Language", "User-Agent"} {
		if v := r.Header.Get(k); v != "" {
			req.Header.Set(k, v)
		}
	}
	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return WriteJSON(w, 502, map[string]any{"detail": "目标服务不可达"})
	}
	defer resp.Body.Close()
	for k, vals := range resp.Header {
		low := strings.ToLower(k)
		if low == "content-length" || low == "transfer-encoding" || low == "connection" {
			continue
		}
		for _, v := range vals {
			w.Header().Add(k, v)
		}
	}
	w.WriteHeader(resp.StatusCode)
	_, _ = io.Copy(w, resp.Body)
	return nil
}

// --- Frp ---

func defaultFRP() map[string]any {
	return map[string]any{
		"mode": "server", "serverBin": "frps", "clientBin": "frpc",
		"server": map[string]any{
			"configPath": "", "bindAddr": "0.0.0.0", "bindPort": 7000, "token": "",
			"dashboardAddr": "127.0.0.1", "dashboardPort": 0,
			"dashboardUser": "admin", "dashboardPwd": "", "logLevel": "info",
		},
		"client": map[string]any{
			"serverAddr": "", "serverPort": 7000, "token": "", "configPath": "",
			"loginFailExit": true, "logLevel": "info",
		},
		"proxies": []any{},
	}
}

func loadFRP() map[string]any {
	data := defaultFRP()
	var raw map[string]any
	if err := ReadJSON(DataPath("frp.json"), &raw); err == nil && raw != nil {
		for k, v := range raw {
			data[k] = v
		}
	}
	return data
}

func frpConfigPath(data map[string]any) string {
	mode := str(data["mode"])
	if mode == "client" {
		if p := str(mapOf(data["client"])["configPath"]); p != "" {
			return p
		}
		return "/etc/frp/frpc.toml"
	}
	if p := str(mapOf(data["server"])["configPath"]); p != "" {
		return p
	}
	return "/etc/frp/frps.toml"
}

func frpRender(data map[string]any) string {
	mode := str(data["mode"])
	lines := []string{}
	if mode == "client" {
		c := mapOf(data["client"])
		lines = append(lines,
			"serverAddr = "+tomlStr(str(c["serverAddr"]))+"",
			fmt.Sprintf("serverPort = %d", int(int64Of(c["serverPort"]))),
			"loginFailExit = "+boolToml(boolOf(c["loginFailExit"])),
			"log.level = "+tomlStr(defaultString(str(c["logLevel"]), "info")))
		if tok := str(c["token"]); tok != "" {
			lines = append(lines, "auth.token = "+tomlStr(tok))
		}
		for _, p := range frpItems(data, "proxies") {
			if !boolOf(p["enabled"]) {
				continue
			}
			lines = append(lines, "", "[[proxies]]")
			lines = append(lines, "name = "+tomlStr(str(p["name"])))
			lines = append(lines, "type = "+tomlStr(defaultString(str(p["type"]), "tcp")))
			lines = append(lines, "localIP = "+tomlStr(defaultString(str(p["localIp"]), "127.0.0.1")))
			lines = append(lines, fmt.Sprintf("localPort = %d", int(int64Of(p["localPort"]))))
			if rp := int(int64Of(p["remotePort"])); rp > 0 {
				lines = append(lines, fmt.Sprintf("remotePort = %d", rp))
			}
			if cd := str(p["customDomains"]); cd != "" {
				lines = append(lines, "customDomains = ["+tomlStr(cd)+"]")
			}
			if boolOf(p["useEncryption"]) {
				lines = append(lines, "transport.useEncryption = true")
			}
			if boolOf(p["useCompression"]) {
				lines = append(lines, "transport.useCompression = true")
			}
		}
	} else {
		s := mapOf(data["server"])
		lines = append(lines,
			"bindAddr = "+tomlStr(defaultString(str(s["bindAddr"]), "0.0.0.0")),
			fmt.Sprintf("bindPort = %d", int(int64Of(s["bindPort"]))))
		if tok := str(s["token"]); tok != "" {
			lines = append(lines, "auth.token = "+tomlStr(tok))
		}
		if int(int64Of(s["dashboardPort"])) > 0 {
			lines = append(lines,
				fmt.Sprintf("webServer.addr = %s", tomlStr(str(s["dashboardAddr"]))),
				fmt.Sprintf("webServer.port = %d", int(int64Of(s["dashboardPort"]))),
				"webServer.user = "+tomlStr(str(s["dashboardUser"])),
				"webServer.password = "+tomlStr(str(s["dashboardPwd"])))
		}
		lines = append(lines, "log.level = "+tomlStr(defaultString(str(s["logLevel"]), "info")))
	}
	return strings.Join(lines, "\n") + "\n"
}

func tomlStr(s string) string {
	return `"` + strings.NewReplacer(`\`, `\\`, `"`, `\"`, "\n", "").Replace(s) + `"`
}
func boolToml(b bool) string {
	if b {
		return "true"
	}
	return "false"
}

func frpItems(data map[string]any, key string) []map[string]any {
	out := []map[string]any{}
	if arr, ok := data[key].([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok {
				out = append(out, m)
			}
		}
	}
	return out
}

var (
	frpMu      sync.Mutex
	frpProcess *os.Process
	frpReason  string
)

func frpBinary(data map[string]any) string {
	if str(data["mode"]) == "client" {
		return defaultString(str(data["clientBin"]), "frpc")
	}
	return defaultString(str(data["serverBin"]), "frps")
}

func frpRunning() bool {
	frpMu.Lock()
	defer frpMu.Unlock()
	if frpProcess == nil {
		return false
	}
	if frpProcess.Signal(nil) == nil {
		return true
	}
	frpProcess = nil
	return false
}

func frpStart(data map[string]any) error {
	if frpRunning() {
		return nil
	}
	binName := frpBinary(data)
	bin := binName
	if HostWhich(binName) != "" {
		bin = HostWhich(binName)
	} else if Exists(HostPath(binName)) {
		bin = HostPath(binName)
	} else {
		return NewHTTPError(400, "未检测到 frp 可执行文件: "+binName)
	}
	cfgView := frpConfigPath(data)
	cfg := HostPath(cfgView)
	_ = os.MkdirAll(filepath.Dir(cfg), 0o755)
	if err := os.WriteFile(cfg, []byte(frpRender(data)), 0o600); err != nil {
		return NewHTTPError(500, "写入 frp 配置失败: "+err.Error())
	}
	cmd := exec.Command(bin, "-c", cfg)
	cmd.Stdout = nil
	cmd.Stderr = nil
	if err := cmd.Start(); err != nil {
		return NewHTTPError(500, "启动 frp 失败: "+err.Error())
	}
	frpMu.Lock()
	frpProcess = cmd.Process
	frpReason = "running"
	frpMu.Unlock()
	go func() { _ = cmd.Wait() }()
	return nil
}

func frpStop() {
	frpMu.Lock()
	defer frpMu.Unlock()
	if frpProcess != nil {
		_ = frpProcess.Kill()
		frpProcess = nil
	}
	frpReason = "stopped"
}

func registerFRPRoutes(a *App) {
	a.Get("/api/frp/status", perm("frp"), func(w http.ResponseWriter, r *http.Request) error {
		data := loadFRP()
		platform := "linux"
		if isWindows() {
			platform = "windows"
		}
		bin := frpBinary(data)
		return WriteJSON(w, 200, map[string]any{
			"mode": str(data["mode"]), "installed": HostWhich(bin) != "" || Exists(HostPath(bin)),
			"running": frpRunning(), "configPath": frpConfigPath(data),
			"binPath": bin, "systemdActive": false, "platform": platform,
		})
	})
	a.Get("/api/frp/config", perm("frp"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, loadFRP())
	})
	a.Get("/api/frp/preview", perm("frp"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"toml": frpRender(loadFRP())})
	})
	a.Put("/api/frp/config", perm("frp"), func(w http.ResponseWriter, r *http.Request) error {
		var req map[string]any
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		data := loadFRP()
		mode := str(req["mode"])
		if mode == "client" || mode == "server" {
			data["mode"] = mode
		}
		for _, key := range []string{"serverBin", "clientBin"} {
			if v, ok := req[key].(string); ok && v != "" {
				data[key] = v
			}
		}
		for _, key := range []string{"server", "client"} {
			if m, ok := req[key].(map[string]any); ok {
				cur := mapOf(data[key])
				for k, v := range m {
					if k == "configPath" {
						p := str(v)
						if p != "" && !strings.HasPrefix(p, "/etc/frp") {
							return NewHTTPError(400, "配置文件必须位于 /etc/frp 目录内")
						}
					}
					cur[k] = v
				}
				data[key] = cur
			}
		}
		if v, ok := req["proxies"].([]any); ok {
			data["proxies"] = v
		}
		if err := WriteJSONFile(DataPath("frp.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, data)
	})
	a.Post("/api/frp/mode", perm("frp"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Mode string `json:"mode"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if req.Mode != "server" && req.Mode != "client" {
			return NewHTTPError(400, "mode 仅支持 server/client")
		}
		data := loadFRP()
		data["mode"] = req.Mode
		if err := WriteJSONFile(DataPath("frp.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "mode": req.Mode})
	})
	a.Post("/api/frp/proxies", perm("frp"), func(w http.ResponseWriter, r *http.Request) error {
		var req map[string]any
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if strings.TrimSpace(str(req["name"])) == "" {
			return NewHTTPError(400, "代理名称不能为空")
		}
		req["id"] = "proxy_" + randomHex(8)
		if _, ok := req["enabled"]; !ok {
			req["enabled"] = true
		}
		data := loadFRP()
		data["proxies"] = append(frpItems(data, "proxies"), req)
		if err := WriteJSONFile(DataPath("frp.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, req)
	})
	a.Put("/api/frp/proxies/{proxy_id}", perm("frp"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "proxy_id")
		var req map[string]any
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		data := loadFRP()
		proxies := frpItems(data, "proxies")
		var proxy map[string]any
		for _, p := range proxies {
			if str(p["id"]) == id {
				proxy = p
				for k, v := range req {
					p[k] = v
				}
			}
		}
		if proxy == nil {
			return NewHTTPError(404, "代理不存在")
		}
		data["proxies"] = proxies
		if err := WriteJSONFile(DataPath("frp.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, proxy)
	})
	a.Delete("/api/frp/proxies/{proxy_id}", perm("frp"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "proxy_id")
		data := loadFRP()
		kept := []map[string]any{}
		for _, p := range frpItems(data, "proxies") {
			if str(p["id"]) != id {
				kept = append(kept, p)
			}
		}
		data["proxies"] = kept
		if err := WriteJSONFile(DataPath("frp.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/frp/toggle-proxy/{proxy_id}", perm("frp"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "proxy_id")
		data := loadFRP()
		proxies := frpItems(data, "proxies")
		enabled := false
		for _, p := range proxies {
			if str(p["id"]) == id {
				p["enabled"] = !boolOf(p["enabled"])
				enabled = boolOf(p["enabled"])
			}
		}
		data["proxies"] = proxies
		if err := WriteJSONFile(DataPath("frp.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "enabled": enabled})
	})
	a.Post("/api/frp/start", perm("frp"), func(w http.ResponseWriter, r *http.Request) error {
		if err := frpStart(loadFRP()); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "running": true})
	})
	a.Post("/api/frp/stop", perm("frp"), func(w http.ResponseWriter, r *http.Request) error {
		frpStop()
		return WriteJSON(w, 200, map[string]any{"ok": true, "running": false})
	})
	a.Post("/api/frp/restart", perm("frp"), func(w http.ResponseWriter, r *http.Request) error {
		frpStop()
		if err := frpStart(loadFRP()); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "running": true})
	})
}

// --- VPN 转发策略 ---

func defaultVPN() map[string]any {
	return map[string]any{
		"enabled": false, "portForward": true, "globalProxy": false, "lowPower": true,
		"idleMinutes": 15, "mixedPort": 7890, "controllerPort": 9097, "controllerSecret": "",
		"binPath": "", "nodes": []any{}, "forwards": []any{}, "subToken": "",
	}
}

func loadVPN() map[string]any {
	data := defaultVPN()
	var raw map[string]any
	if err := ReadJSON(DataPath("vpn.json"), &raw); err == nil && raw != nil {
		for k, v := range raw {
			data[k] = v
		}
	}
	return data
}

func vpnItems(data map[string]any, key string) []map[string]any {
	out := []map[string]any{}
	if arr, ok := data[key].([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok {
				out = append(out, m)
			}
		}
	}
	return out
}

func vpnDetectBinary(data map[string]any) string {
	if p := strings.TrimSpace(str(data["binPath"])); p != "" {
		return p
	}
	for _, name := range []string{"mihomo", "clash-meta", "clash"} {
		if p := HostWhich(name); p != "" {
			return p
		}
	}
	return ""
}

func vpnProxyYAML(data map[string]any) string {
	lines := []string{"mixed-port: " + itoa(int(int64Of(data["mixedPort"]))),
		"allow-lan: false", "mode: rule", "log-level: info"}
	nodes := vpnItems(data, "nodes")
	if len(nodes) == 0 {
		lines = append(lines, "proxies: []", "rules:", "  - MATCH,DIRECT")
		return strings.Join(lines, "\n") + "\n"
	}
	lines = append(lines, "proxies:")
	for _, n := range nodes {
		lines = append(lines, "  - name: "+yamlStr(str(n["name"])))
		lines = append(lines, "    type: "+yamlStr(str(n["type"])))
		lines = append(lines, "    server: "+yamlStr(str(n["server"])))
		lines = append(lines, "    port: "+itoa(int(int64Of(n["port"]))))
		for _, key := range []string{"uuid", "password", "cipher", "username", "sni", "flow"} {
			if v := str(n[key]); v != "" {
				lines = append(lines, "    "+key+": "+yamlStr(v))
			}
		}
		if boolOf(n["udp"]) {
			lines = append(lines, "    udp: true")
		}
		if boolOf(n["tls"]) {
			lines = append(lines, "    tls: true")
		}
		if boolOf(n["skipCertVerify"]) {
			lines = append(lines, "    skip-cert-verify: true")
		}
	}
	lines = append(lines, "proxy-groups:", "  - name: PROXY", "    type: select", "    proxies:")
	for _, n := range vpnItems(data, "nodes") {
		lines = append(lines, "      - "+yamlStr(str(n["name"])))
	}
	lines = append(lines, "rules:", "  - MATCH,PROXY")
	return strings.Join(lines, "\n") + "\n"
}

func yamlStr(s string) string {
	return `"` + strings.NewReplacer(`\`, `\\`, `"`, `\"`, "\n", "").Replace(s) + `"`
}

func vpnStatus(data map[string]any) map[string]any {
	bin := vpnDetectBinary(data)
	vpnMu.Lock()
	running := vpnProcess != nil && vpnProcess.Signal(nil) == nil
	autoStop := vpnAutoStop
	startedAt := vpnStartedAt
	vpnMu.Unlock()
	resp := map[string]any{
		"running": running, "enabled": boolOf(data["enabled"]),
		"mixedPort": int(int64Of(data["mixedPort"])), "binary": bin,
		"lowPower": boolOf(data["lowPower"]), "mode": "mihomo", "auto_stopped": autoStop,
	}
	if !startedAt.IsZero() {
		resp["started_at"] = startedAt.Format("2006-01-02 15:04:05")
	}
	return resp
}

var (
	vpnMu        sync.Mutex
	vpnProcess   *os.Process
	vpnStartedAt time.Time
	vpnLastBusy  time.Time
	vpnAutoStop  bool
)

func vpnRunning() bool {
	vpnMu.Lock()
	defer vpnMu.Unlock()
	return vpnProcess != nil && vpnProcess.Signal(nil) == nil
}

func vpnStart(data map[string]any) error {
	vpnMu.Lock()
	if vpnProcess != nil && vpnProcess.Signal(nil) == nil {
		vpnMu.Unlock()
		return nil
	}
	bin := vpnDetectBinary(data)
	if bin == "" {
		vpnMu.Unlock()
		return NewHTTPError(400, "未检测到 mihomo/clash 核心，请在配置中指定 binPath")
	}
	cfgPath := DataPath("vpn", "config.yaml")
	_ = os.MkdirAll(filepath.Dir(cfgPath), 0o700)
	if err := os.WriteFile(cfgPath, []byte(vpnProxyYAML(data)), 0o600); err != nil {
		vpnMu.Unlock()
		return err
	}
	cmd := exec.Command(bin, "-f", cfgPath)
	logPath := DataPath("vpn", "core.log")
	logFile, _ := os.Create(logPath)
	if logFile != nil {
		cmd.Stdout = logFile
		cmd.Stderr = logFile
	}
	if err := cmd.Start(); err != nil {
		vpnMu.Unlock()
		return NewHTTPError(500, "启动 VPN 核心失败: "+err.Error())
	}
	vpnProcess = cmd.Process
	vpnStartedAt = time.Now()
	vpnLastBusy = time.Now()
	vpnAutoStop = false
	vpnMu.Unlock()
	go func() { _ = cmd.Wait() }()
	startVPNWatchdog()
	return nil
}

func vpnStop() {
	vpnMu.Lock()
	if vpnProcess != nil {
		_ = vpnProcess.Kill()
		vpnProcess = nil
	}
	vpnMu.Unlock()
}

var vpnWatchdogOnce sync.Once

// startVPNWatchdog 低占用看门狗：空闲超时自动停核、意外退出后限频自愈、首次请求唤醒。
func startVPNWatchdog() {
	vpnWatchdogOnce.Do(func() {
		go func() {
			for {
				time.Sleep(60 * time.Second)
				func() {
					defer func() { _ = recover() }()
					data := loadVPN()
					if !boolOf(data["enabled"]) || !boolOf(data["lowPower"]) {
						return
					}
					vpnMu.Lock()
					running := vpnProcess != nil && vpnProcess.Signal(nil) == nil
					lastBusy := vpnLastBusy
					idleMinutes := int(int64Of(data["idleMinutes"]))
					vpnMu.Unlock()
					if idleMinutes <= 0 {
						idleMinutes = 15
					}
					if !running {
						// 低占用模式下不主动拉起，等待 /api/vpn/wake 或面板首次请求唤醒。
						return
					}
					if vpnActiveConnections(data) > 0 {
						vpnMu.Lock()
						vpnLastBusy = time.Now()
						vpnMu.Unlock()
						return
					}
					if time.Since(lastBusy) >= time.Duration(idleMinutes)*time.Minute {
						vpnStop()
						vpnMu.Lock()
						vpnAutoStop = true
						vpnMu.Unlock()
						appendVPNLog("空闲 " + itoa(idleMinutes) + " 分钟，自动停止核心以节省资源")
					}
				}()
			}
		}()
	})
}

func vpnActiveConnections(data map[string]any) int {
	port := int(int64Of(data["controllerPort"]))
	if port <= 0 {
		return 0
	}
	secret := str(data["controllerSecret"])
	client := &http.Client{Timeout: 5 * time.Second}
	req, err := http.NewRequest(http.MethodGet, "http://127.0.0.1:"+itoa(port)+"/connections", nil)
	if err != nil {
		return 0
	}
	if secret != "" {
		req.Header.Set("Authorization", "Bearer "+secret)
	}
	resp, err := client.Do(req)
	if err != nil {
		return 0
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return 0
	}
	var payload struct {
		Connections []any `json:"connections"`
	}
	if json.NewDecoder(io.LimitReader(resp.Body, 4<<20)).Decode(&payload) != nil {
		return 0
	}
	return len(payload.Connections)
}

func appendVPNLog(message string) {
	_ = os.MkdirAll(DataPath("vpn"), 0o700)
	f, err := os.OpenFile(DataPath("vpn", "core.log"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return
	}
	defer f.Close()
	_, _ = f.WriteString("[" + time.Now().Format("2006-01-02 15:04:05") + "] " + message + "\n")
}

func registerVPNRoutes(a *App) {
	a.Get("/api/vpn", perm("vpn"), func(w http.ResponseWriter, r *http.Request) error {
		data := loadVPN()
		// 低占用模式：面板首次打开时按需唤醒核心。
		if boolOf(data["enabled"]) && boolOf(data["lowPower"]) && !vpnRunning() {
			go func(d map[string]any) { _ = vpnStart(d) }(data)
		}
		return WriteJSON(w, 200, map[string]any{"config": data, "status": vpnStatus(data)})
	})
	a.Get("/api/vpn/status", perm("vpn"), func(w http.ResponseWriter, r *http.Request) error {
		data := loadVPN()
		return WriteJSON(w, 200, vpnStatus(data))
	})
	a.Put("/api/vpn/config", perm("vpn"), func(w http.ResponseWriter, r *http.Request) error {
		var req map[string]any
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		data := loadVPN()
		for _, key := range []string{"enabled", "portForward", "globalProxy", "lowPower", "idleMinutes",
			"mixedPort", "controllerPort", "controllerSecret", "binPath", "nodes", "forwards"} {
			if v, ok := req[key]; ok {
				data[key] = v
			}
		}
		if err := WriteJSONFile(DataPath("vpn.json"), data); err != nil {
			return err
		}
		warning := ""
		if boolOf(data["enabled"]) {
			if err := vpnStart(data); err != nil {
				warning = err.Error()
			}
		} else {
			vpnStop()
		}
		return WriteJSON(w, 200, map[string]any{
			"ok": true, "config": data, "status": vpnStatus(data), "warning": warning,
		})
	})
	a.Post("/api/vpn/start", perm("vpn"), func(w http.ResponseWriter, r *http.Request) error {
		data := loadVPN()
		if err := vpnStart(data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "status": vpnStatus(data)})
	})
	a.Post("/api/vpn/stop", perm("vpn"), func(w http.ResponseWriter, r *http.Request) error {
		vpnStop()
		data := loadVPN()
		return WriteJSON(w, 200, map[string]any{"ok": true, "status": vpnStatus(data)})
	})
	a.Post("/api/vpn/wake", perm("vpn"), func(w http.ResponseWriter, r *http.Request) error {
		data := loadVPN()
		err := vpnStart(data)
		started := err == nil
		detail := ""
		if err != nil {
			detail = err.Error()
		}
		return WriteJSON(w, 200, map[string]any{
			"ok": true, "started": started, "detail": detail, "status": vpnStatus(data),
		})
	})
	a.Post("/api/vpn/subtoken", perm("vpn"), func(w http.ResponseWriter, r *http.Request) error {
		data := loadVPN()
		token := randomHex(32)
		data["subToken"] = token
		if err := WriteJSONFile(DataPath("vpn.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "subToken": token})
	})
	a.Post("/api/vpn/detect", perm("vpn"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"binary": vpnDetectBinary(loadVPN())})
	})
	a.Get("/api/vpn/preview", perm("vpn"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"yaml": vpnProxyYAML(loadVPN())})
	})
	a.Get("/api/vpn/log", perm("vpn"), func(w http.ResponseWriter, r *http.Request) error {
		b, err := os.ReadFile(DataPath("vpn", "core.log"))
		if err != nil {
			return WriteJSON(w, 200, map[string]any{"log": ""})
		}
		s := string(b)
		if len(s) > 16000 {
			s = s[len(s)-16000:]
		}
		return WriteJSON(w, 200, map[string]any{"log": s})
	})
	a.Get("/api/vpn/sub", customAuth(), func(w http.ResponseWriter, r *http.Request) error {
		data := loadVPN()
		token := r.URL.Query().Get("token")
		if token == "" || data["subToken"] == "" || !hmac.Equal([]byte(token), []byte(str(data["subToken"]))) {
			return NewHTTPError(401, "订阅令牌无效")
		}
		w.Header().Set("Content-Type", "text/yaml; charset=utf-8")
		_, err := w.Write([]byte(vpnProxyYAML(data)))
		return err
	})
}

// RestoreVPN 面板启动时按配置恢复 VPN 核心（低占用模式等待首次唤醒）。
func RestoreVPN() {
	data := loadVPN()
	if boolOf(data["enabled"]) && !boolOf(data["lowPower"]) {
		if err := vpnStart(data); err != nil {
			appendVPNLog("启动恢复失败: " + err.Error())
		}
	} else if boolOf(data["enabled"]) {
		startVPNWatchdog()
	}
}

// --- 站点 Git 自动部署 ---

func loadGitDeploys() []map[string]any {
	var data map[string]any
	_ = ReadJSON(DataPath("gitdeploy.json"), &data)
	items := []map[string]any{}
	if arr, ok := data["deploys"].([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok {
				items = append(items, m)
			}
		}
	}
	return items
}

func saveGitDeploys(items []map[string]any) error {
	arr := []any{}
	for _, item := range items {
		arr = append(arr, item)
	}
	return WriteJSONFile(DataPath("gitdeploy.json"), map[string]any{"version": 1, "deploys": arr})
}

var gitDeployIDRe = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)

func findGitDeploy(id string) map[string]any {
	for _, d := range loadGitDeploys() {
		if str(d["id"]) == id {
			return d
		}
	}
	return nil
}

func validRepoURL(raw string) bool {
	low := strings.ToLower(raw)
	return strings.HasPrefix(low, "http://") || strings.HasPrefix(low, "https://") ||
		strings.HasPrefix(low, "ssh://") || strings.HasPrefix(raw, "git@")
}

// validDeployDir 与 Python _DIR_RE 一致：Linux 绝对路径或 Windows 盘符路径。
func validDeployDir(dir string) bool {
	if strings.HasPrefix(dir, "/") {
		return true
	}
	return regexp.MustCompile(`^[A-Za-z]:[\\/]`).MatchString(dir)
}

// gitDeployPublic 对外脱敏：扁平字段 + 永不返回 token / secret 明文。
func gitDeployPublic(d map[string]any) map[string]any {
	source := mapOf(d["source"])
	repo := str(source["repo_url"])
	masked := repo
	if u, err := url.Parse(repo); err == nil && u.User != nil && u.User.String() != "" {
		u.User = url.User(u.User.Username())
		masked = u.String()
	}
	return map[string]any{
		"id": str(d["id"]), "name": str(d["name"]),
		"site_id": str(d["site_id"]), "site_name": str(d["site_name"]),
		"repo_url": masked, "branch": str(source["branch"]),
		"auth":       defaultString(str(source["auth"]), "none"),
		"has_token":  str(source["token"]) != "",
		"deploy_dir": str(d["deploy_dir"]), "node_id": defaultString(str(d["node_id"]), "local"),
		"has_secret": str(d["secret"]) != "", "notify": boolOfDefault(d["notify"], true),
		"status":   defaultString(str(d["status"]), "idle"),
		"last_run": mapOf(d["last_run"]),
	}
}

func setGitDeployStatus(id, status string, lastRun map[string]any) {
	items := loadGitDeploys()
	for _, d := range items {
		if str(d["id"]) != id {
			continue
		}
		d["status"] = status
		if lastRun != nil {
			d["last_run"] = lastRun
		}
		d["updated_at"] = time.Now().Format("2006-01-02 15:04:05")
	}
	_ = saveGitDeploys(items)
}

func deployGitRepo(d map[string]any) (bool, string, string) {
	dir := str(d["deploy_dir"])
	source := mapOf(d["source"])
	repo := str(source["repo_url"])
	branch := defaultString(str(source["branch"]), "main")
	if dir == "" || !validDeployDir(dir) || strings.Contains(dir, "\n") {
		return false, "部署目录必须是绝对路径", ""
	}
	nodeID := defaultString(str(d["node_id"]), localNodeID)
	cloneURL := repo
	if str(source["auth"]) == "token" && str(source["token"]) != "" && strings.HasPrefix(strings.ToLower(repo), "http") {
		if u, err := url.Parse(repo); err == nil {
			u.User = url.UserPassword("x-access-token", str(source["token"]))
			cloneURL = u.String()
		}
	}
	script := "set -e; mkdir -p " + shellQuote(dir) + "; cd " + shellQuote(dir) + "; " +
		"if [ -d .git ]; then git fetch --all --prune && git checkout " + shellQuote(branch) + " && git reset --hard origin/" + shellQuote(branch) + "; " +
		"else git clone --branch " + shellQuote(branch) + " --depth 1 " + shellQuote(cloneURL) + " .; fi; " +
		"if [ -x ./deploy.sh ]; then ./deploy.sh; fi; git rev-parse --short HEAD"
	stdout, stderr, code, err := runShellOnNode(nodeID, script, 600*time.Second)
	if err != nil || code != 0 {
		return false, strings.TrimSpace(stderr), ""
	}
	lines := strings.Split(strings.TrimSpace(stdout), "\n")
	rev := ""
	if len(lines) > 0 {
		rev = cropText(strings.TrimSpace(lines[len(lines)-1]), 16)
	}
	return true, "", rev
}

// runGitDeploy 执行一次部署并写入任务中心 / 状态 / 通知。
func runGitDeploy(id string) (bool, string, map[string]any) {
	d := findGitDeploy(id)
	if d == nil {
		return false, "", nil
	}
	source := mapOf(d["source"])
	branch := defaultString(str(source["branch"]), "main")
	siteName := defaultString(str(d["site_name"]), defaultString(str(d["site_id"]), id))
	task := createTaskRecord(map[string]any{
		"name": "Git 部署 " + siteName + "@" + branch, "type": "gitdeploy",
		"target": str(d["deploy_dir"]), "title": "Git 部署 " + siteName + "@" + branch,
	})
	taskID := str(task["id"])
	appendTaskLog(taskID, map[string]any{"type": "log", "line": "开始部署：" + str(d["name"]) + "（" + siteName + " @ " + branch + "）"})
	appendTaskLog(taskID, map[string]any{"type": "log", "line": "目标目录：" + str(d["deploy_dir"]) + "（节点 " + str(d["node_id"]) + "）"})
	setGitDeployStatus(id, "running", nil)
	ok, detail, rev := deployGitRepo(d)
	now := time.Now().Format("2006-01-02 15:04:05")
	if ok {
		lastRun := map[string]any{"at": now, "ok": true, "rev": rev, "error": ""}
		setGitDeployStatus(id, "success", lastRun)
		updateTaskRecord(taskID, map[string]any{
			"status": "success", "finished_at": now, "result": map[string]any{"rev": rev},
		})
		appendTaskLog(taskID, map[string]any{"type": "log", "line": "部署成功"})
		if boolOfDefault(d["notify"], true) && PushAllFunc != nil {
			_, _ = PushAllFunc("站点《" + siteName + "》Git 部署成功（" + branch + "@" + defaultString(rev, "?") + "）")
		}
		return true, "", lastRun
	}
	lastRun := map[string]any{"at": now, "ok": false, "rev": "", "error": cropText(detail, 500)}
	setGitDeployStatus(id, "failed", lastRun)
	updateTaskRecord(taskID, map[string]any{"status": "failed", "finished_at": now, "error": detail})
	appendTaskLog(taskID, map[string]any{"type": "error", "message": detail})
	if boolOfDefault(d["notify"], true) && PushAllFunc != nil {
		_, _ = PushAllFunc("站点《" + siteName + "》Git 部署失败：" + cropText(detail, 200))
	}
	return false, detail, lastRun
}

func registerGitDeployRoutes(a *App) {
	a.Get("/api/gitdeploy", perm("gitdeploy"), func(w http.ResponseWriter, r *http.Request) error {
		deploys := []map[string]any{}
		for _, d := range loadGitDeploys() {
			deploys = append(deploys, gitDeployPublic(d))
		}
		return WriteJSON(w, 200, map[string]any{"deploys": deploys})
	})
	a.Post("/api/gitdeploy", perm("gitdeploy"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Name      string         `json:"name"`
			SiteID    string         `json:"site_id"`
			RepoURL   string         `json:"repo_url"`
			Branch    string         `json:"branch"`
			Auth      string         `json:"auth"`
			Token     string         `json:"token"`
			Source    map[string]any `json:"source"`
			DeployDir string         `json:"deploy_dir"`
			NodeID    string         `json:"node_id"`
			Notify    *bool          `json:"notify"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		repo := req.RepoURL
		branch := req.Branch
		auth := req.Auth
		token := req.Token
		if req.Source != nil {
			if v := str(req.Source["repo_url"]); v != "" {
				repo = v
			}
			if v := str(req.Source["branch"]); v != "" {
				branch = v
			}
			if v := str(req.Source["auth"]); v != "" {
				auth = v
			}
			token = str(req.Source["token"])
		}
		repo = strings.TrimSpace(repo)
		branch = defaultString(strings.TrimSpace(branch), "main")
		if !validRepoURL(repo) {
			return NewHTTPError(400, "repo_url 格式非法（仅支持 http/https/ssh/git@）")
		}
		if !regexp.MustCompile(`^[A-Za-z0-9._/-]{1,128}$`).MatchString(branch) {
			return NewHTTPError(400, "branch 格式非法")
		}
		siteName := ""
		dir := strings.TrimSpace(req.DeployDir)
		for _, s := range mergedSites() {
			if str(s["id"]) == req.SiteID {
				siteName = str(s["name"])
				if dir == "" {
					dir = str(s["root"])
				}
			}
		}
		if dir == "" || !validDeployDir(dir) {
			return NewHTTPError(400, "deploy_dir 必须为绝对路径")
		}
		notify := true
		if req.Notify != nil {
			notify = *req.Notify
		}
		secret := randomHex(32)
		rec := map[string]any{
			"id": randomHex(10), "name": defaultString(strings.TrimSpace(req.Name), "Git 部署"),
			"site_id": req.SiteID, "site_name": siteName,
			"source": map[string]any{
				"repo_url": repo, "branch": branch,
				"auth": defaultString(auth, "none"), "token": token,
			},
			"deploy_dir": dir, "node_id": defaultString(req.NodeID, "local"),
			"secret": secret, "notify": notify, "status": "idle",
			"last_run":   map[string]any{},
			"created_at": time.Now().Format("2006-01-02T15:04:05"),
			"updated_at": time.Now().Format("2006-01-02T15:04:05"),
		}
		if err := saveGitDeploys(append(loadGitDeploys(), rec)); err != nil {
			return err
		}
		resp := gitDeployPublic(rec)
		resp["secret_once"] = secret
		return WriteJSON(w, 200, resp)
	})
	a.Put("/api/gitdeploy/{deploy_id}", perm("gitdeploy"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "deploy_id")
		if !gitDeployIDRe.MatchString(id) {
			return NewHTTPError(400, "非法的部署 ID")
		}
		var req map[string]any
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		items := loadGitDeploys()
		for _, d := range items {
			if str(d["id"]) != id {
				continue
			}
			source := mapOf(d["source"])
			if v, ok := req["name"]; ok {
				d["name"] = cropText(strings.TrimSpace(str(v)), 64)
			}
			if v, ok := req["repo_url"]; ok {
				repo := strings.TrimSpace(str(v))
				if !validRepoURL(repo) {
					return NewHTTPError(400, "repo_url 格式非法")
				}
				source["repo_url"] = repo
			}
			if s, ok := req["source"].(map[string]any); ok {
				if v := str(s["repo_url"]); v != "" {
					if !validRepoURL(v) {
						return NewHTTPError(400, "repo_url 格式非法")
					}
					source["repo_url"] = v
				}
				if v := str(s["branch"]); v != "" {
					source["branch"] = v
				}
				if v, ok := s["auth"]; ok {
					source["auth"] = str(v)
				}
				if v, ok := s["token"]; ok {
					source["token"] = str(v)
				}
			}
			if v, ok := req["branch"]; ok {
				source["branch"] = str(v)
			}
			if v, ok := req["auth"]; ok {
				source["auth"] = str(v)
			}
			if v, ok := req["token"]; ok {
				source["token"] = str(v)
			}
			if !regexp.MustCompile(`^[A-Za-z0-9._/-]{1,128}$`).MatchString(str(source["branch"])) {
				return NewHTTPError(400, "branch 格式非法")
			}
			d["source"] = source
			if v, ok := req["deploy_dir"]; ok {
				dir := strings.TrimSpace(str(v))
				if dir == "" || !validDeployDir(dir) {
					return NewHTTPError(400, "deploy_dir 必须为绝对路径")
				}
				d["deploy_dir"] = dir
			}
			if v, ok := req["node_id"]; ok {
				d["node_id"] = str(v)
			}
			if v, ok := req["notify"]; ok {
				d["notify"] = boolOf(v)
			}
			secretOnce := ""
			if boolOf(req["reset_secret"]) {
				d["secret"] = randomHex(32)
				secretOnce = str(d["secret"])
			}
			d["updated_at"] = time.Now().Format("2006-01-02T15:04:05")
			resp := gitDeployPublic(d)
			resp["secret_once"] = secretOnce
			if err := saveGitDeploys(items); err != nil {
				return err
			}
			return WriteJSON(w, 200, resp)
		}
		return NewHTTPError(404, "部署不存在")
	})
	a.Delete("/api/gitdeploy/{deploy_id}", perm("gitdeploy"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "deploy_id")
		if !gitDeployIDRe.MatchString(id) {
			return NewHTTPError(400, "非法的部署 ID")
		}
		items := loadGitDeploys()
		kept := []map[string]any{}
		for _, d := range items {
			if str(d["id"]) != id {
				kept = append(kept, d)
			}
		}
		if len(kept) == len(items) {
			return NewHTTPError(404, "部署不存在")
		}
		if err := saveGitDeploys(kept); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	// Go 1.22 ServeMux 不允许 /webhook/{id} 与 /{id}/trigger 两个 POST
	// 模式共存（路径优先级相同），合并为一个两段通配路由再分发。
	a.Post("/api/gitdeploy/{first}/{second}", customAuth(), func(w http.ResponseWriter, r *http.Request) error {
		first, second := PathParam(r, "first"), PathParam(r, "second")
		if first == "webhook" {
			return gitDeployWebhook(w, r, second)
		}
		if second == "trigger" {
			u, err := Authenticate(r, true)
			if err != nil {
				return err
			}
			if u.Role() != "admin" || !HasPerm(u, "gitdeploy") {
				return NewHTTPError(403, "需要 gitdeploy 模块权限")
			}
			return gitDeployTrigger(w, first)
		}
		return NewHTTPError(404, "Not Found")
	})
}

func gitDeployTrigger(w http.ResponseWriter, id string) error {
	if findGitDeploy(id) == nil {
		return NewHTTPError(404, "部署不存在")
	}
	ok, _, lastRun := runGitDeploy(id)
	if !ok {
		return WriteJSON(w, 200, map[string]any{
			"ok": false, "error": "部署失败，请查看任务中心日志", "last_run": lastRun,
		})
	}
	return WriteJSON(w, 200, map[string]any{"ok": true, "last_run": lastRun})
}

func gitDeployWebhook(w http.ResponseWriter, r *http.Request, id string) error {
	// Git 平台无面板登录态：按仓库密钥校验签名/令牌，且本路由不经 Agent 代理。
	if !gitDeployIDRe.MatchString(id) {
		return NewHTTPError(400, "非法的部署 ID")
	}
	d := findGitDeploy(id)
	if d == nil {
		return NewHTTPError(404, "部署不存在")
	}
	body, _ := io.ReadAll(io.LimitReader(r.Body, 2<<20))
	secret := str(d["secret"])
	ghSig := r.Header.Get("X-Hub-Signature-256")
	gitlabToken := r.Header.Get("X-Gitlab-Token")
	querySecret := r.URL.Query().Get("secret")
	verified := false
	if ghSig != "" {
		mac := hmac.New(sha256.New, []byte(secret))
		mac.Write(body)
		expect := "sha256=" + hex.EncodeToString(mac.Sum(nil))
		verified = hmac.Equal([]byte(expect), []byte(ghSig))
	}
	if !verified && gitlabToken != "" {
		verified = hmac.Equal([]byte(secret), []byte(gitlabToken))
	}
	if !verified && querySecret != "" {
		verified = hmac.Equal([]byte(secret), []byte(querySecret))
	}
	if !verified {
		return NewHTTPError(401, "签名校验失败")
	}
	var payload map[string]any
	_ = json.Unmarshal(body, &payload)
	ref := str(payload["ref"])
	if ref == "" {
		ref = str(payload["head"])
	}
	branch := strings.TrimPrefix(ref, "refs/heads/")
	if idx := strings.LastIndex(branch, "/"); idx >= 0 && !strings.HasPrefix(ref, "refs/heads/") {
		branch = branch[idx+1:]
	}
	want := defaultString(str(mapOf(d["source"])["branch"]), "main")
	if branch != "" && branch != want {
		return WriteJSON(w, 200, map[string]any{"ok": true, "skipped": true, "reason": "branch 不匹配"})
	}
	if ok, _, _ := runGitDeploy(id); !ok {
		return WriteJSON(w, 200, map[string]any{"ok": false, "error": "部署失败，请查看任务中心日志"})
	}
	return WriteJSON(w, 200, map[string]any{"ok": true})
}
