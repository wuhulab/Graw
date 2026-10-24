package graw

import (
	"crypto/md5"
	"crypto/sha1"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"hash"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

// 登录日志 / FTP 用户 / 工具箱 / 一键体检 / 镜像扫描 / 慢查询。

func registerLoginLogRoutes(a *App) {
	a.Get("/api/loginlog/status", loginOnly(), func(w http.ResponseWriter, r *http.Request) error {
		logs := loadLoginLogs()
		success, failed, abnormal := 0, 0, 0
		for _, l := range logs {
			if l.Status == "success" {
				success++
			} else if l.Status == "failed" {
				failed++
			}
			if l.Abnormal {
				abnormal++
			}
		}
		known := loadKnown()
		cfg, _ := known["_config"].(map[string]any)
		alert := true
		if cfg != nil {
			if v, ok := cfg["alert_enabled"].(bool); ok {
				alert = v
			}
		}
		return WriteJSON(w, 200, map[string]any{
			"alert_enabled": alert, "total": len(logs),
			"success": success, "failed": failed, "abnormal": abnormal,
		})
	})
	a.Get("/api/loginlog/list", perm("loginlog"), func(w http.ResponseWriter, r *http.Request) error {
		limit := QueryInt(r, "limit", 100)
		if limit < 1 {
			limit = 1
		}
		if limit > 500 {
			limit = 500
		}
		username := r.URL.Query().Get("username")
		status := r.URL.Query().Get("status")
		out := []LoginLogEntry{}
		for _, l := range loadLoginLogs() {
			if username != "" && !strings.Contains(l.Username, username) {
				continue
			}
			if (status == "success" || status == "failed") && l.Status != status {
				continue
			}
			out = append(out, l)
			if len(out) >= limit {
				break
			}
		}
		return WriteJSON(w, 200, map[string]any{"logs": out})
	})
	a.Get("/api/loginlog/mine", loginOnly(), func(w http.ResponseWriter, r *http.Request) error {
		u := CurrentUser(r)
		limit := QueryInt(r, "limit", 100)
		out := []LoginLogEntry{}
		for _, l := range loadLoginLogs() {
			if l.Username == u.Username() {
				out = append(out, l)
				if len(out) >= limit {
					break
				}
			}
		}
		return WriteJSON(w, 200, map[string]any{"logs": out})
	})
	a.Post("/api/loginlog/clear", perm("loginlog"), func(w http.ResponseWriter, r *http.Request) error {
		_ = WriteJSONFile(DataPath("login_logs.json"), []LoginLogEntry{})
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Put("/api/loginlog/config", perm("loginlog"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			AlertEnabled bool `json:"alert_enabled"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		known := loadKnown()
		known["_config"] = map[string]any{"alert_enabled": req.AlertEnabled}
		if err := WriteJSONFile(DataPath("login_known.json"), known); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/loginlog/test-alert", perm("loginlog"), func(w http.ResponseWriter, r *http.Request) error {
		msg := "【Graw 异常登录提醒(测试)】账号「admin(测试)」于 " +
			time.Now().Format("2006-01-02 15:04:05") + " 从 " + ClientIP(r) +
			"（浏览器(测试)）登录面板。这是用于验证通知渠道的测试消息，请忽略。"
		sent, failed := 0, 0
		if PushAllFunc != nil {
			sent, failed = PushAllFunc(msg)
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "sent": sent, "failed": failed})
	})
}

// --- FTP 用户 ---

var (
	ftpUsernameRe  = regexp.MustCompile(`^[A-Za-z0-9._-]{1,64}$`)
	ftpDirectoryRe = regexp.MustCompile(`^(?:/|\\\\|[A-Za-z]:[\\/])`)
)

func loadFTPUsers() map[string]any {
	data := map[string]any{"users": []any{}}
	_ = ReadJSON(DataPath("ftp_users.json"), &data)
	return data
}

func ftpUserList(data map[string]any) []map[string]any {
	out := []map[string]any{}
	if arr, ok := data["users"].([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok {
				out = append(out, m)
			}
		}
	}
	return out
}

func publicFTPUser(u map[string]any) map[string]any {
	out := map[string]any{}
	for k, v := range u {
		if k != "password" {
			out[k] = v
		}
	}
	return out
}

func registerFTPUserRoutes(a *App) {
	a.Get("/api/ftpusers", perm("ftpusers"), func(w http.ResponseWriter, r *http.Request) error {
		users := []map[string]any{}
		for _, u := range ftpUserList(loadFTPUsers()) {
			users = append(users, publicFTPUser(u))
		}
		return WriteJSON(w, 200, map[string]any{"users": users})
	})
	a.Post("/api/ftpusers", perm("ftpusers"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Username    string `json:"username"`
			Password    string `json:"password"`
			Directory   string `json:"directory"`
			Enabled     *bool  `json:"enabled"`
			Description string `json:"description"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if !ftpUsernameRe.MatchString(req.Username) {
			return NewHTTPError(400, "用户名仅允许字母/数字/._-，且长度为 1-64 字符")
		}
		if strings.TrimSpace(req.Directory) == "" || len(req.Directory) > 1024 || !ftpDirectoryRe.MatchString(req.Directory) {
			return NewHTTPError(400, "目录必须为绝对路径（如 /srv/ftp 或 C:\\ftp）")
		}
		if len(req.Password) < 6 {
			return NewHTTPError(400, "密码至少 6 位")
		}
		data := loadFTPUsers()
		users := ftpUserList(data)
		for _, u := range users {
			if str(u["username"]) == req.Username {
				return NewHTTPError(400, "用户名已存在")
			}
		}
		hash, err := HashPassword(req.Password)
		if err != nil {
			return err
		}
		enabled := true
		if req.Enabled != nil {
			enabled = *req.Enabled
		}
		user := map[string]any{
			"id": "ftp_" + randomHex(10), "username": req.Username, "password": hash,
			"directory": req.Directory, "enabled": enabled,
			"description": strings.TrimSpace(req.Description),
			"created_at":  time.Now().Format("2006-01-02T15:04:05"),
		}
		data["users"] = append(users, user)
		if err := WriteJSONFile(DataPath("ftp_users.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, publicFTPUser(user))
	})
	a.Put("/api/ftpusers/{user_id}", perm("ftpusers"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "user_id")
		var req struct {
			Username    *string `json:"username"`
			Password    *string `json:"password"`
			Directory   *string `json:"directory"`
			Enabled     *bool   `json:"enabled"`
			Description *string `json:"description"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		data := loadFTPUsers()
		users := ftpUserList(data)
		var user map[string]any
		for _, u := range users {
			if str(u["id"]) == id {
				user = u
				break
			}
		}
		if user == nil {
			return NewHTTPError(404, "FTP 用户不存在")
		}
		if req.Username != nil {
			if !ftpUsernameRe.MatchString(*req.Username) {
				return NewHTTPError(400, "用户名格式非法")
			}
			for _, u := range users {
				if str(u["id"]) != id && str(u["username"]) == *req.Username {
					return NewHTTPError(400, "用户名已存在")
				}
			}
			user["username"] = *req.Username
		}
		if req.Password != nil {
			if len(*req.Password) < 6 {
				return NewHTTPError(400, "密码至少 6 位")
			}
			hash, err := HashPassword(*req.Password)
			if err != nil {
				return err
			}
			user["password"] = hash
		}
		if req.Directory != nil {
			if !ftpDirectoryRe.MatchString(*req.Directory) {
				return NewHTTPError(400, "目录必须为绝对路径")
			}
			user["directory"] = *req.Directory
		}
		if req.Enabled != nil {
			user["enabled"] = *req.Enabled
		}
		if req.Description != nil {
			user["description"] = strings.TrimSpace(*req.Description)
		}
		if err := WriteJSONFile(DataPath("ftp_users.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, publicFTPUser(user))
	})
	a.Delete("/api/ftpusers/{user_id}", perm("ftpusers"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "user_id")
		data := loadFTPUsers()
		users := ftpUserList(data)
		kept := []map[string]any{}
		for _, u := range users {
			if str(u["id"]) != id {
				kept = append(kept, u)
			}
		}
		if len(kept) == len(users) {
			return NewHTTPError(404, "FTP 用户不存在")
		}
		data["users"] = kept
		if err := WriteJSONFile(DataPath("ftp_users.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
}

// --- 工具箱 ---

var toolboxHostRe = regexp.MustCompile(`^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$`)

func registerToolboxRoutes(a *App) {
	a.Get("/api/toolbox/portview", perm("toolbox"), func(w http.ResponseWriter, r *http.Request) error {
		items := []map[string]any{}
		res := HostCmd(10*time.Second, "ss", "-tlnp")
		if res.Code != 0 {
			res = runCommand(10*time.Second, "netstat", "-tlnp")
		}
		for _, line := range strings.Split(res.Stdout, "\n") {
			fields := strings.Fields(line)
			if len(fields) < 4 || (fields[0] != "LISTEN" && !strings.HasPrefix(fields[0], "tcp")) {
				continue
			}
			local := fields[len(fields)-2]
			if strings.HasPrefix(fields[0], "tcp") && len(fields) >= 4 {
				local = fields[3]
			}
			idx := strings.LastIndex(local, ":")
			if idx < 0 {
				continue
			}
			port, _ := strconv.Atoi(local[idx+1:])
			proc := ""
			if len(fields) >= 6 {
				proc = fields[len(fields)-1]
			}
			items = append(items, map[string]any{
				"port": port, "proto": "tcp", "address": local, "process": proc,
			})
		}
		return WriteJSON(w, 200, map[string]any{"ports": items})
	})
	a.Get("/api/toolbox/scripts", perm("toolbox"), func(w http.ResponseWriter, r *http.Request) error {
		var scripts []map[string]any
		_ = ReadJSON(DataPath("toolbox_scripts.json"), &scripts)
		if scripts == nil {
			scripts = []map[string]any{}
		}
		return WriteJSON(w, 200, map[string]any{"scripts": scripts})
	})
	a.Post("/api/toolbox/scripts", perm("toolbox"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Name    string   `json:"name"`
			Desc    string   `json:"desc"`
			Target  string   `json:"target"`
			Content string   `json:"content"`
			Tags    []string `json:"tags"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if strings.TrimSpace(req.Name) == "" || len(req.Name) > 64 {
			return NewHTTPError(422, "脚本名称必填且不超过 64 字符")
		}
		if len(req.Content) > 65536 {
			return NewHTTPError(400, "脚本内容过长")
		}
		var scripts []map[string]any
		_ = ReadJSON(DataPath("toolbox_scripts.json"), &scripts)
		script := map[string]any{
			"id": "script_" + randomHex(8), "name": req.Name, "desc": req.Desc,
			"target": defaultString(req.Target, "host"), "content": req.Content,
			"tags": req.Tags, "created_at": time.Now().Format("2006-01-02T15:04:05"),
		}
		scripts = append(scripts, script)
		if err := WriteJSONFile(DataPath("toolbox_scripts.json"), scripts); err != nil {
			return err
		}
		return WriteJSON(w, 200, script)
	})
	a.Put("/api/toolbox/scripts/{script_id}", perm("toolbox"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "script_id")
		var req map[string]any
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		var scripts []map[string]any
		_ = ReadJSON(DataPath("toolbox_scripts.json"), &scripts)
		var target map[string]any
		for _, s := range scripts {
			if str(s["id"]) == id {
				target = s
				for _, k := range []string{"name", "desc", "target", "content", "tags"} {
					if v, ok := req[k]; ok {
						s[k] = v
					}
				}
			}
		}
		if target == nil {
			return NewHTTPError(404, "脚本不存在")
		}
		if err := WriteJSONFile(DataPath("toolbox_scripts.json"), scripts); err != nil {
			return err
		}
		return WriteJSON(w, 200, target)
	})
	a.Delete("/api/toolbox/scripts/{script_id}", perm("toolbox"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "script_id")
		var scripts []map[string]any
		_ = ReadJSON(DataPath("toolbox_scripts.json"), &scripts)
		kept := []map[string]any{}
		for _, s := range scripts {
			if str(s["id"]) != id {
				kept = append(kept, s)
			}
		}
		if err := WriteJSONFile(DataPath("toolbox_scripts.json"), kept); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/toolbox/exec", perm("toolbox"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Tool string         `json:"tool"`
			Args map[string]any `json:"args"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		result, err := execToolboxTool(strings.ToLower(strings.TrimSpace(req.Tool)), req.Args)
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "tool": req.Tool, "result": result})
	})
}

func execToolboxTool(tool string, args map[string]any) (any, error) {
	text, _ := args["text"].(string)
	switch tool {
	case "base64_encode":
		return base64.StdEncoding.EncodeToString([]byte(text)), nil
	case "base64_decode":
		b, err := base64.StdEncoding.DecodeString(strings.TrimSpace(text))
		if err != nil {
			return nil, NewHTTPError(400, "不是合法的 Base64 数据")
		}
		return string(b), nil
	case "hash":
		algo, _ := args["algorithm"].(string)
		var h hash.Hash
		switch strings.ToLower(algo) {
		case "md5":
			h = md5.New()
		case "sha1":
			h = sha1.New()
		default:
			h = sha256.New()
		}
		h.Write([]byte(text))
		return hex.EncodeToString(h.Sum(nil)), nil
	case "timestamp_to_datetime":
		ts := int64(num(args["timestamp"]))
		t := time.Unix(ts, 0)
		return map[string]any{"local": t.Format("2006-01-02 15:04:05"), "utc": t.UTC().Format("2006-01-02 15:04:05")}, nil
	case "datetime_to_timestamp":
		s, _ := args["datetime"].(string)
		for _, layout := range []string{"2006-01-02 15:04:05", "2006-01-02 15:04", "2006-01-02", "2006/01/02 15:04:05", "2006/01/02"} {
			if t, err := time.ParseInLocation(layout, s, time.Local); err == nil {
				return t.Unix(), nil
			}
		}
		return nil, NewHTTPError(400, "时间格式无法解析")
	case "port_scan":
		host, _ := args["host"].(string)
		if host == "" {
			return nil, NewHTTPError(400, "主机地址不能为空")
		}
		ports := []int{}
		switch v := args["ports"].(type) {
		case []any:
			for _, item := range v {
				ports = append(ports, int(num(item)))
			}
		case []int:
			ports = v
		case string:
			for _, part := range strings.Split(v, ",") {
				if n, err := strconv.Atoi(strings.TrimSpace(part)); err == nil {
					ports = append(ports, n)
				}
			}
		}
		if len(ports) == 0 {
			ports = []int{22, 80, 443, 3306, 6379, 8080}
		}
		if len(ports) > 200 {
			return nil, NewHTTPError(400, "单次最多扫描 200 个端口")
		}
		results := []map[string]any{}
		if remoteActive() {
			// 在远程节点上探测（与 Python 的 host 类语义一致）。
			var script strings.Builder
			script.WriteString("for p in")
			for _, p := range ports {
				if p >= 1 && p <= 65535 {
					script.WriteString(" " + itoa(p))
				}
			}
			script.WriteString("; do timeout 1 bash -c \"exec 3<>/dev/tcp/" + host + "/$p; exec 3>&-\" >/dev/null 2>&1 && echo \"$p:1\" || echo \"$p:0\"; done")
			res := HostShell(60*time.Second, script.String())
			openMap := map[int]bool{}
			for _, line := range strings.Split(res.Stdout, "\n") {
				kv := strings.SplitN(strings.TrimSpace(line), ":", 2)
				if len(kv) == 2 && kv[1] == "1" {
					if n, err := strconv.Atoi(kv[0]); err == nil {
						openMap[n] = true
					}
				}
			}
			for _, p := range ports {
				if p < 1 || p > 65535 {
					continue
				}
				results = append(results, map[string]any{"port": p, "open": openMap[p]})
			}
			return results, nil
		}
		for _, p := range ports {
			if p < 1 || p > 65535 {
				continue
			}
			conn, err := net.DialTimeout("tcp", net.JoinHostPort(host, itoa(p)), 500*time.Millisecond)
			open := err == nil
			if conn != nil {
				conn.Close()
			}
			results = append(results, map[string]any{"port": p, "open": open})
		}
		return results, nil
	case "whois":
		domain, _ := args["domain"].(string)
		if !toolboxHostRe.MatchString(domain) {
			return nil, NewHTTPError(400, "域名格式非法")
		}
		res := HostCmd(10*time.Second, "whois", domain)
		if res.Code != 0 {
			return nil, NewHTTPError(500, "whois 查询失败")
		}
		if len(res.Stdout) > 20000 {
			return res.Stdout[:20000], nil
		}
		return res.Stdout, nil
	case "ping":
		host, _ := args["host"].(string)
		if !toolboxHostRe.MatchString(host) && net.ParseIP(host) == nil {
			return nil, NewHTTPError(400, "主机地址非法")
		}
		var res CmdResult
		if windowsTarget() {
			res = HostCmd(30*time.Second, "ping", "-n", "4", host)
		} else {
			res = HostCmd(30*time.Second, "ping", "-c", "4", host)
		}
		out := res.Stdout + res.Stderr
		if len(out) > 30000 {
			out = out[:30000]
		}
		return out, nil
	case "traceroute":
		host, _ := args["host"].(string)
		if !toolboxHostRe.MatchString(host) && net.ParseIP(host) == nil {
			return nil, NewHTTPError(400, "主机地址非法")
		}
		name := "traceroute"
		if windowsTarget() {
			name = "tracert"
		}
		res := HostCmd(30*time.Second, name, host)
		out := res.Stdout + res.Stderr
		if len(out) > 30000 {
			out = out[:30000]
		}
		return out, nil
	case "dns_lookup":
		host, _ := args["host"].(string)
		if !toolboxHostRe.MatchString(host) {
			return nil, NewHTTPError(400, "域名格式非法")
		}
		qtype, _ := args["type"].(string)
		if qtype == "" {
			qtype = "A"
		}
		res := HostCmd(10*time.Second, "nslookup", "-type="+qtype, host)
		out := res.Stdout + res.Stderr
		if len(out) > 30000 {
			out = out[:30000]
		}
		return out, nil
	case "http_probe":
		raw, _ := args["url"].(string)
		u, err := url.Parse(raw)
		if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.User != nil {
			return nil, NewHTTPError(400, "仅支持 http/https URL 且不允许 userinfo")
		}
		start := time.Now()
		client := &http.Client{Timeout: 15 * time.Second}
		resp, err := client.Get(u.String())
		if err != nil {
			return nil, NewHTTPError(502, err.Error())
		}
		defer resp.Body.Close()
		return map[string]any{
			"status": resp.StatusCode, "elapsed_ms": time.Since(start).Milliseconds(),
			"headers": resp.Header,
		}, nil
	}
	return nil, NewHTTPError(400, "不支持的工具: "+tool)
}

// --- 一键体检 ---

// --- 一键系统体检（与 Python healthcheck.py 检查项 / 文案 / 评分一致） ---

var weakPasswordsHigh = []string{
	"admin", "admin123", "123456", "password", "12345678", "123456789",
	"qwerty", "abc123", "111111",
}

var weakPasswordsFull = append(append([]string{}, weakPasswordsHigh...),
	"1234567890", "password123", "123123", "root", "test", "1234",
	"000000", "666666", "888888", "passw0rd",
)

var suspiciousCronPatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)\brm\s+-rf\s+[~/]`),
	regexp.MustCompile(`(?i)\bcurl\b.*\|\s*(ba)?sh\b`),
	regexp.MustCompile(`(?i)\bwget\b.*\|\s*(ba)?sh\b`),
	regexp.MustCompile(`(?i)\bmkpasswd\b`),
	regexp.MustCompile(`(?i)base64\s+.*-d\b`),
	regexp.MustCompile(`(?i)\bchmod\s+777\b`),
	regexp.MustCompile(`(?i)i?ptables\s+-F\b`),
	regexp.MustCompile(`(?i)>\s*/etc/passwd`),
	regexp.MustCompile(`(?i)python\S*\s+-c\s+['"]import\s+(os|socket|subprocess)`),
}

var sensitiveServicePorts = map[int]string{
	3306: "MySQL", 5432: "PostgreSQL", 6379: "Redis", 27017: "MongoDB",
	9200: "Elasticsearch", 11211: "Memcached", 1433: "SQL Server",
	3389: "RDP", 5900: "VNC", 23: "Telnet",
}

func healthcheckWeakPasswords(users Users) []map[string]any {
	items := []map[string]any{}
	for name, u := range users {
		hash := u.PasswordHash()
		if hash != "" && !strings.HasPrefix(hash, "$2") {
			items = append(items, map[string]any{
				"level": "high", "title": "账号「" + name + "」密码明文存储",
				"detail": "密码字段不是 bcrypt 哈希，一旦数据文件泄露即密码外泄。",
				"advice": "使用「账号管理」重置该账号密码（面板会以 bcrypt 重新哈希）。",
			})
			continue
		}
		if hash == "" {
			continue
		}
		if u.Role() == "admin" && IsDefaultPassword(hash) {
			items = append(items, map[string]any{
				"level": "high", "title": "账号「" + name + "」仍在使用默认密码",
				"detail": "默认密码属于高危隐患，任何知道默认值的人都能登录。",
				"advice": "立即登录并修改为强密码（面板已强制拦截默认密码登录）。",
			})
			continue
		}
		candidates := weakPasswordsHigh
		if u.Role() == "admin" {
			candidates = weakPasswordsFull
		}
		for _, weak := range candidates {
			if VerifyPassword(weak, hash) {
				items = append(items, map[string]any{
					"level":  "medium",
					"title":  "账号「" + name + "」密码过于简单（命中常见弱口令字典）",
					"detail": "该密码命中常见弱口令字典，容易被暴力破解，请尽快更换。",
					"advice": "建议更换为 12 位以上含大小写/数字/符号的强密码。",
				})
				break
			}
		}
	}
	return items
}

func healthcheckLoginLogs() []map[string]any {
	items := []map[string]any{}
	logs := loadLoginLogs()
	if len(logs) == 0 {
		return items
	}
	recent := logs
	if len(recent) > 20 {
		recent = recent[:20]
	}
	failCounts := map[string]int{}
	for _, entry := range recent {
		if entry.Status == "failed" {
			failCounts[entry.Username]++
		}
	}
	for name, cnt := range failCounts {
		if cnt >= 5 {
			items = append(items, map[string]any{
				"level": "high", "title": "检测到针对账号「" + name + "」的暴力破解尝试",
				"detail": "最近 " + itoa(len(recent)) + " 条登录记录中该账号连续失败 " + itoa(cnt) + " 次。",
				"advice": "检查该账号密码强度；面板已内置同 IP+账号 5 次失败锁定 10 分钟。",
			})
		}
	}
	var latest *LoginLogEntry
	for i := range logs {
		if logs[i].Abnormal {
			latest = &logs[i]
			break
		}
	}
	if latest != nil {
		items = append(items, map[string]any{
			"level":  "medium",
			"title":  "存在异常登录记录（" + latest.Time + "，账号「" + latest.Username + "」）",
			"detail": "来源 IP：" + latest.IP + "，原因：" + latest.AbnormalReason,
			"advice": "如非本人操作，建议立即修改密码并在「登录日志」中排查。",
		})
	}
	return items
}

func healthcheckPorts() []map[string]any {
	items := []map[string]any{}
	fw := loadFirewall()
	if !boolOfDefault(fw["enabled"], true) {
		items = append(items, map[string]any{
			"level": "high", "title": "系统防火墙处于关闭状态",
			"detail": "当前未启用防火墙，所有端口均对外暴露，风险较高。",
			"advice": "前往「防火墙」开启防护，并配置必要的放行/拦截规则。",
		})
	}
	rulePorts := map[int]bool{}
	for _, rule := range firewallRules(fw, "port_rules") {
		if p := int(int64Of(rule["port"])); p > 0 {
			rulePorts[p] = true
		}
	}
	for port, name := range sensitiveServicePorts {
		if !rulePorts[port] {
			items = append(items, map[string]any{
				"level":  "medium",
				"title":  "敏感端口 " + itoa(port) + "（" + name + "）未配置防火墙规则",
				"detail": "该端口通常承载数据库/远程管理服务，若确实对外服务建议加白名单限制来源 IP。",
				"advice": "如无需公网访问，建议在防火墙中拦截该端口；如需要，请限制来源 IP。",
			})
		}
	}
	return items
}

func healthcheckCron() []map[string]any {
	items := []map[string]any{}
	for _, task := range loadCronTasks() {
		cmd := str(task["command"])
		name := defaultString(str(task["name"]), defaultString(str(task["id"]), "未命名任务"))
		for _, pat := range suspiciousCronPatterns {
			if pat.MatchString(cmd) {
				level := "medium"
				if strings.Contains(cmd, "rm -rf") || strings.Contains(cmd, "/etc/passwd") {
					level = "high"
				}
				items = append(items, map[string]any{
					"level": level, "title": "计划任务「" + name + "」包含疑似危险命令",
					"detail": "命令片段：" + cropText(cmd, 120),
					"advice": "请确认该任务来源是否可信；如非必要建议停用或移除。",
				})
				break
			}
		}
	}
	return items
}

func healthcheckPanelConfig(users Users) []map[string]any {
	items := []map[string]any{}
	admins := 0
	any2FA := false
	for _, u := range users {
		if u.Role() != "admin" {
			continue
		}
		admins++
		if u.Bool("otp_enabled") {
			any2FA = true
		}
	}
	if admins > 0 && !any2FA {
		items = append(items, map[string]any{
			"level": "low", "title": "管理员账号未开启两步验证（2FA）",
			"detail": "2FA 可显著提升账号安全性，当前没有任何管理员启用 TOTP。",
			"advice": "建议管理员在「账号管理」中开启 2FA，防止密码泄露后被直接登录。",
		})
	}
	return items
}

func registerHealthcheckRoutes(a *App) {
	a.Get("/api/healthcheck/run", perm("healthcheck"), func(w http.ResponseWriter, r *http.Request) error {
		users, _ := LoadUsers()
		items := []map[string]any{}
		items = append(items, healthcheckWeakPasswords(users)...)
		items = append(items, healthcheckLoginLogs()...)
		items = append(items, healthcheckPorts()...)
		items = append(items, healthcheckCron()...)
		items = append(items, healthcheckPanelConfig(users)...)
		score := 100
		summary := map[string]int{"total": len(items), "high": 0, "medium": 0, "low": 0}
		for _, item := range items {
			switch str(item["level"]) {
			case "high":
				score -= 25
				summary["high"]++
			case "medium":
				score -= 10
				summary["medium"]++
			default:
				score -= 3
				summary["low"]++
			}
		}
		if score < 0 {
			score = 0
		}
		return WriteJSON(w, 200, map[string]any{"score": score, "summary": summary, "items": items})
	})
}

// --- 镜像漏洞扫描 ---

// --- Docker 镜像漏洞扫描（本地 advisory 库） ---

var imageRefRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_./:@-]{0,255}$`)

const imagePkgScript = "if command -v dpkg-query >/dev/null 2>&1; then " +
	"dpkg-query -W -f '${Package}\\t${Version}\\n' 2>/dev/null; fi; " +
	"if command -v rpm >/dev/null 2>&1; then " +
	"rpm -qa --queryformat '%{NAME}\\t%{VERSION}\\n' 2>/dev/null; fi"

type versionSegment struct {
	kind int // 0=数字 1=文本
	num  int
	text string
}

func splitVersion(v string) []versionSegment {
	out := []versionSegment{}
	for _, part := range regexp.MustCompile(`[.\-_+]`).Split(strings.ToLower(strings.TrimSpace(v)), -1) {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		if n, err := strconv.Atoi(part); err == nil {
			out = append(out, versionSegment{kind: 0, num: n})
		} else {
			out = append(out, versionSegment{kind: 1, text: part})
		}
	}
	return out
}

func versionCompare(a, b string) int {
	aa, bb := splitVersion(a), splitVersion(b)
	for len(aa) < 10 {
		aa = append(aa, versionSegment{kind: 0, num: 0})
	}
	for len(bb) < 10 {
		bb = append(bb, versionSegment{kind: 0, num: 0})
	}
	for i := 0; i < len(aa) || i < len(bb); i++ {
		var x, y versionSegment
		if i < len(aa) {
			x = aa[i]
		}
		if i < len(bb) {
			y = bb[i]
		}
		if x.kind == y.kind {
			if x.kind == 0 {
				if x.num == y.num {
					continue
				}
				if x.num > y.num {
					return 1
				}
				return -1
			}
			if x.text == y.text {
				continue
			}
			if x.text > y.text {
				return 1
			}
			return -1
		}
		if x.kind < y.kind {
			return 1
		}
		return -1
	}
	return 0
}

var versionConstraintRe = regexp.MustCompile(`^\s*(<=|>=|<|>|=|==)\s*([0-9][A-Za-z0-9.+\-_]*)\s*$`)

func parseVersionConstraint(expr string) (string, string, bool) {
	m := versionConstraintRe.FindStringSubmatch(expr)
	if len(m) != 3 {
		return "", "", false
	}
	op := m[1]
	if op == "==" {
		op = "="
	}
	return op, m[2], true
}

func matchVersionConstraint(installed, expr string) bool {
	op, target, ok := parseVersionConstraint(expr)
	if !ok {
		return false
	}
	r := versionCompare(installed, target)
	switch op {
	case "<=":
		return r <= 0
	case ">=":
		return r >= 0
	case "<":
		return r < 0
	case ">":
		return r > 0
	}
	return r == 0
}

func loadImageAdvisory() []map[string]any {
	var data map[string]any
	_ = ReadJSON(DataPath("advisory.json"), &data)
	out := []map[string]any{}
	if arr, ok := data["packages"].([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok {
				out = append(out, m)
			}
		}
	}
	return out
}

func saveImageAdvisory(packages []map[string]any) error {
	arr := []any{}
	for _, p := range packages {
		arr = append(arr, p)
	}
	return WriteJSONFile(DataPath("advisory.json"), map[string]any{"version": 1, "packages": arr})
}

func importImageAdvisory(packages []any) (map[string]any, error) {
	existing := loadImageAdvisory()
	seen := map[string]bool{}
	for _, e := range existing {
		seen[str(e["name"])+"\x00"+str(e["cve"])] = true
	}
	added := 0
	for _, raw := range packages {
		p, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		name := strings.TrimSpace(str(p["name"]))
		versions := strings.TrimSpace(str(p["versions"]))
		cve := strings.TrimSpace(str(p["cve"]))
		if name == "" || versions == "" || cve == "" {
			continue
		}
		if _, _, ok := parseVersionConstraint(versions); !ok {
			continue
		}
		item := map[string]any{
			"name": strings.ToLower(name), "versions": versions,
			"cve": strings.ToUpper(cve), "severity": strings.ToLower(defaultString(strings.TrimSpace(str(p["severity"])), "unknown")),
			"desc": cropText(strings.TrimSpace(str(p["desc"])), 500),
		}
		key := str(item["name"]) + "\x00" + str(item["cve"])
		if seen[key] {
			continue
		}
		seen[key] = true
		existing = append(existing, item)
		added++
	}
	if err := saveImageAdvisory(existing); err != nil {
		return nil, err
	}
	return map[string]any{"imported": added, "total": len(existing)}, nil
}

func parseImagePackageList(raw string) [][2]string {
	pkgs := [][2]string{}
	seen := map[string]bool{}
	for _, line := range strings.Split(raw, "\n") {
		line = strings.TrimSpace(line)
		if !strings.Contains(line, "\t") {
			continue
		}
		parts := strings.SplitN(line, "\t", 2)
		name, version := strings.TrimSpace(parts[0]), strings.TrimSpace(parts[1])
		if name == "" || version == "" {
			continue
		}
		key := strings.ToLower(name) + "\x00" + version
		if seen[key] {
			continue
		}
		seen[key] = true
		pkgs = append(pkgs, [2]string{name, version})
	}
	return pkgs
}

func scanImage(image string) (map[string]any, error) {
	image = strings.TrimSpace(image)
	if !imageRefRe.MatchString(image) {
		return nil, NewHTTPError(400, "镜像引用非法")
	}
	cache := map[string]any{}
	_ = ReadJSON(DataPath("image_scan_cache.json"), &cache)
	if cached, ok := cache[image].(map[string]any); ok {
		out := map[string]any{}
		for k, v := range cached {
			out[k] = v
		}
		out["cached"] = true
		return out, nil
	}
	res := dockerRun(120*time.Second, "run", "--rm", "--entrypoint", "sh", image, "-c", imagePkgScript)
	if res.Code != 0 {
		errText := strings.TrimSpace(res.Stderr)
		if errText == "" {
			errText = strings.TrimSpace(res.Stdout)
		}
		return nil, NewHTTPError(502, "容器执行失败（exit="+itoa(res.Code)+"）："+cropText(defaultString(errText, "镜像无可用 sh"), 300))
	}
	output := res.Stdout
	truncated := false
	if len(output) > 2*1024*1024 {
		output = output[:2*1024*1024]
		truncated = true
	}
	pkgs := parseImagePackageList(output)
	advisory := loadImageAdvisory()
	findings := []map[string]any{}
	for _, pkg := range pkgs {
		for _, adv := range advisory {
			if strings.ToLower(str(adv["name"])) != strings.ToLower(pkg[0]) {
				continue
			}
			if matchVersionConstraint(pkg[1], str(adv["versions"])) {
				findings = append(findings, map[string]any{
					"pkg": pkg[0], "version": pkg[1], "cve": str(adv["cve"]),
					"severity": defaultString(str(adv["severity"]), "unknown"),
					"desc":     str(adv["desc"]),
				})
			}
		}
	}
	result := map[string]any{
		"scan_id": randomHex(5), "image": image, "scanned_at": time.Now().Format("2006-01-02 15:04:05"),
		"total_pkgs": len(pkgs), "findings": findings, "cached": false,
	}
	if truncated {
		_ = truncated
	}
	cache[image] = result
	if len(cache) > 200 {
		// 保持数量上限（map 顺序不稳定，仅做容量约束）
		keys := make([]string, 0, len(cache))
		for k := range cache {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys[:len(keys)-200] {
			delete(cache, k)
		}
	}
	_ = WriteJSONFile(DataPath("image_scan_cache.json"), cache)
	return result, nil
}

func registerImageSafetyRoutes(a *App) {
	a.Get("/api/imgsafety/advisory", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		packages := loadImageAdvisory()
		return WriteJSON(w, 200, map[string]any{"packages": packages, "total": len(packages)})
	})
	a.Post("/api/imgsafety/advisory/import", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Packages []any `json:"packages"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if len(req.Packages) < 1 || len(req.Packages) > 5000 {
			return NewHTTPError(422, "packages 数量需在 1-5000 之间")
		}
		res, err := importImageAdvisory(req.Packages)
		if err != nil {
			return err
		}
		res["ok"] = true
		return WriteJSON(w, 200, res)
	})
	a.Get("/api/imgsafety/containers", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		items, res := dockerJSONLines("ps", "-a", "--format", "json")
		if res.Code != 0 {
			return WriteJSON(w, 200, map[string]any{"containers": []any{}, "error": "Docker/Podman 不可用"})
		}
		out := []map[string]any{}
		for _, c := range items {
			name := str(c["Names"])
			if name == "" {
				name = str(c["name"])
			}
			image := str(c["Image"])
			if image == "" {
				image = name
			}
			image = strings.TrimPrefix(image, "sha256:")
			out = append(out, map[string]any{"id": str(c["Id"]), "name": name, "image": image})
		}
		return WriteJSON(w, 200, map[string]any{"containers": out})
	})
	a.Post("/api/imgsafety/scan", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Image string `json:"image"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if strings.TrimSpace(req.Image) == "" || len(req.Image) > 256 {
			return NewHTTPError(422, "请指定 1-256 字符的镜像引用")
		}
		result, err := scanImage(req.Image)
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, result)
	})
}

// --- 慢查询分析 ---

func isMySQLFamily(dbType string) bool {
	switch strings.ToLower(strings.TrimSpace(dbType)) {
	case "mysql", "mariadb":
		return true
	}
	return false
}

func registerSlowQueryRoutes(a *App) {
	a.Get("/api/slowquery/connections", perm("database"), func(w http.ResponseWriter, r *http.Request) error {
		mysqlConns := []map[string]any{}
		for _, c := range loadDBConnections() {
			dbType := defaultString(str(c["db_type"]), "mysql")
			if !isMySQLFamily(dbType) {
				continue
			}
			port := int(int64Of(c["port"]))
			if port == 0 {
				port = 3306
			}
			mysqlConns = append(mysqlConns, map[string]any{
				"id": c["id"], "name": defaultString(str(c["name"]), str(c["id"])),
				"db_type": dbType, "host": str(c["host"]), "port": port,
			})
		}
		return WriteJSON(w, 200, map[string]any{"connections": mysqlConns})
	})
	a.Post("/api/slowquery/scan", perm("database"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			ConnectionID string `json:"connection_id"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		conn, err := findDBConnection(req.ConnectionID)
		if err != nil {
			return err
		}
		dbType := defaultString(str(conn["db_type"]), "mysql")
		if !isMySQLFamily(dbType) {
			return NewHTTPError(400, "仅支持 MySQL / MariaDB 连接")
		}
		host := defaultString(str(conn["host"]), "127.0.0.1")
		port := int(int64Of(conn["port"]))
		if port == 0 {
			port = 3306
		}
		if host != "127.0.0.1" && host != "localhost" && host != "::1" && host != "" {
			return WriteJSON(w, 200, map[string]any{
				"enabled": false,
				"hint": "目标 MySQL 位于 " + host + ":" + itoa(port) +
					"，不是当前管理节点的本机服务；请将「当前节点」切换到该数据库所在主机后重试",
				"items": []any{},
			})
		}
		slowOn, logFile, _, verr := queryMySQLSlowVars(conn)
		if verr != nil {
			return NewHTTPError(502, "连接 MySQL 失败，请检查连接配置")
		}
		AuditRecord("慢查询分析", "", "", "conn="+req.ConnectionID)
		if !slowOn {
			return WriteJSON(w, 200, map[string]any{
				"enabled": false,
				"hint":    "慢查询日志未开启。开启方式：SET GLOBAL slow_query_log='ON'; SET GLOBAL long_query_time=1;（需 MySQL 管理员权限，面板不做强改）",
				"items":   []any{},
			})
		}
		if logFile == "" {
			return WriteJSON(w, 200, map[string]any{"enabled": true, "hint": "慢查询日志路径为空，无法读取", "items": []any{}})
		}
		res := HostShell(20*time.Second, "tail -c 8388608 "+shellQuote(logFile)+" 2>/dev/null || echo '__NOFILE__'")
		text := res.Stdout
		if strings.Contains(text, "__NOFILE__") || strings.TrimSpace(text) == "" {
			return WriteJSON(w, 200, map[string]any{
				"enabled": true,
				"hint":    "未找到慢日志文件 " + logFile + "（可能已轮转或无记录）。若刚开启，请等待慢查询产生后再试",
				"items":   []any{},
			})
		}
		items := parseMySQLSlowLog(text)
		if len(items) > 50 {
			items = items[:50]
		}
		return WriteJSON(w, 200, map[string]any{
			"enabled": true, "hint": "", "long_query_time": 0, "items": items,
		})
	})
}

// queryMySQLSlowVars 查询慢日志开关与路径；连接失败时按 Python 语义返回 502。
func queryMySQLSlowVars(conn map[string]any) (bool, string, float64, error) {
	db, err := openSQL(conn, false)
	if err != nil {
		return false, "", 0, err
	}
	defer db.Close()
	slow := false
	logFile := ""
	longQuery := 0.0
	query := func(sql string, handler func(v string)) {
		rows, qerr := db.Query(sql)
		if qerr != nil {
			return
		}
		defer rows.Close()
		for rows.Next() {
			var k, v string
			if rows.Scan(&k, &v) == nil {
				handler(v)
			}
		}
	}
	query("SHOW VARIABLES LIKE 'slow_query_log'", func(v string) { slow = v == "ON" || v == "1" })
	query("SHOW VARIABLES LIKE 'slow_query_log_file'", func(v string) { logFile = v })
	query("SHOW VARIABLES LIKE 'long_query_time'", func(v string) {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			longQuery = f
		}
	})
	return slow, logFile, longQuery, nil
}

var (
	slowQueryTimeRe = regexp.MustCompile(`#\s*Query_time:\s*([0-9.]+)\s+Lock_time:\s*([0-9.]+)\s+Rows_sent:\s*([0-9]+)\s+Rows_examined:\s*([0-9]+)`)
	slowUserHostRe  = regexp.MustCompile(`#\s*User@Host:\s*(\S+)\s*\[([^\]]*)\]`)
	slowTimeRe      = regexp.MustCompile(`#\s*Time:\s*(\S+\s+\S+|\S+)`)
)

func normalizeSlowSQL(sql string) string {
	parts := []string{}
	for _, line := range strings.Split(sql, "\n") {
		if s := strings.TrimSpace(line); s != "" {
			parts = append(parts, s)
		}
	}
	one := strings.Join(parts, " ")
	if len(one) > 500 {
		return one[:500] + "…"
	}
	return one
}

func slowSQLSuggest(sql string, rowsExamined, rowsSent int) string {
	low := strings.ToLower(sql)
	hints := []string{}
	if rowsSent > 0 && rowsExamined > rowsSent*100 {
		hints = append(hints, "扫描行数为返回行数的百倍以上，疑似全表扫描，建议检查索引")
	}
	if strings.Contains(low, "select *") {
		hints = append(hints, "SELECT * 拉取了全部列，建议只选所需字段")
	}
	if strings.Contains(low, "select") && !regexp.MustCompile(`\bwhere\b`).MatchString(low) {
		hints = append(hints, "SELECT 无 WHERE 条件，请确认查询范围")
	}
	if regexp.MustCompile(`select.*(?:\(|\s)select\b`).MatchString(low) {
		hints = append(hints, "存在嵌套子查询，评估可否改写为 JOIN")
	}
	if strings.Contains(low, "order by") && !strings.Contains(low, "limit") {
		hints = append(hints, "ORDER BY 未配合 LIMIT，可能全量排序，建议限制条数")
	}
	return strings.Join(hints, "；")
}

func parseMySQLSlowLog(text string) []map[string]any {
	items := []map[string]any{}
	var cur map[string]any
	sqlHead := ""
	flush := func() {
		if cur != nil && sqlHead != "" {
			cur["sql"] = normalizeSlowSQL(sqlHead)
			cur["suggest"] = slowSQLSuggest(cur["sql"].(string), int(cur["rows_examined"].(int)), int(cur["rows_sent"].(int)))
			items = append(items, cur)
		}
		cur = nil
		sqlHead = ""
	}
	for _, raw := range strings.Split(text, "\n") {
		line := strings.TrimRight(raw, "\r")
		if strings.HasPrefix(line, "#") {
			if sqlHead != "" {
				flush()
			}
			if m := slowTimeRe.FindStringSubmatch(line); len(m) == 2 && cur == nil {
				cur = map[string]any{
					"time": m[1], "user": "", "host": "", "query_time": 0.0,
					"rows_examined": 0, "rows_sent": 0, "sql": "", "suggest": "",
				}
				continue
			}
			if m := slowQueryTimeRe.FindStringSubmatch(line); len(m) == 5 && cur != nil {
				qt, _ := strconv.ParseFloat(m[1], 64)
				cur["query_time"] = qt
				cur["rows_sent"], _ = strconv.Atoi(m[3])
				cur["rows_examined"], _ = strconv.Atoi(m[4])
				continue
			}
			if m := slowUserHostRe.FindStringSubmatch(line); len(m) == 3 && cur != nil {
				cur["user"], cur["host"] = m[1], m[2]
			}
			continue
		}
		if cur == nil {
			continue
		}
		s := strings.TrimSpace(line)
		if s == "" || strings.HasPrefix(s, "SET ") {
			continue
		}
		if sqlHead == "" {
			sqlHead = s
		} else {
			sqlHead += "\n" + s
		}
	}
	if cur != nil && sqlHead != "" {
		flush()
	}
	sort.SliceStable(items, func(i, j int) bool {
		return items[i]["query_time"].(float64) > items[j]["query_time"].(float64)
	})
	return items
}
