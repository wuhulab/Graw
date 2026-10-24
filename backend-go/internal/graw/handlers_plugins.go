package graw

import (
	"bytes"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

// 插件开放协议（GPOP）：与 Python plugin_protocol.py / routers/plugins.py 对齐。
// 注册表结构：{ "api_version": 1, "enabled": true, "plugins": { <id>: {...} } }

const (
	pluginAPIVersion     = 1
	pluginDefaultPanel   = "http://127.0.0.1:8000"
	pluginMaxConfigBytes = 64 * 1024
)

var (
	pluginIDRe      = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`)
	pluginVersionRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$`)
	pluginCaps      = []string{"panel_info", "notify", "audit", "config"}
)

func pluginCapabilityKnown(c string) bool {
	for _, cap := range pluginCaps {
		if cap == c {
			return true
		}
	}
	return false
}

func pluginDefaultStore() map[string]any {
	return map[string]any{
		"api_version": pluginAPIVersion, "enabled": true, "plugins": map[string]any{},
	}
}

// loadPluginStore 读取注册表；兼容旧 Go 版的数组结构并自动迁移为字典。
func loadPluginStore() map[string]any {
	data := pluginDefaultStore()
	_ = ReadJSON(DataPath("plugins.json"), &data)
	if data == nil {
		data = pluginDefaultStore()
	}
	if data["api_version"] == nil {
		data["api_version"] = pluginAPIVersion
	}
	if _, ok := data["enabled"]; !ok {
		data["enabled"] = true
	}
	switch plugins := data["plugins"].(type) {
	case map[string]any:
		data["plugins"] = plugins
	case []any:
		migrated := map[string]any{}
		for _, item := range plugins {
			if m, ok := item.(map[string]any); ok && str(m["id"]) != "" {
				migrated[str(m["id"])] = m
			}
		}
		data["plugins"] = migrated
	default:
		data["plugins"] = map[string]any{}
	}
	return data
}

func savePluginStore(data map[string]any) error {
	return WriteJSONFile(DataPath("plugins.json"), data)
}

func pluginFeatureEnabled() bool {
	return boolOfDefault(loadPluginStore()["enabled"], true)
}

func pluginRecords(data map[string]any) []map[string]any {
	plugins := mapOf(data["plugins"])
	ids := make([]string, 0, len(plugins))
	for id := range plugins {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	out := []map[string]any{}
	for _, id := range ids {
		if m, ok := plugins[id].(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out
}

func getPluginRecord(id string) map[string]any {
	plugins := mapOf(loadPluginStore()["plugins"])
	if m, ok := plugins[id].(map[string]any); ok {
		return m
	}
	return nil
}

func updatePluginRecord(id string, fields map[string]any) map[string]any {
	data := loadPluginStore()
	plugins := mapOf(data["plugins"])
	rec, ok := plugins[id].(map[string]any)
	if !ok {
		return nil
	}
	for k, v := range fields {
		if k == "id" || k == "token_hash" {
			continue
		}
		rec[k] = v
	}
	_ = savePluginStore(data)
	return rec
}

func hashToken(token string) string {
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:])
}

func randomTokenURLSafe() string {
	b := make([]byte, 32)
	_, _ = rand.Read(b)
	return base64.RawURLEncoding.EncodeToString(b)
}

// --- 公共记录 ---

func pluginPublic(rec map[string]any) map[string]any {
	out := map[string]any{}
	for k, v := range rec {
		if k == "token_hash" || k == "manifest" {
			continue
		}
		out[k] = v
	}
	manifest := mapOf(rec["manifest"])
	if env, ok := manifest["env"]; ok {
		out["env"] = env
	}
	hasCompose := false
	if compose := str(rec["compose_file"]); compose != "" {
		root, _ := filepath.Abs(DataPath())
		cand, err := filepath.Abs(compose)
		if err == nil && (cand == root || strings.HasPrefix(cand, root+string(os.PathSeparator))) {
			hasCompose = Exists(compose)
		}
	}
	out["has_compose"] = hasCompose
	return out
}

// --- 清单校验 ---

func validatePluginManifest(raw map[string]any) (map[string]any, error) {
	if raw == nil {
		return nil, NewHTTPError(400, "插件清单必须是 YAML/JSON 对象")
	}
	apiVersion := pluginAPIVersion
	if v, ok := raw["api_version"]; ok && v != nil {
		n, ok := toIntStrict(v)
		if !ok {
			return nil, NewHTTPError(400, "api_version 必须是整数")
		}
		if n > pluginAPIVersion {
			return nil, NewHTTPError(400, "插件协议版本过新（清单 "+itoa(n)+" > 面板支持 "+itoa(pluginAPIVersion)+"）")
		}
		apiVersion = n
	}
	manifest := map[string]any{}
	for _, f := range []string{"id", "name", "version", "description"} {
		val := strings.TrimSpace(str(raw[f]))
		if val == "" {
			return nil, NewHTTPError(400, "插件清单缺少必填字段: "+f)
		}
		if len(val) > 2000 {
			return nil, NewHTTPError(400, "字段 "+f+" 过长")
		}
		manifest[f] = val
	}
	pid := str(manifest["id"])
	if !pluginIDRe.MatchString(pid) {
		return nil, NewHTTPError(400, "插件 ID 只能包含英文字母 / 数字 / _ / - / .，且必须以字母或数字开头")
	}
	if !pluginVersionRe.MatchString(str(manifest["version"])) {
		return nil, NewHTTPError(400, "version 只能包含英文字母 / 数字 / . / _ / + / -")
	}
	for _, f := range []string{"author", "category", "homepage"} {
		if val := strings.TrimSpace(str(raw[f])); val != "" {
			manifest[f] = cropText(val, 2000)
		}
	}
	if composeURL := strings.TrimSpace(str(raw["compose_url"])); strings.HasPrefix(composeURL, "http://") || strings.HasPrefix(composeURL, "https://") {
		manifest["compose_url"] = cropText(composeURL, 2048)
	}
	caps := []any{}
	if rawCaps, ok := raw["capabilities"]; ok && rawCaps != nil {
		arr, ok := rawCaps.([]any)
		if !ok {
			return nil, NewHTTPError(400, "capabilities 必须是字符串列表")
		}
		for _, c := range arr {
			if cs, ok := c.(string); ok && pluginCapabilityKnown(cs) {
				caps = append(caps, cs)
			}
		}
	}
	manifest["capabilities"] = caps
	if entryRaw, ok := raw["entry"]; ok {
		if entry, ok := entryRaw.(map[string]any); ok {
			clean := map[string]any{}
			if svc := strings.TrimSpace(str(entry["service"])); svc != "" {
				clean["service"] = cropText(svc, 128)
			}
			if p, ok := toIntStrict(entry["port"]); ok && p >= 1 && p <= 65535 {
				clean["port"] = p
			}
			if path := str(entry["path"]); strings.HasPrefix(path, "/") {
				clean["path"] = cropText(path, 512)
			}
			if len(clean) > 0 {
				manifest["entry"] = clean
			}
		}
	}
	if envRaw, ok := raw["env"]; ok {
		if arr, ok := envRaw.([]any); ok {
			cleanEnvs := []any{}
			for _, item := range arr {
				e, ok := item.(map[string]any)
				if !ok {
					continue
				}
				name := strings.TrimSpace(str(e["name"]))
				if name == "" || len(name) > 128 {
					continue
				}
				cleanEnvs = append(cleanEnvs, map[string]any{
					"name": name, "default": cropText(str(e["default"]), 512),
					"desc": cropText(str(e["desc"]), 512),
				})
			}
			if len(cleanEnvs) > 0 {
				manifest["env"] = cleanEnvs
			}
		}
	}
	manifest["api_version"] = apiVersion
	return manifest, nil
}

func toIntStrict(v any) (int, bool) {
	switch n := v.(type) {
	case int:
		return n, true
	case int64:
		return int(n), true
	case float64:
		return int(n), n == float64(int(n))
	case json.Number:
		i, err := n.Int64()
		return int(i), err == nil
	}
	return 0, false
}

func cropText(s string, max int) string {
	if len(s) > max {
		return s[:max]
	}
	return s
}

// --- 本地示例插件 ---

func localPluginRoots() []string {
	roots := []string{}
	if d := appStoreDir(); d != "" {
		roots = append(roots, filepath.Join(d, "plugins"))
		if parent := filepath.Dir(d); parent != "" {
			roots = append(roots, filepath.Join(parent, "plugin-examples"))
		}
	}
	roots = append(roots, "plugin-examples")
	return roots
}

func readLocalManifest(pluginID string) (string, string, bool) {
	if !pluginIDRe.MatchString(pluginID) {
		return "", "", false
	}
	for _, root := range localPluginRoots() {
		pdir := filepath.Join(root, pluginID)
		b, err := os.ReadFile(filepath.Join(pdir, "plugin.yml"))
		if err == nil {
			abs, _ := filepath.Abs(pdir)
			return string(b), abs, true
		}
	}
	return "", "", false
}

func findLocalCompose(pdir string) (string, error) {
	for _, name := range []string{"docker-compose.yml", "docker-compose.yaml", "compose.yml"} {
		p := filepath.Join(pdir, name)
		if Exists(p) {
			return p, nil
		}
	}
	return "", NewHTTPError(404, "本地插件目录缺少 docker-compose.yml: "+pdir)
}

func localPluginExamples() []map[string]any {
	found := map[string]bool{}
	for _, root := range localPluginRoots() {
		entries, err := os.ReadDir(root)
		if err != nil {
			continue
		}
		for _, e := range entries {
			if e.IsDir() && !strings.HasPrefix(e.Name(), ".") {
				found[e.Name()] = true
			}
		}
	}
	ids := make([]string, 0, len(found))
	for id := range found {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	out := []map[string]any{}
	for _, id := range ids {
		out = append(out, map[string]any{"id": id})
	}
	return out
}

// --- 安装 ---

type pluginInstallRequest struct {
	ID       string `json:"id"`
	Source   string `json:"source"`
	PanelURL string `json:"panel_url"`
	Port     int    `json:"port"`
	Pull     *bool  `json:"pull"`
	Restart  string `json:"restart"`
}

func injectPluginEnv(services map[string]any, entrySvc, pid, token, panelURL string) {
	svc, ok := services[entrySvc].(map[string]any)
	if !ok {
		return
	}
	envs := map[string]string{
		"GRAW_PLUGIN_ID": pid, "GRAW_PLUGIN_TOKEN": token,
		"GRAW_PANEL_URL": panelURL, "GRAW_PLUGIN_API_VERSION": itoa(pluginAPIVersion),
	}
	switch env := svc["environment"].(type) {
	case map[string]any:
		for k, v := range envs {
			env[k] = v
		}
	case []any:
		existing := map[string]bool{}
		for _, item := range env {
			existing[strings.SplitN(str(item), "=", 2)[0]] = true
		}
		keys := make([]string, 0, len(envs))
		for k := range envs {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			if !existing[k] {
				env = append(env, k+"="+envs[k])
			}
		}
		svc["environment"] = env
	default:
		svc["environment"] = envs
	}
}

func applyPluginPort(svc map[string]any, external int) {
	if svc == nil {
		return
	}
	ports, ok := svc["ports"].([]any)
	if !ok {
		svc["ports"] = []any{itoa(external) + ":" + itoa(external)}
		return
	}
	svc["ports"] = append(ports, itoa(external)+":"+itoa(external))
}

func openPluginFirewallPort(port int, pluginID string) {
	rule := map[string]any{
		"id": randomHex(8), "port": port, "protocol": "tcp", "action": "allow",
		"comment": "插件 " + pluginID, "created_at": nowISO(),
	}
	addPortRule(rule)
	data := loadFirewall()
	data["port_rules"] = append(firewallRules(data, "port_rules"), rule)
	_ = saveFirewall(data)
}

func installPlugin(req pluginInstallRequest) (map[string]any, error) {
	pid := strings.TrimSpace(req.ID)
	if !pluginIDRe.MatchString(pid) {
		return nil, NewHTTPError(400, "插件 ID 只能包含英文字母 / 数字 / _ / - / .，且必须以字母或数字开头")
	}
	source := strings.TrimSpace(req.Source)
	if source == "" {
		source = "local"
	}
	var ymlText, pdir, baseURL, sourceType string
	switch {
	case source == "local" || strings.HasPrefix(source, "local:"):
		lookID := pid
		if strings.Contains(source, ":") {
			lookID = strings.TrimSpace(strings.SplitN(source, ":", 2)[1])
		}
		text, dir, ok := readLocalManifest(lookID)
		if !ok {
			return nil, NewHTTPError(404, "本地示例插件不存在: "+lookID)
		}
		ymlText, pdir, sourceType = text, dir, "local"
	case strings.HasPrefix(source, "http://") || strings.HasPrefix(source, "https://"):
		text, err := fetchAppURL(source, 30*time.Second, 4<<20)
		if err != nil {
			return nil, err
		}
		ymlText, baseURL, sourceType = text, source, "remote"
	default:
		dirSource := cleanHostView(source)
		if !IsDir(dirSource) {
			return nil, NewHTTPError(400, "非法 source: "+source)
		}
		b, err := os.ReadFile(filepath.Join(dirSource, "plugin.yml"))
		if err != nil {
			return nil, NewHTTPError(404, "目录缺少 plugin.yml: "+source)
		}
		ymlText, pdir, sourceType = string(b), dirSource, "dir"
	}

	var raw map[string]any
	if err := yaml.Unmarshal([]byte(ymlText), &raw); err != nil {
		return nil, NewHTTPError(400, "插件清单解析失败: "+err.Error())
	}
	manifest, err := validatePluginManifest(raw)
	if err != nil {
		return nil, err
	}
	if str(manifest["id"]) != pid {
		return nil, NewHTTPError(400, "清单 ID 不一致: "+str(manifest["id"])+" != "+pid)
	}

	var composeText string
	if sourceType == "remote" {
		composeURL := str(manifest["compose_url"])
		if composeURL == "" {
			base := baseURL
			if idx := strings.LastIndex(baseURL, "/"); idx >= 0 {
				base = baseURL[:idx]
			}
			composeURL = base + "/docker-compose.yml"
		}
		composeText, err = fetchAppURL(composeURL, 60*time.Second, 4<<20)
		if err != nil {
			return nil, err
		}
	} else {
		composePath, cerr := findLocalCompose(pdir)
		if cerr != nil {
			return nil, cerr
		}
		b, rerr := os.ReadFile(composePath)
		if rerr != nil {
			return nil, NewHTTPError(500, "读取 docker-compose.yml 失败")
		}
		composeText = string(b)
	}

	var cdata map[string]any
	if err := yaml.Unmarshal([]byte(composeText), &cdata); err != nil {
		return nil, NewHTTPError(400, "docker-compose.yml 解析失败: "+err.Error())
	}
	services, ok := cdata["services"].(map[string]any)
	if cdata == nil || !ok || len(services) == 0 {
		return nil, NewHTTPError(400, "docker-compose.yml 缺少 services 定义")
	}

	entrySvc := ""
	if entry := mapOf(manifest["entry"]); entry != nil {
		entrySvc = strings.TrimSpace(str(entry["service"]))
	}
	if entrySvc == "" || services[entrySvc] == nil {
		ids := make([]string, 0, len(services))
		for id := range services {
			ids = append(ids, id)
		}
		sort.Strings(ids)
		if len(ids) > 0 {
			entrySvc = ids[0]
		}
	}
	if entrySvc == "" {
		return nil, NewHTTPError(400, "compose 中没有可用的服务定义")
	}

	token := randomTokenURLSafe()
	panelURL := strings.TrimRight(strings.TrimSpace(req.PanelURL), "/")
	if panelURL == "" {
		panelURL = pluginDefaultPanel
	}
	if !strings.HasPrefix(panelURL, "http://") && !strings.HasPrefix(panelURL, "https://") {
		return nil, NewHTTPError(400, "panel_url 必须是 http/https 地址")
	}
	injectPluginEnv(services, entrySvc, pid, token, panelURL)

	port := req.Port
	if port == 0 {
		port = int(int64Of(mapOf(manifest["entry"])["port"]))
	}
	if port > 0 {
		if svc, ok := services[entrySvc].(map[string]any); ok {
			setServiceEnv(svc, "GRAW_ENTRY_PORT", itoa(port))
			applyPluginPort(svc, port)
		}
		openPluginFirewallPort(port, pid)
	}
	restart := req.Restart
	if !validRestartPolicies[restart] {
		restart = "always"
	}
	for _, raw := range services {
		if svc, ok := raw.(map[string]any); ok {
			svc["restart"] = restart
		}
	}

	var buf bytes.Buffer
	enc := yaml.NewEncoder(&buf)
	enc.SetIndent(2)
	if err := enc.Encode(cdata); err != nil {
		return nil, NewHTTPError(500, "docker-compose.yml 序列化失败: "+err.Error())
	}
	_ = enc.Close()

	projectDir := DataPath("plugins", pid)
	if err := EnsureDir(projectDir); err != nil {
		return nil, err
	}
	composePath := filepath.Join(projectDir, "docker-compose.yml")
	if err := os.WriteFile(composePath, buf.Bytes(), 0o600); err != nil {
		return nil, err
	}
	enginePath := HostVisiblePath(composePath)
	pull := true
	if req.Pull != nil {
		pull = *req.Pull
	}
	if pull {
		_ = dockerRun(1800*time.Second, "compose", "-f", enginePath, "pull")
	}
	_ = dockerRun(1800*time.Second, "compose", "-f", enginePath, "up", "-d", "--remove-orphans")

	record := map[string]any{
		"id": pid, "name": str(manifest["name"]), "version": str(manifest["version"]),
		"api_version": int(int64Of(manifest["api_version"])),
		"author":      str(manifest["author"]), "description": str(manifest["description"]),
		"category": str(manifest["category"]), "capabilities": manifest["capabilities"],
		"manifest": manifest, "token_hash": hashToken(token), "compose_file": composePath,
		"port": nil, "enabled": true, "status": "running", "installed_at": nowISO(),
	}
	if port > 0 {
		record["port"] = port
	}
	data := loadPluginStore()
	mapOf(data["plugins"])[pid] = record
	if err := savePluginStore(data); err != nil {
		return nil, err
	}
	return map[string]any{
		"ok": true, "plugin": pluginPublic(record), "token": token,
		"entry_service": entrySvc, "panel_url": panelURL,
		"compose_file": composePath, "warnings": []any{},
	}, nil
}

// --- 插件 compose 操作 ---

func pluginEnginePath(rec map[string]any) (string, error) {
	compose := str(rec["compose_file"])
	root, _ := filepath.Abs(DataPath())
	cand, err := filepath.Abs(compose)
	if compose == "" || err != nil || !(cand == root || strings.HasPrefix(cand, root+string(os.PathSeparator))) {
		return "", NewHTTPError(400, "插件缺少 compose 文件: "+str(rec["id"]))
	}
	if !Exists(compose) {
		return "", NewHTTPError(400, "插件缺少 compose 文件: "+str(rec["id"]))
	}
	return HostVisiblePath(compose), nil
}

func requirePluginCompose(id string) (map[string]any, string, error) {
	rec := getPluginRecord(id)
	if rec == nil {
		return nil, "", NewHTTPError(404, "插件不存在: "+id)
	}
	enginePath, err := pluginEnginePath(rec)
	if err != nil {
		return nil, "", err
	}
	return rec, enginePath, nil
}

func cropHTTPDetail(text string) string {
	text = strings.TrimSpace(text)
	if len(text) > 2000 {
		return text[len(text)-2000:]
	}
	return text
}

// --- 路由 ---

func registerPluginRoutes(a *App) {
	// 总开关路由始终注册（关闭后仍可重新打开，需重启生效）
	a.Get("/api/plugins/settings", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{
			"enabled": pluginFeatureEnabled(), "api_version": pluginAPIVersion,
			"capabilities": pluginCaps, "restart_required": true,
		})
	})
	a.Put("/api/plugins/settings", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Enabled bool `json:"enabled"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		data := loadPluginStore()
		data["enabled"] = req.Enabled
		if err := savePluginStore(data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{
			"ok": true, "enabled": req.Enabled, "restart_required": true,
			"hint": "插件开关修改后需重启面板生效",
		})
	})

	// 关闭插件功能时不加载业务路由（重启后完全生效，与 Python 一致）
	if !pluginFeatureEnabled() {
		return
	}

	a.Get("/api/plugins/protocol", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{
			"api_version": pluginAPIVersion, "capabilities": pluginCaps,
			"name": "Graw Plugin Open Protocol", "local_examples": localPluginExamples(),
		})
	})
	a.Get("/api/plugins", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		records := pluginRecords(loadPluginStore())
		out := []map[string]any{}
		for _, rec := range records {
			out = append(out, pluginPublic(rec))
		}
		return WriteJSON(w, 200, map[string]any{
			"api_version": pluginAPIVersion, "count": len(out), "plugins": out,
		})
	})
	a.Get("/api/plugins/{plugin_id}", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "plugin_id")
		rec := getPluginRecord(id)
		if rec == nil {
			return NewHTTPError(404, "插件不存在: "+id)
		}
		return WriteJSON(w, 200, pluginPublic(rec))
	})
	a.Post("/api/plugins/install", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		var req pluginInstallRequest
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		res, err := installPlugin(req)
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, res)
	})
	pluginComposeAction := func(action string, status string, enabled bool) func(http.ResponseWriter, *http.Request) error {
		return func(w http.ResponseWriter, r *http.Request) error {
			id := PathParam(r, "plugin_id")
			_, enginePath, err := requirePluginCompose(id)
			if err != nil {
				return err
			}
			res := dockerRun(300*time.Second, "compose", "-f", enginePath, action)
			if res.Code != 0 {
				detail := strings.TrimSpace(res.Stderr)
				if detail == "" {
					detail = strings.TrimSpace(res.Stdout)
				}
				if detail == "" {
					detail = "compose " + action + " 失败"
				}
				return NewHTTPError(500, cropHTTPDetail(detail))
			}
			if status != "" {
				updatePluginRecord(id, map[string]any{"status": status, "enabled": enabled})
			}
			return WriteJSON(w, 200, map[string]any{"ok": true, "plugin_id": id})
		}
	}
	a.Post("/api/plugins/{plugin_id}/start", adminOnly(), pluginComposeAction("start", "running", true))
	a.Post("/api/plugins/{plugin_id}/stop", adminOnly(), pluginComposeAction("stop", "stopped", false))
	a.Post("/api/plugins/{plugin_id}/restart", adminOnly(), pluginComposeAction("restart", "running", true))
	a.Post("/api/plugins/{plugin_id}/uninstall", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "plugin_id")
		_, enginePath, err := requirePluginCompose(id)
		if err != nil {
			return err
		}
		_ = dockerRun(600*time.Second, "compose", "-f", enginePath, "down", "--remove-orphans")
		data := loadPluginStore()
		delete(mapOf(data["plugins"]), id)
		if err := savePluginStore(data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "plugin_id": id})
	})
	a.Post("/api/plugins/{plugin_id}/rotate-token", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "plugin_id")
		if getPluginRecord(id) == nil {
			return NewHTTPError(404, "插件不存在: "+id)
		}
		token := randomTokenURLSafe()
		data := loadPluginStore()
		rec, _ := mapOf(data["plugins"])[id].(map[string]any)
		if rec == nil {
			return NewHTTPError(404, "插件不存在: "+id)
		}
		rec["token_hash"] = hashToken(token)
		if err := savePluginStore(data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{
			"ok": true, "plugin_id": id, "token": token, "plugin": pluginPublic(rec),
		})
	})
	a.Get("/api/plugins/{plugin_id}/config", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "plugin_id")
		if getPluginRecord(id) == nil {
			return NewHTTPError(404, "插件不存在: "+id)
		}
		return WriteJSON(w, 200, map[string]any{"plugin_id": id, "config": loadPluginConfig(id)})
	})

	// --- 插件开放接口（令牌鉴权） ---
	a.Get("/api/op/protocol", customAuth(), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{
			"api_version": pluginAPIVersion, "capabilities": pluginCaps,
			"name": "Graw Plugin Open Protocol",
		})
	})
	a.Get("/api/op/me", customAuth(), func(w http.ResponseWriter, r *http.Request) error {
		rec, err := pluginAuth(r)
		if err != nil {
			return err
		}
		if err := requirePluginCapability(rec, "panel_info"); err != nil {
			return err
		}
		scheme := "http"
		if r.TLS != nil || strings.EqualFold(r.Header.Get("X-Forwarded-Proto"), "https") {
			scheme = "https"
		}
		_, offset := time.Now().Zone()
		return WriteJSON(w, 200, map[string]any{
			"plugin": pluginPublic(rec),
			"panel": map[string]any{
				"panel_url":           scheme + "://" + r.Host,
				"server_time":         nowISO(),
				"timezone_utc_offset": formatUTCOffset(offset),
			},
		})
	})
	a.Post("/api/op/notify", customAuth(), func(w http.ResponseWriter, r *http.Request) error {
		rec, err := pluginAuth(r)
		if err != nil {
			return err
		}
		if err := requirePluginCapability(rec, "notify"); err != nil {
			return err
		}
		var req struct {
			Title   string `json:"title"`
			Message string `json:"message"`
			Level   string `json:"level"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		title := strings.TrimSpace(req.Title)
		if title == "" || len(title) > 200 {
			return NewHTTPError(400, "title 不能为空且不超过 200 字符")
		}
		level := req.Level
		if level != "info" && level != "warn" && level != "error" {
			level = "info"
		}
		message := "[插件:" + str(rec["id"]) + "] " + title
		if body := strings.TrimSpace(req.Message); body != "" {
			message += " - " + body
		}
		sent := 0
		if PushAllFunc != nil {
			sent, _ = PushAllFunc(message)
		}
		AuditRecord("插件通知", "plugin:"+str(rec["id"]), "", cropText("插件推送通知: "+message, 200))
		return WriteJSON(w, 200, map[string]any{"ok": true, "level": level, "channels_sent": sent})
	})
	a.Post("/api/op/audit", customAuth(), func(w http.ResponseWriter, r *http.Request) error {
		rec, err := pluginAuth(r)
		if err != nil {
			return err
		}
		if err := requirePluginCapability(rec, "audit"); err != nil {
			return err
		}
		var req struct {
			Action string `json:"action"`
			Detail string `json:"detail"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		action := cropText(strings.TrimSpace(req.Action), 64)
		if action == "" {
			action = "插件事件"
		}
		AuditRecord(action, "plugin:"+str(rec["id"]), "", cropText(strings.TrimSpace(req.Detail), 2000))
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Get("/api/op/config", customAuth(), func(w http.ResponseWriter, r *http.Request) error {
		rec, err := pluginAuth(r)
		if err != nil {
			return err
		}
		if err := requirePluginCapability(rec, "config"); err != nil {
			return err
		}
		id := str(rec["id"])
		return WriteJSON(w, 200, map[string]any{"plugin_id": id, "config": loadPluginConfig(id)})
	})
	a.Put("/api/op/config", customAuth(), func(w http.ResponseWriter, r *http.Request) error {
		rec, err := pluginAuth(r)
		if err != nil {
			return err
		}
		if err := requirePluginCapability(rec, "config"); err != nil {
			return err
		}
		var req struct {
			Config map[string]any `json:"config"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if req.Config == nil {
			req.Config = map[string]any{}
		}
		id := str(rec["id"])
		payload, _ := json.Marshal(req.Config)
		if len(payload) > pluginMaxConfigBytes {
			return NewHTTPError(400, "配置过大（上限 64KB）")
		}
		if err := savePluginConfig(id, req.Config); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{
			"ok": true, "plugin_id": id, "config": loadPluginConfig(id),
		})
	})
}

func formatUTCOffset(seconds int) string {
	sign := "+"
	if seconds < 0 {
		sign = "-"
		seconds = -seconds
	}
	return sign + pad2(seconds/3600) + pad2((seconds%3600)/60)
}

func pad2(n int) string {
	if n < 10 {
		return "0" + itoa(n)
	}
	return itoa(n)
}

func pluginAuth(r *http.Request) (map[string]any, error) {
	pid := strings.TrimSpace(r.Header.Get("X-Graw-Plugin-Id"))
	if pid == "" {
		return nil, NewHTTPError(401, "缺少 X-Graw-Plugin-Id 头")
	}
	token := BearerToken(r)
	if token == "" {
		return nil, NewHTTPError(401, "缺少 Bearer 令牌")
	}
	rec := getPluginRecord(pid)
	if rec == nil || !hmac.Equal([]byte(hashToken(token)), []byte(str(rec["token_hash"]))) {
		return nil, NewHTTPError(401, "插件令牌校验失败")
	}
	if !boolOf(rec["enabled"]) {
		return nil, NewHTTPError(403, "插件未启用")
	}
	return rec, nil
}

func requirePluginCapability(rec map[string]any, capability string) error {
	for _, c := range toStringSlice(rec["capabilities"]) {
		if c == capability {
			return nil
		}
	}
	return NewHTTPError(403, "插件未声明 "+capability+" 能力")
}

func loadPluginConfig(id string) map[string]any {
	cfg := map[string]any{}
	_ = ReadJSON(DataPath("plugins", id, "config.json"), &cfg)
	if cfg == nil {
		cfg = map[string]any{}
	}
	return cfg
}

func savePluginConfig(id string, cfg map[string]any) error {
	dir := DataPath("plugins", id)
	if err := EnsureDir(dir); err != nil {
		return err
	}
	return WriteJSONFile(filepath.Join(dir, "config.json"), cfg)
}

// 保留给其他模块可能使用的辅助：插件端口字符串转换为整数。
func pluginPortInt(v any) int {
	n, _ := strconv.Atoi(str(v))
	return n
}
