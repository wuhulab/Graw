package graw

import (
	"crypto/sha1"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// 站点高级配置：把 1Panel 站点详情页里的常用能力收拢到 site["advanced"]。
// 数据仍保存在 data/sites.json，生成 nginx 配置时由 nginxSiteConfig 统一注入。
//
// 覆盖能力：
//   - 默认文档（index）
//   - 重定向（location = /old { return 301 /new; }）
//   - 密码访问（auth_basic + htpasswd）
//   - 跨域（Access-Control-* 响应头）
//   - 真实 IP（set_real_ip_from / real_ip_header）
//   - 流量限制（limit_req，zones 写入独立 include）
//   - 多上游负载均衡（upstream 块 + proxy_pass）
//   - 站点访问/错误日志（按站点拆分的日志文件）
//   - 原始配置文件在线编辑（nginx -t 校验，失败自动回滚）

var (
	advBasicUserRe  = regexp.MustCompile(`^[A-Za-z0-9._-]{1,32}$`)
	advDocRe        = regexp.MustCompile(`^[A-Za-z0-9._-]{1,64}$`)
	advUpstreamRe   = regexp.MustCompile(`^[A-Za-z0-9._:-]{1,253}:[0-9]{1,5}$`)
	advOriginRe     = regexp.MustCompile(`(?i)^https?://[A-Za-z0-9.*_:-]{1,253}$`)
	advHeaderNameRe = regexp.MustCompile(`^[A-Za-z0-9-]{1,64}$`)
)

func siteAdvancedOf(site map[string]any) map[string]any {
	return mapOf(site["advanced"])
}

func advancedStringList(v any) []string {
	out := []string{}
	seen := map[string]bool{}
	for _, item := range toStringSlice(v) {
		s := strings.TrimSpace(item)
		// 去重：重复的 CORS 来源会生成重复的 map key，nginx 直接报配置错误。
		if s == "" || seen[s] {
			continue
		}
		seen[s] = true
		out = append(out, s)
	}
	return out
}

// validateAdvancedPayload 校验并规范化站点高级配置。
// 返回的 map 可直接写入 site["advanced"]；密码只保留 hash，不回传明文。
func validateAdvancedPayload(raw map[string]any, existing map[string]any) (map[string]any, *HTTPError) {
	out := map[string]any{}

	// ---- 默认文档 ----
	docs := advancedStringList(raw["default_docs"])
	if len(docs) == 0 {
		docs = []string{"index.html", "index.htm"}
	}
	if len(docs) > 20 {
		return nil, NewHTTPError(400, "默认文档最多 20 个")
	}
	for _, d := range docs {
		if !advDocRe.MatchString(d) {
			return nil, NewHTTPError(400, "默认文档名称非法: "+d)
		}
	}
	out["default_docs"] = docs

	// ---- 重定向 ----
	redirects := []map[string]any{}
	if arr, ok := raw["redirects"].([]any); ok {
		if len(arr) > 50 {
			return nil, NewHTTPError(400, "重定向规则最多 50 条")
		}
		for _, item := range arr {
			m, ok := item.(map[string]any)
			if !ok {
				continue
			}
			from := strings.TrimSpace(str(m["from"]))
			to := strings.TrimSpace(str(m["to"]))
			code := intOf(m["code"])
			if code == 0 {
				code = 301
			}
			if code != 301 && code != 302 && code != 307 && code != 308 {
				return nil, NewHTTPError(400, "重定向状态码仅支持 301/302/307/308")
			}
			// 引号与 # 会被直接拼进 nginx 配置：引号可截断 return 语句，
			// # 会注释掉收尾的 ; } 导致整份配置失效，必须一并拒绝。
			if from == "" || !strings.HasPrefix(from, "/") || strings.ContainsAny(from, " \t\r\n;{}\"'#`") {
				return nil, NewHTTPError(400, "重定向来源必须是 / 开头的路径，且不能包含空白/分号/花括号/引号/#")
			}
			if to == "" || strings.ContainsAny(to, " \t\r\n;{}\"'#`") {
				return nil, NewHTTPError(400, "重定向目标不能为空，且不能包含空白/分号/花括号/引号/#")
			}
			if !strings.HasPrefix(to, "/") && !strings.HasPrefix(strings.ToLower(to), "http://") && !strings.HasPrefix(strings.ToLower(to), "https://") {
				return nil, NewHTTPError(400, "重定向目标必须是站内路径或 http(s) 地址")
			}
			redirects = append(redirects, map[string]any{"from": from, "to": to, "code": code})
		}
	}
	out["redirects"] = redirects

	// ---- 密码访问 ----
	basic := mapOf(raw["basic_auth"])
	basicOut := map[string]any{"enabled": boolOf(basic["enabled"])}
	oldHash := ""
	if old := mapOf(existing["basic_auth"]); old != nil {
		oldHash = str(old["password_hash"])
	}
	if basicOut["enabled"] == true {
		user := strings.TrimSpace(str(basic["username"]))
		if !advBasicUserRe.MatchString(user) {
			return nil, NewHTTPError(400, "访问用户名仅允许字母、数字、点、下划线、中划线（1-32 位）")
		}
		password := str(basic["password"])
		if password == "" && oldHash != "" && user == str(mapOf(existing["basic_auth"])["username"]) {
			basicOut["password_hash"] = oldHash
		} else {
			if len(password) < 6 || len(password) > 64 || strings.ContainsAny(password, "\r\n") {
				return nil, NewHTTPError(400, "访问密码长度需为 6-64 位且不能包含换行")
			}
			basicOut["password_hash"] = htpasswdSHA(password)
		}
		basicOut["username"] = user
	} else {
		basicOut["username"] = ""
	}
	out["basic_auth"] = basicOut

	// ---- 跨域 ----
	cors := mapOf(raw["cors"])
	corsOut := map[string]any{"enabled": boolOf(cors["enabled"])}
	origins := advancedStringList(cors["origins"])
	if len(origins) > 20 {
		return nil, NewHTTPError(400, "允许来源最多 20 个")
	}
	for _, o := range origins {
		if o != "*" && !advOriginRe.MatchString(o) {
			return nil, NewHTTPError(400, "跨域来源格式非法: "+o)
		}
	}
	methodSet := map[string]bool{"GET": true, "POST": true, "PUT": true, "DELETE": true, "PATCH": true, "HEAD": true, "OPTIONS": true}
	methods := []string{}
	for _, m := range advancedStringList(cors["methods"]) {
		u := strings.ToUpper(m)
		if !methodSet[u] {
			return nil, NewHTTPError(400, "跨域方法非法: "+m)
		}
		methods = append(methods, u)
	}
	if len(methods) == 0 {
		methods = []string{"GET", "POST", "OPTIONS"}
	}
	headers := []string{}
	for _, h := range advancedStringList(cors["headers"]) {
		if !advHeaderNameRe.MatchString(h) {
			return nil, NewHTTPError(400, "跨域请求头名称非法: "+h)
		}
		headers = append(headers, h)
	}
	if len(headers) == 0 {
		headers = []string{"Content-Type", "Authorization"}
	}
	corsOut["origins"] = origins
	corsOut["methods"] = methods
	corsOut["headers"] = headers
	corsOut["credentials"] = boolOf(cors["credentials"])
	out["cors"] = corsOut

	// ---- 真实 IP ----
	real := mapOf(raw["real_ip"])
	realOut := map[string]any{"enabled": boolOf(real["enabled"])}
	header := strings.TrimSpace(str(real["header"]))
	if header == "" {
		header = "X-Forwarded-For"
	}
	if !advHeaderNameRe.MatchString(header) {
		return nil, NewHTTPError(400, "真实 IP 请求头名称非法")
	}
	trusted := advancedStringList(real["trusted"])
	if len(trusted) > 50 {
		return nil, NewHTTPError(400, "可信代理地址最多 50 个")
	}
	for _, c := range trusted {
		if net.ParseIP(c) == nil {
			if _, _, err := net.ParseCIDR(c); err != nil {
				return nil, NewHTTPError(400, "可信代理必须是 IP 或 CIDR: "+c)
			}
		}
	}
	realOut["header"] = header
	realOut["trusted"] = trusted
	out["real_ip"] = realOut

	// ---- 流量限制 ----
	rate := mapOf(raw["rate_limit"])
	rateOut := map[string]any{"enabled": boolOf(rate["enabled"])}
	rps := intOf(rate["rps"])
	burst := intOf(rate["burst"])
	if rateOut["enabled"] == true {
		if rps <= 0 || rps > 10000 {
			return nil, NewHTTPError(400, "限流速率需为 1-10000 次/秒")
		}
		if burst < 0 || burst > 100000 {
			return nil, NewHTTPError(400, "限流突发值非法")
		}
	}
	rateOut["rps"] = rps
	rateOut["burst"] = burst
	out["rate_limit"] = rateOut

	// ---- 多上游负载均衡 ----
	ups := []map[string]any{}
	if arr, ok := raw["upstreams"].([]any); ok {
		if len(arr) > 32 {
			return nil, NewHTTPError(400, "上游节点最多 32 个")
		}
		for _, item := range arr {
			m, ok := item.(map[string]any)
			if !ok {
				continue
			}
			server := strings.TrimSpace(str(m["server"]))
			if !advUpstreamRe.MatchString(server) {
				return nil, NewHTTPError(400, "上游地址必须是 host:port: "+server)
			}
			weight := intOf(m["weight"])
			if weight <= 0 || weight > 100 {
				weight = 1
			}
			ups = append(ups, map[string]any{"server": server, "weight": weight})
		}
	}
	out["upstreams"] = ups

	// ---- 站点日志 ----
	logging := mapOf(raw["logging"])
	out["logging"] = map[string]any{"enabled": boolOf(logging["enabled"])}

	return out, nil
}

func htpasswdSHA(password string) string {
	sum := sha1.Sum([]byte(password))
	return "{SHA}" + base64.StdEncoding.EncodeToString(sum[:])
}

func siteAdvancedPath(site map[string]any) (string, string) {
	id := siteStr(site, "id")
	base := webServerBaseDir()
	if siteStr(site, "type") == siteTypeTCPUDP {
		return base + "/stream-enabled/" + id + ".conf", "stream"
	}
	return base + "/sites-available/" + id + ".conf", "http"
}

// siteAdvancedServerLines 返回要注入 server {} 的配置行。
func siteAdvancedServerLines(site map[string]any) []string {
	adv := siteAdvancedOf(site)
	if len(adv) == 0 {
		return nil
	}
	id := siteStr(site, "id")
	if !siteIDRe.MatchString(id) {
		return nil
	}
	lines := []string{}

	// 站点日志：拆到独立文件，便于「站点配置中心 -> 日志」直接读取。
	if logging := mapOf(adv["logging"]); boolOf(logging["enabled"]) {
		lines = append(lines,
			"    access_log /var/log/nginx/graw_"+id+".access.log;",
			"    error_log /var/log/nginx/graw_"+id+".error.log;")
	}

	if basic := mapOf(adv["basic_auth"]); boolOf(basic["enabled"]) && str(basic["username"]) != "" && str(basic["password_hash"]) != "" {
		lines = append(lines,
			`    auth_basic "Restricted";`,
			"    auth_basic_user_file "+webServerBaseDir()+"/graw-auth/"+id+".htpasswd;")
	}

	if cors := mapOf(adv["cors"]); boolOf(cors["enabled"]) {
		origins := advancedStringList(cors["origins"])
		if len(origins) == 0 {
			origins = []string{"*"}
		}
		// 配置里出现 * 时其余来源没有意义，统一按通配处理。
		for _, o := range origins {
			if o == "*" {
				origins = []string{"*"}
				break
			}
		}
		allowOrigin := origins[0]
		if len(origins) > 1 {
			// 多来源不能直接反射 $http_origin：那样等于放行任意站点，
			// 白名单形同虚设。改由 writeSiteZones 生成 http 上下文的 map，
			// 未命中白名单时变量为空，nginx 会自动省略该响应头。
			allowOrigin = "$" + siteCORSVar(id)
			lines = append(lines, "    add_header Vary Origin always;")
		}
		lines = append(lines,
			"    add_header Access-Control-Allow-Origin \""+allowOrigin+"\" always;",
			"    add_header Access-Control-Allow-Methods \""+strings.Join(advancedStringList(cors["methods"]), ", ")+"\" always;",
			"    add_header Access-Control-Allow-Headers \""+strings.Join(advancedStringList(cors["headers"]), ", ")+"\" always;")
		if boolOf(cors["credentials"]) {
			lines = append(lines, "    add_header Access-Control-Allow-Credentials \"true\" always;")
		}
	}

	if real := mapOf(adv["real_ip"]); boolOf(real["enabled"]) {
		for _, c := range advancedStringList(real["trusted"]) {
			lines = append(lines, "    set_real_ip_from "+c+";")
		}
		header := str(real["header"])
		if header == "" {
			header = "X-Forwarded-For"
		}
		lines = append(lines, "    real_ip_header "+header+";", "    real_ip_recursive on;")
	}

	if rate := mapOf(adv["rate_limit"]); boolOf(rate["enabled"]) && intOf(rate["rps"]) > 0 {
		burst := intOf(rate["burst"])
		line := "    limit_req zone=graw_site_" + id + " burst=" + strconv.Itoa(burst) + " nodelay;"
		lines = append(lines, line, "    limit_req_status 429;")
	}

	// 重定向放在最后，避免和静态 location 顺序冲突（精确匹配优先于普通 location）。
	if arr, ok := adv["redirects"].([]any); ok {
		for _, item := range arr {
			m, ok := item.(map[string]any)
			if !ok {
				continue
			}
			from := str(m["from"])
			to := str(m["to"])
			code := intOf(m["code"])
			if from == "" || to == "" {
				continue
			}
			lines = append(lines, "    location = "+from+" { return "+strconv.Itoa(code)+" "+to+"; }")
		}
	}

	return lines
}

// siteAdvancedUpstream 返回 upstream 块和 proxy_pass 目标。
func siteAdvancedUpstream(site map[string]any) (string, string) {
	id := siteStr(site, "id")
	if !siteIDRe.MatchString(id) {
		return "", ""
	}
	adv := siteAdvancedOf(site)
	ups := []map[string]any{}
	if arr, ok := adv["upstreams"].([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok && str(m["server"]) != "" {
				ups = append(ups, m)
			}
		}
	}
	if len(ups) == 0 {
		return "", ""
	}
	name := "graw_site_" + id
	lines := []string{"upstream " + name + " {"}
	for _, u := range ups {
		weight := intOf(u["weight"])
		if weight <= 0 {
			weight = 1
		}
		lines = append(lines, "    server "+str(u["server"])+" weight="+strconv.Itoa(weight)+";")
	}
	lines = append(lines, "}")
	return strings.Join(lines, "\n"), "http://" + name
}

// siteCORSVar 生成站点 CORS 白名单的 nginx 变量名。
// 变量名不允许包含 '-'（站点 ID 允许），因此用 ID 的 SHA-256 前缀保证唯一。
// 这里只是生成变量名、不涉及敏感数据，用现代哈希避免误报弱算法。
func siteCORSVar(id string) string {
	sum := sha256.Sum256([]byte(id))
	return "graw_cors_" + hex.EncodeToString(sum[:4])
}

// corsOriginPattern 把来源转换为 map 的正则键：转义正则元字符，
// 通配符 * 按「任意子域名标签」处理。
func corsOriginPattern(origin string) string {
	var b strings.Builder
	for _, r := range origin {
		switch r {
		case '.', '+', '?', '(', ')', '[', ']', '{', '}', '^', '$', '|', '\\':
			b.WriteByte('\\')
			b.WriteRune(r)
		case '*':
			b.WriteString(`[^/]+`)
		default:
			b.WriteRune(r)
		}
	}
	return b.String()
}

// writeSiteZones 汇总所有站点的限流 zone 与 CORS 白名单到 http 上下文 include 文件。
func writeSiteZones() {
	lines := []string{"# Managed by Graw - site zones and CORS maps (http context)", ""}
	any := false
	for _, s := range loadSites() {
		id := siteStr(s, "id")
		if !siteIDRe.MatchString(id) {
			continue
		}
		adv := siteAdvancedOf(s)
		if rate := mapOf(adv["rate_limit"]); boolOf(rate["enabled"]) {
			if rps := intOf(rate["rps"]); rps > 0 {
				lines = append(lines, "limit_req_zone $binary_remote_addr zone=graw_site_"+id+":10m rate="+strconv.Itoa(rps)+"r/s;")
				any = true
			}
		}
		// 多来源 CORS 需要 http 上下文的 map 做精确白名单；
		// 单来源或通配在 server 块里直接写固定值即可。
		if cors := mapOf(adv["cors"]); boolOf(cors["enabled"]) {
			origins := advancedStringList(cors["origins"])
			hasStar := false
			for _, o := range origins {
				if o == "*" {
					hasStar = true
					break
				}
			}
			if len(origins) > 1 && !hasStar {
				lines = append(lines, "map $http_origin $"+siteCORSVar(id)+" {")
				lines = append(lines, `    default "";`)
				for _, o := range origins {
					if strings.Contains(o, "*") {
						lines = append(lines, "    ~*^"+corsOriginPattern(o)+"$ $http_origin;")
					} else {
						lines = append(lines, `    "`+o+`" $http_origin;`)
					}
				}
				lines = append(lines, "}")
				any = true
			}
		}
	}
	lines = append(lines, "")
	base := webServerBaseDir()
	_ = os.MkdirAll(HostPath(base), 0o755)
	_ = writeHostFile(base+"/graw-site-zones.conf", strings.Join(lines, "\n"))
	if any {
		ensureSiteZonesInclude()
	}
}

func ensureSiteZonesInclude() {
	confPath := HostPath(webServerBaseDir() + "/nginx.conf")
	content, err := os.ReadFile(confPath)
	if err != nil {
		return
	}
	include := "include " + webServerBaseDir() + "/graw-site-zones.conf;"
	lines := strings.Split(string(content), "\n")
	// include 必须位于 http 上下文。旧实现用「http { 之后的第一个 }」定位，
	// 一旦 http 块里有 server/map 等嵌套块就会插错位置（nginx -t 失败）。
	httpLine := -1
	for i, ln := range lines {
		if strings.Contains(ln, "http {") || strings.Contains(ln, "http{") {
			httpLine = i
			break
		}
	}
	if httpLine < 0 {
		return
	}
	// 已经紧跟在 http 行之后：无需改动，避免每次都重写 nginx.conf。
	if httpLine+1 < len(lines) && strings.TrimSpace(lines[httpLine+1]) == include {
		return
	}
	// 先剔除旧版可能插到嵌套块里的 include 行，再统一插到 http 行之后。
	cleaned := make([]string, 0, len(lines)+1)
	for _, ln := range lines {
		if strings.TrimSpace(ln) == include {
			continue
		}
		cleaned = append(cleaned, ln)
	}
	for i, ln := range cleaned {
		if strings.Contains(ln, "http {") || strings.Contains(ln, "http{") {
			cleaned = append(cleaned[:i+1], append([]string{"    " + include}, cleaned[i+1:]...)...)
			break
		}
	}
	_ = os.WriteFile(confPath, []byte(strings.Join(cleaned, "\n")), 0o644)
}

// writeSiteAuthFile 生成 nginx htpasswd 文件。
func writeSiteAuthFile(site map[string]any) error {
	adv := siteAdvancedOf(site)
	basic := mapOf(adv["basic_auth"])
	if !boolOf(basic["enabled"]) {
		return nil
	}
	user, hash := str(basic["username"]), str(basic["password_hash"])
	if user == "" || hash == "" {
		return nil
	}
	dir := webServerBaseDir() + "/graw-auth"
	_ = os.MkdirAll(HostPath(dir), 0o755)
	return writeHostFile(dir+"/"+siteStr(site, "id")+".htpasswd", user+":"+hash+"\n")
}

func registerSiteAdvancedRoutes(a *App) {
	a.Get("/api/sites/{site_id}/advanced", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		site := findSiteByID(PathParam(r, "site_id"))
		if site == nil {
			return NewHTTPError(404, "Site not found")
		}
		adv := siteAdvancedOf(site)
		out := map[string]any{}
		for k, v := range adv {
			out[k] = v
		}
		// 密码只回传「已设置」标记，避免明文/哈希泄露到前端。
		basic := mapOf(out["basic_auth"])
		basicOut := map[string]any{"enabled": boolOf(basic["enabled"]), "username": str(basic["username"]), "password_set": str(basic["password_hash"]) != ""}
		out["basic_auth"] = basicOut
		return WriteJSON(w, 200, map[string]any{"advanced": out, "site": site})
	})

	a.Post("/api/sites/{site_id}/advanced", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "site_id")
		var raw map[string]any
		if err := DecodeJSON(r, &raw); err != nil {
			return err
		}
		sites := loadSites()
		var site map[string]any
		idx := -1
		for i, s := range sites {
			if siteStr(s, "id") == id {
				site, idx = s, i
				break
			}
		}
		if site == nil {
			return NewHTTPError(404, "Site not found")
		}
		adv, herr := validateAdvancedPayload(raw, siteAdvancedOf(site))
		if herr != nil {
			return herr
		}
		site["advanced"] = adv
		sites[idx] = site
		if err := saveSites(sites); err != nil {
			return err
		}
		if err := writeSiteAuthFile(site); err != nil {
			return err
		}
		writeSiteZones()
		if err := applySiteConfig(site, siteBool(site, "enabled")); err != nil {
			return err
		}
		if err := reloadIfNginxStrict(); err != nil {
			return NewHTTPError(500, "配置已保存，但 Web 服务器重载失败："+err.Error())
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})

	a.Get("/api/sites/{site_id}/logs", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		site := findSiteByID(PathParam(r, "site_id"))
		if site == nil {
			return NewHTTPError(404, "Site not found")
		}
		if !siteIDRe.MatchString(siteStr(site, "id")) {
			return NewHTTPError(400, "站点 ID 非法")
		}
		typ := r.URL.Query().Get("type")
		if typ != "error" {
			typ = "access"
		}
		lines := QueryInt(r, "lines", 200)
		if lines < 1 {
			lines = 1
		}
		if lines > 2000 {
			lines = 2000
		}
		path := "/var/log/nginx/graw_" + siteStr(site, "id") + "." + typ + ".log"
		if !HostExists(path) {
			return WriteJSON(w, 200, map[string]any{"path": path, "lines": []string{}, "exists": false})
		}
		res := HostCmd(20*time.Second, "tail", "-n", strconv.Itoa(lines), "--", path)
		text := strings.TrimRight(res.Stdout, "\n")
		arr := []string{}
		if text != "" {
			arr = strings.Split(text, "\n")
		}
		return WriteJSON(w, 200, map[string]any{"path": path, "lines": arr, "exists": true})
	})

	a.Post("/api/sites/{site_id}/config/raw", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		site := findSiteByID(PathParam(r, "site_id"))
		if site == nil {
			return NewHTTPError(404, "Site not found")
		}
		if boolOf(site["external"]) {
			return NewHTTPError(400, "外部接管的站点不支持在线编辑，避免破坏原面板配置")
		}
		ws := webServerType()
		if ws != "nginx" && ws != "openresty" {
			return NewHTTPError(400, "当前未检测到 Nginx/OpenResty，无法在线编辑配置")
		}
		var req struct {
			Content string `json:"content"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if len(req.Content) == 0 || len(req.Content) > 1024*1024 {
			return NewHTTPError(400, "配置内容为空或超过 1MB")
		}
		if strings.Contains(req.Content, "\x00") {
			return NewHTTPError(400, "配置内容包含非法字符")
		}
		confPath, _ := siteAdvancedPath(site)
		real := HostPath(confPath)
		old, _ := os.ReadFile(real)
		if err := os.MkdirAll(filepath.Dir(real), 0o755); err != nil {
			return err
		}
		if err := os.WriteFile(real, []byte(req.Content), 0o644); err != nil {
			return err
		}
		if err := reloadWebServer(); err != nil {
			if len(old) > 0 {
				_ = os.WriteFile(real, old, 0o644)
				_ = reloadWebServer()
			}
			return NewHTTPError(400, "配置校验失败，已回滚："+err.Error())
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
}

func findSiteByID(id string) map[string]any {
	for _, s := range loadSites() {
		if siteStr(s, "id") == id {
			return s
		}
	}
	return nil
}
