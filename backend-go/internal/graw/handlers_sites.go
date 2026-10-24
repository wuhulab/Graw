package graw

import (
	"crypto/sha1"
	"encoding/hex"
	"fmt"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// 网站管理：站点 CRUD + nginx/openresty 配置生成 + 真实站点发现。
// 配置格式与 Python 版保持兼容，站点数据存于 data/sites.json。

var (
	confForbiddenRe = regexp.MustCompile(`[\r\n;{}]`)
	siteIDRe        = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,63}$`)
	domainRe        = regexp.MustCompile(`(?i)^(\*\.)?([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}$`)
	subdomainRe     = regexp.MustCompile(`^[A-Za-z0-9*][A-Za-z0-9*-]*$`)
	proxyDomainRe   = regexp.MustCompile(`^[A-Za-z0-9*.-]+$`)
	proxyURLRe      = regexp.MustCompile(`(?i)^https?://[A-Za-z0-9.~:/?#\[\]@!$&'()*+,;=%_-]+$`)
	upstreamRe      = regexp.MustCompile(`^[A-Za-z0-9._-]+:[0-9]{1,5}$`)
	siteTypes       = map[string]bool{"static": true, "proxy": true, "tcpudp": true, "subsite": true}
)

const (
	siteTypeStatic  = "static"
	siteTypeProxy   = "proxy"
	siteTypeTCPUDP  = "tcpudp"
	siteTypeSubsite = "subsite"
)

// --- Web 服务器引擎适配 ---

func webServerMode() string {
	var cfg map[string]any
	if err := ReadJSON(DataPath("webserver.json"), &cfg); err == nil {
		if m := str(cfg["mode"]); m == "openresty" || m == "nginx" {
			return m
		}
	}
	return "nginx"
}

func webServerSetMode(mode string) error {
	mode = strings.ToLower(strings.TrimSpace(mode))
	if mode != "nginx" && mode != "openresty" {
		return NewHTTPError(400, "不支持的 Web 服务器引擎: "+mode+"（仅支持 nginx/openresty）")
	}
	return WriteJSONFile(DataPath("webserver.json"), map[string]any{"mode": mode})
}

func webServerBaseDir() string {
	if webServerMode() == "openresty" {
		return "/usr/local/openresty/nginx/conf"
	}
	return "/etc/nginx"
}

func webServerBinary() string {
	if webServerMode() == "openresty" {
		return "openresty"
	}
	return "nginx"
}

func webServerAvailable(engine string) bool {
	if HostWhich(engine) != "" {
		return true
	}
	res := HostCmd(5*time.Second, engine, "-v")
	if res.Code == 0 {
		return true
	}
	// 容器化引擎（1Panel 等）：检查运行容器镜像名。
	ps := dockerRun(15*time.Second, "ps", "--format", "{{.Image}}")
	if ps.Code == 0 {
		for _, line := range strings.Split(ps.Stdout, "\n") {
			img := strings.ToLower(strings.TrimSpace(line))
			if img != "" && (strings.Contains(img, strings.ToLower(engine)) || strings.Contains(img, "nginx")) {
				return true
			}
		}
	}
	return false
}

func webServerType() string {
	if webServerMode() == "openresty" && webServerAvailable("openresty") {
		return "openresty"
	}
	if webServerAvailable("nginx") || webServerAvailable("openresty") {
		return "nginx"
	}
	if HostWhich("apache2") != "" || HostWhich("httpd") != "" {
		return "apache"
	}
	return "none"
}

func reloadWebServer() bool {
	res := HostCmd(10*time.Second, webServerBinary(), "-s", "reload")
	return res.Code == 0
}

func webServerStatus() map[string]any {
	return map[string]any{
		"mode": webServerMode(), "binary": webServerBinary(),
		"available":           webServerAvailable(webServerBinary()),
		"nginx_available":     webServerAvailable("nginx"),
		"openresty_available": webServerAvailable("openresty"),
		"conf_base":           webServerBaseDir(),
		"config_file":         DataPath("webserver.json"),
	}
}

// --- 站点数据 ---

func loadSites() []map[string]any {
	var sites []map[string]any
	if err := ReadJSON(DataPath("sites.json"), &sites); err != nil {
		return []map[string]any{}
	}
	if sites == nil {
		return []map[string]any{}
	}
	return sites
}

func saveSites(sites []map[string]any) error {
	CaptureSnapshot("sites", "sites", DataPath("sites.json"))
	return WriteJSONFile(DataPath("sites.json"), sites)
}

func loadExternalNames() map[string]string {
	names := map[string]string{}
	var raw map[string]any
	_ = ReadJSON(DataPath("sites_names.json"), &raw)
	for k, v := range raw {
		if s, ok := v.(string); ok {
			names[k] = s
		}
	}
	return names
}

func saveExternalNames(names map[string]string) {
	_ = WriteJSONFile(DataPath("sites_names.json"), names)
}

func normalizeProxy(value string) string {
	v := strings.TrimSpace(value)
	if v != "" && !regexp.MustCompile(`(?i)^https?://`).MatchString(v) {
		v = "http://" + v
	}
	return v
}

func rejectConfInjection(value, field string) *HTTPError {
	if value != "" && confForbiddenRe.MatchString(value) {
		return NewHTTPError(400, "字段 "+field+" 包含非法字符（换行/分号/花括号）")
	}
	return nil
}

func validateConfPath(value, field string) *HTTPError {
	if value == "" {
		return nil
	}
	if err := rejectConfInjection(value, field); err != nil {
		return err
	}
	if !strings.HasPrefix(value, "/") || strings.Contains(value, "..") {
		return NewHTTPError(400, "字段 "+field+" 必须是以 / 开头且不含 .. 的绝对路径")
	}
	return nil
}

func siteStr(site map[string]any, key string) string { return str(site[key]) }

func siteBool(site map[string]any, key string) bool { return boolOf(site[key]) }

func siteInt(site map[string]any, key string, def int) int {
	n := int64Of(site[key])
	if n == 0 {
		return def
	}
	return int(n)
}

func siteDomains(site map[string]any) []string {
	out := []string{}
	if arr, ok := site["domains"].([]any); ok {
		for _, item := range arr {
			if s, ok := item.(string); ok {
				out = append(out, s)
			}
		}
	}
	return out
}

func validateSitePayload(siteType string, domains []string, root, reverseProxy string,
	locations []map[string]any, protocol, upstream, subdomain, domain string, ssl map[string]any) *HTTPError {
	if siteType != "" && !siteTypes[siteType] {
		return NewHTTPError(400, "站点类型非法")
	}
	if len(domains) > 32 {
		return NewHTTPError(400, "domains 必须是最多 32 项的列表")
	}
	loose := siteType == siteTypeProxy || siteType == siteTypeTCPUDP
	for _, d := range domains {
		if loose {
			if !proxyDomainRe.MatchString(d) {
				return NewHTTPError(400, "域名格式非法: "+d)
			}
		} else if !domainRe.MatchString(d) {
			return NewHTTPError(400, "域名格式非法: "+d)
		}
	}
	if err := validateConfPath(root, "root"); err != nil {
		return err
	}
	if reverseProxy != "" {
		if !proxyURLRe.MatchString(reverseProxy) {
			return NewHTTPError(400, "反向代理地址必须形如 http(s)://host[:port][/path]")
		}
		if err := rejectConfInjection(reverseProxy, "reverse_proxy"); err != nil {
			return err
		}
	}
	if len(locations) > 64 {
		return NewHTTPError(400, "locations 必须是最多 64 项的列表")
	}
	for _, loc := range locations {
		path := str(loc["path"])
		if path == "" {
			path = "/"
		}
		if !strings.HasPrefix(path, "/") || confForbiddenRe.MatchString(path) {
			return NewHTTPError(400, "location 路径非法: "+path)
		}
		if err := validateConfPath(str(loc["root"]), "location.root"); err != nil {
			return err
		}
	}
	if protocol != "" && protocol != "tcp" && protocol != "udp" {
		return NewHTTPError(400, "protocol 仅支持 tcp/udp")
	}
	if upstream != "" && !upstreamRe.MatchString(upstream) {
		return NewHTTPError(400, "上游地址必须形如 host:port")
	}
	if subdomain != "" && !subdomainRe.MatchString(subdomain) {
		return NewHTTPError(400, "子域名前缀仅允许字母、数字、中划线（可含 *）")
	}
	if domain != "" && !domainRe.MatchString(domain) {
		return NewHTTPError(400, "根域名格式非法: "+domain)
	}
	for _, key := range []string{"cert", "key"} {
		if err := validateConfPath(str(ssl[key]), "ssl."+key); err != nil {
			return err
		}
	}
	return nil
}

func siteServerName(site map[string]any) string {
	if siteStr(site, "type") == siteTypeSubsite {
		sub, domain := siteStr(site, "subdomain"), siteStr(site, "domain")
		if sub != "" && domain != "" && subdomainRe.MatchString(sub) && domainRe.MatchString(domain) {
			return sub + "." + domain
		}
		if domain != "" && domainRe.MatchString(domain) {
			return "*." + domain
		}
		return "_"
	}
	names := []string{}
	for _, d := range siteDomains(site) {
		if domainRe.MatchString(d) || proxyDomainRe.MatchString(d) {
			names = append(names, d)
		}
	}
	if len(names) == 0 {
		return "_"
	}
	return strings.Join(names, " ")
}

// nginxSiteConfig 生成站点 nginx 配置（与 Python 版格式一致）。
func nginxSiteConfig(site map[string]any, rootOverride string) string {
	siteType := siteStr(site, "type")
	if siteType == "" {
		siteType = siteTypeStatic
	}
	serverName := siteServerName(site)
	root := siteStr(site, "root")
	if rootOverride != "" {
		root = rootOverride
	}
	if root == "" {
		root = "/var/www/html"
	}
	port := siteInt(site, "port", 80)
	ssl := mapOf(site["ssl"])
	enableSSL := boolOf(ssl["enabled"]) && str(ssl["cert"]) != "" && str(ssl["key"]) != ""
	proxy := siteStr(site, "reverse_proxy")
	locations := []map[string]any{}
	if arr, ok := site["locations"].([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok {
				locations = append(locations, m)
			}
		}
	}

	content := []string{}
	if siteType == siteTypeProxy {
		if proxy != "" {
			content = append(content, "    location / {")
			content = append(content, "        proxy_pass "+proxy+";")
			content = append(content, proxySSLOptions(proxy)...)
			content = append(content,
				"        proxy_set_header Host $host;",
				"        proxy_set_header X-Real-IP $remote_addr;",
				"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
				"        proxy_set_header X-Forwarded-Proto $scheme;")
			content = append(content, "    }")
		}
	} else {
		content = append(content, "    root "+root+";")
		content = append(content, "    index index.html index.htm index.php;")
		if proxy != "" {
			content = append(content, "    location / {")
			content = append(content, "        proxy_pass "+proxy+";")
			content = append(content, proxySSLOptions(proxy)...)
			content = append(content, "        proxy_set_header Host $host;")
			content = append(content, "        proxy_set_header X-Real-IP $remote_addr;")
			content = append(content, "    }")
		} else {
			for _, loc := range locations {
				path := str(loc["path"])
				if path == "" {
					path = "/"
				}
				locRoot := str(loc["root"])
				if locRoot == "" {
					locRoot = root
				}
				content = append(content, "    location "+path+" {")
				content = append(content, "        root "+locRoot+";")
				content = append(content, "        try_files $uri $uri/ =404;")
				content = append(content, "    }")
			}
			if len(locations) == 0 {
				if frag := rewriteFragment(siteStr(site, "rewrite")); frag != "" {
					for _, ln := range strings.Split(frag, "\n") {
						content = append(content, "    "+ln)
					}
				} else {
					content = append(content, "    location / {")
					content = append(content, "        try_files $uri $uri/ =404;")
					content = append(content, "    }")
				}
			}
		}
	}
	if ver := strings.TrimSpace(siteStr(site, "php_version")); ver != "" {
		content = append(content,
			`    location ~ \.php$ {`,
			"        fastcgi_pass unix:/run/php/php"+ver+"-fpm.sock;",
			"        fastcgi_index index.php;",
			"        include fastcgi_params;",
			"        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;",
			"    }")
	}
	if id := siteStr(site, "id"); siteIDRe.MatchString(id) && wafSiteActive(id) &&
		Exists(HostPath(webServerBaseDir()+"/waf/"+id+".conf")) {
		content = append(content, "    include "+webServerBaseDir()+"/waf/"+id+".conf;")
	}
	content = append(content, siteOptsExtra(site)...)

	lines := []string{"server {", fmt.Sprintf("    listen %d;", port)}
	if enableSSL && siteInt(ssl, "port", 443) != 0 {
		lines = append(lines, fmt.Sprintf("    listen %d ssl;", siteInt(ssl, "port", 443)))
	}
	lines = append(lines, "    server_name "+serverName+";")
	if enableSSL {
		lines = append(lines, "    ssl_certificate "+str(ssl["cert"])+";")
		lines = append(lines, "    ssl_certificate_key "+str(ssl["key"])+";")
	}
	lines = append(lines, content...)
	lines = append(lines, "}")
	return strings.Join(lines, "\n")
}

func proxySSLOptions(proxy string) []string {
	if !strings.HasPrefix(strings.ToLower(proxy), "https://") {
		return nil
	}
	return []string{
		"        proxy_ssl_verify off;",
		"        proxy_ssl_server_name off;",
		"        proxy_ssl_session_reuse on;",
	}
}

func nginxStreamConfig(site map[string]any) string {
	protocol := siteStr(site, "protocol")
	if protocol != "tcp" && protocol != "udp" {
		protocol = "tcp"
	}
	port := siteInt(site, "port", 443)
	upstream := siteStr(site, "upstream")
	sid := siteStr(site, "id")
	if sid == "" {
		sid = "site"
	}
	lines := []string{
		"stream {",
		"    upstream " + sid + "_upstream {",
		"        server " + upstream + ";",
		"    }",
		"    server {",
		fmt.Sprintf("        listen %d %s;", port, protocol),
		"        proxy_pass " + sid + "_upstream;",
		"    }",
		"}",
	}
	return strings.Join(lines, "\n")
}

func writeHostFile(hostView, content string) error {
	real := HostPath(hostView)
	if err := os.MkdirAll(filepath.Dir(real), 0o755); err != nil {
		return err
	}
	return os.WriteFile(real, []byte(content), 0o644)
}

func ensureStreamInclude() {
	confPath := HostPath(webServerBaseDir() + "/nginx.conf")
	include := "include " + webServerBaseDir() + "/stream-enabled/*.conf;"
	content, err := os.ReadFile(confPath)
	if err != nil || strings.Contains(string(content), include) {
		return
	}
	text := string(content)
	idx := strings.Index(text, "events {")
	if idx < 0 {
		return
	}
	end := strings.Index(text[idx:], "}")
	if end < 0 {
		return
	}
	end += idx + 1
	text = text[:end] + "\n" + include + "\n" + text[end:]
	_ = os.WriteFile(confPath, []byte(text), 0o644)
}

func applyNginxSite(site map[string]any, enabled bool) error {
	id := siteStr(site, "id")
	if id == "" || !siteIDRe.MatchString(id) {
		return NewHTTPError(400, "站点 ID 非法")
	}
	base := webServerBaseDir()
	if siteStr(site, "type") == siteTypeTCPUDP {
		streamPath := base + "/stream-enabled/" + id + ".conf"
		if enabled {
			if err := writeHostFile(streamPath, nginxStreamConfig(site)); err != nil {
				return err
			}
			ensureStreamInclude()
		} else {
			_ = os.Remove(HostPath(streamPath))
		}
		return nil
	}
	available := base + "/sites-available/" + id + ".conf"
	enabledPath := base + "/sites-enabled/" + id + ".conf"
	if enabled {
		rootOverride := ""
		if siteBool(site, "maintenance") {
			rootOverride = maintenanceRoot(site)
		}
		if err := writeHostFile(available, nginxSiteConfig(site, rootOverride)); err != nil {
			return err
		}
		realAvailable, realEnabled := HostPath(available), HostPath(enabledPath)
		_ = os.MkdirAll(filepath.Dir(realEnabled), 0o755)
		_ = os.Remove(realEnabled)
		if err := os.Symlink(realAvailable, realEnabled); err != nil {
			if err := CopyFile(realAvailable, realEnabled); err != nil {
				return err
			}
		}
	} else {
		_ = os.Remove(HostPath(enabledPath))
	}
	return nil
}

func applyApacheSite(site map[string]any, enabled bool) error {
	id := siteStr(site, "id")
	available := "/etc/apache2/sites-available/" + id + ".conf"
	enabledPath := "/etc/apache2/sites-enabled/" + id + ".conf"
	if enabled {
		serverName := siteServerName(site)
		root := siteStr(site, "root")
		proxy := siteStr(site, "reverse_proxy")
		lines := []string{
			"<VirtualHost *:" + itoa(siteInt(site, "port", 80)) + ">",
			"    ServerName " + strings.Split(serverName, " ")[0],
			"    DocumentRoot " + root,
			"    <Directory " + root + ">",
			"        AllowOverride All",
			"        Require all granted",
			"    </Directory>",
		}
		if proxy != "" {
			lines = append(lines,
				"    ProxyPreserveHost On",
				"    ProxyPass / "+proxy+"/",
				"    ProxyPassReverse / "+proxy+"/")
		}
		lines = append(lines, "</VirtualHost>")
		if err := writeHostFile(available, strings.Join(lines, "\n")); err != nil {
			return err
		}
		if err := os.MkdirAll(HostPath("/etc/apache2/sites-enabled"), 0o755); err != nil {
			return err
		}
		_ = os.Symlink(HostPath(available), HostPath(enabledPath))
		HostCmd(10*time.Second, "a2ensite", filepath.Base(available))
		HostCmd(10*time.Second, "apache2ctl", "graceful")
	} else {
		_ = os.Remove(HostPath(enabledPath))
		HostCmd(10*time.Second, "apache2ctl", "graceful")
	}
	return nil
}

func applySiteConfig(site map[string]any, enabled bool) error {
	ws := webServerType()
	switch ws {
	case "nginx", "openresty":
		return applyNginxSite(site, enabled)
	case "apache":
		return applyApacheSite(site, enabled)
	}
	return nil
}

func reloadIfNginx() {
	ws := webServerType()
	if ws == "nginx" || ws == "openresty" {
		reloadWebServer()
	}
}

// maintenanceRoot 准备维护页目录并返回宿主机视角路径。
func maintenanceRoot(site map[string]any) string {
	id := siteStr(site, "id")
	if !siteIDRe.MatchString(id) {
		return ""
	}
	view := DataPath("maintenance", id)
	real := HostPath(view)
	_ = os.MkdirAll(real, 0o755)
	index := filepath.Join(real, "index.html")
	if !Exists(index) {
		_ = os.WriteFile(index, []byte(defaultMaintenanceHTML), 0o644)
	}
	return view
}

const defaultMaintenanceHTML = `<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>维护中</title>
<style>body{font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#0f172a;color:#e2e8f0}
.box{text-align:center;max-width:520px;padding:40px}h1{font-size:48px;margin:0 0 12px}p{opacity:.75}</style>
</head><body><div class="box"><h1>503</h1><p>站点正在维护中，请稍后再访问。</p></div></body></html>`

func saveMaintenanceHTML(id, html string) {
	if !siteIDRe.MatchString(id) {
		return
	}
	dir := HostPath(DataPath("maintenance", id))
	_ = os.MkdirAll(dir, 0o755)
	if strings.TrimSpace(html) == "" {
		html = defaultMaintenanceHTML
	}
	_ = os.WriteFile(filepath.Join(dir, "index.html"), []byte(html), 0o644)
}

func siteOnline(port int) bool {
	conn, err := net.DialTimeout("tcp", "127.0.0.1:"+itoa(port), 300*time.Millisecond)
	if err != nil {
		return false
	}
	_ = conn.Close()
	return true
}

// --- 真实站点发现 ---

func discoverExternalSites() []map[string]any {
	dirs := []string{webServerBaseDir() + "/sites-available", webServerBaseDir() + "/sites-enabled", "/opt/1panel/www/conf.d"}
	seen := map[string]bool{}
	out := []map[string]any{}
	index := 0
	for _, dir := range dirs {
		realDir := HostPath(dir)
		if !IsDir(realDir) {
			continue
		}
		entries, _ := os.ReadDir(realDir)
		for _, e := range entries {
			if e.IsDir() || !strings.HasSuffix(e.Name(), ".conf") {
				continue
			}
			realPath := filepath.Join(realDir, e.Name())
			if st, err := os.Stat(realPath); err != nil || st.IsDir() {
				continue
			}
			resolved := realPath
			if target, err := filepath.EvalSymlinks(realPath); err == nil {
				resolved = target
			}
			if seen[resolved] {
				continue
			}
			seen[resolved] = true
			data, err := os.ReadFile(resolved)
			if err != nil {
				continue
			}
			conf := string(data)
			serverName := parseServerName(conf)
			if serverName == "" {
				continue
			}
			index++
			id := extSiteID(serverName, index)
			enabled := strings.Contains(realPath, "sites-enabled") ||
				(dir == "/opt/1panel/www/conf.d")
			source := "nginx"
			if strings.Contains(dir, "1panel") {
				source = "1panel"
			}
			site := map[string]any{
				"id": id, "name": serverName, "type": siteTypeStatic,
				"domains": []string{serverName}, "root": parseRootDir(conf),
				"port": parseListen(conf), "ssl": map[string]any{},
				"reverse_proxy": parseProxyPass(conf), "locations": []any{},
				"protocol": "tcp", "upstream": "", "subdomain": "", "domain": "",
				"enabled": enabled, "external": true, "source": source,
				"conf_path": dir + "/" + e.Name(), "config_file": dir + "/" + e.Name(),
				"created_at": "", "maintenance": false,
			}
			if siteStr(site, "reverse_proxy") != "" {
				site["type"] = siteTypeProxy
			}
			out = append(out, site)
		}
	}
	return out
}

func extSiteID(serverName string, index int) string {
	base := strings.ToLower(serverName)
	base = regexp.MustCompile(`[^a-z0-9_-]+`).ReplaceAllString(base, "-")
	base = strings.Trim(base, "-")
	if base == "" {
		base = "ext"
	}
	return "ext-" + base + "-" + itoa(index)
}

func parseServerName(conf string) string {
	re := regexp.MustCompile(`server_name\s+([^;]+);`)
	for _, m := range re.FindAllStringSubmatch(conf, -1) {
		names := strings.Fields(m[1])
		if len(names) > 0 {
			return names[0]
		}
	}
	return ""
}

func parseListen(conf string) int {
	re := regexp.MustCompile(`\blisten\s+(?:[^\s:;]+:)?(\d+)`)
	if m := re.FindStringSubmatch(conf); len(m) > 1 {
		n, _ := strconv.Atoi(m[1])
		if n > 0 {
			return n
		}
	}
	return 80
}

func parseRootDir(conf string) string {
	re := regexp.MustCompile(`(?m)^\s*root\s+([^;]+);`)
	if m := re.FindStringSubmatch(conf); len(m) > 1 {
		return strings.TrimSpace(m[1])
	}
	return ""
}

func parseProxyPass(conf string) string {
	re := regexp.MustCompile(`location\s+/\s*\{`)
	idx := re.FindStringIndex(conf)
	search := conf
	if idx != nil {
		search = conf[idx[1]:]
	}
	pre := regexp.MustCompile(`proxy_pass\s+([^;]+);`)
	if m := pre.FindStringSubmatch(search); len(m) > 1 {
		return strings.TrimSpace(m[1])
	}
	return ""
}

func findExternalSite(id string) map[string]any {
	for _, s := range discoverExternalSites() {
		if siteStr(s, "id") == id {
			return s
		}
	}
	return nil
}

func mergedSites() []map[string]any {
	sites := loadSites()
	builtin := map[string]bool{}
	for _, s := range sites {
		for _, d := range siteDomains(s) {
			builtin[strings.ToLower(d)] = true
		}
	}
	names := loadExternalNames()
	for _, ext := range discoverExternalSites() {
		skip := false
		for _, d := range siteDomains(ext) {
			if builtin[strings.ToLower(d)] {
				skip = true
				break
			}
		}
		if skip {
			continue
		}
		if name := names[siteStr(ext, "id")]; name != "" {
			ext["name"] = name
		}
		sites = append(sites, ext)
	}
	return sites
}

// --- 路由 ---

func registerSiteRoutes(a *App) {
	a.Get("/api/sites/list", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		ws := webServerType()
		sites := mergedSites()
		for _, s := range sites {
			s["web_server"] = ws
			s["online"] = siteOnline(siteInt(s, "port", 80))
			if _, ok := s["external"]; !ok {
				s["external"] = false
			}
		}
		return WriteJSON(w, 200, map[string]any{"sites": sites, "web_server": ws})
	})
	a.Post("/api/sites/create", perm("sites"), createSiteHandler)
	a.Post("/api/sites/{site_id}/action", perm("sites"), siteActionHandler)
	a.Get("/api/sites/{site_id}/config", perm("sites"), siteConfigHandler)
	a.Post("/api/sites/{site_id}/update", perm("sites"), siteUpdateHandler)
	a.Post("/api/sites/{site_id}/delete", perm("sites"), siteDeleteHandler)
	a.Post("/api/sites/{site_id}/maintenance", perm("sites"), siteMaintenanceHandler)

	// 引擎模式（webmode 模块）
	a.Get("/api/webmode/status", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, webServerStatus())
	})
	a.Post("/api/webmode/mode", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Mode string `json:"mode"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if err := webServerSetMode(req.Mode); err != nil {
			return err
		}
		return WriteJSON(w, 200, webServerStatus())
	})
}

type sitePayload struct {
	Name         *string          `json:"name"`
	Type         *string          `json:"type"`
	Domains      *[]string        `json:"domains"`
	Root         *string          `json:"root"`
	Port         *int             `json:"port"`
	SSL          map[string]any   `json:"ssl"`
	ReverseProxy *string          `json:"reverse_proxy"`
	Locations    []map[string]any `json:"locations"`
	Protocol     *string          `json:"protocol"`
	Upstream     *string          `json:"upstream"`
	Subdomain    *string          `json:"subdomain"`
	Domain       *string          `json:"domain"`
}

func extractSitePayload(req sitePayload) (string, []string, string, string, []map[string]any, string, string, string, string, map[string]any) {
	siteType := siteTypeStatic
	if req.Type != nil {
		siteType = *req.Type
	}
	domains := []string{}
	if req.Domains != nil {
		domains = *req.Domains
	}
	root, proxy := "", ""
	if req.Root != nil {
		root = *req.Root
	}
	if req.ReverseProxy != nil {
		proxy = normalizeProxy(*req.ReverseProxy)
	}
	protocol, upstream, subdomain, domain := "tcp", "", "", ""
	if req.Protocol != nil {
		protocol = *req.Protocol
	}
	if req.Upstream != nil {
		upstream = *req.Upstream
	}
	if req.Subdomain != nil {
		subdomain = *req.Subdomain
	}
	if req.Domain != nil {
		domain = *req.Domain
	}
	ssl := req.SSL
	if ssl == nil {
		ssl = map[string]any{}
	}
	return siteType, domains, root, proxy, req.Locations, protocol, upstream, subdomain, domain, ssl
}

func createSiteHandler(w http.ResponseWriter, r *http.Request) error {
	var req sitePayload
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	if req.Name == nil || strings.TrimSpace(*req.Name) == "" || len(*req.Name) > 64 {
		return NewHTTPError(422, "站点名称必填且不超过 64 字符")
	}
	name := *req.Name
	siteType, domains, root, proxy, locations, protocol, upstream, subdomain, domain, ssl := extractSitePayload(req)
	if req.Type == nil {
		return NewHTTPError(422, "站点类型必填")
	}
	if req.Port == nil {
		req.Port = new(int)
		*req.Port = 80
	}
	if *req.Port < 1 || *req.Port > 65535 {
		return NewHTTPError(422, "端口需在 1-65535 之间")
	}
	if err := validateSitePayload(siteType, domains, root, proxy, locations, protocol, upstream, subdomain, domain, ssl); err != nil {
		return err
	}
	sites := loadSites()
	for _, s := range sites {
		if siteStr(s, "name") == name {
			return NewHTTPError(400, "Site name already exists")
		}
	}
	siteID := strings.Trim(regexp.MustCompile(`[^a-z0-9_-]+`).ReplaceAllString(strings.ToLower(name), "-"), "-")
	if !siteIDRe.MatchString(siteID) {
		h := sha1.Sum([]byte(name))
		siteID = "site-" + hex.EncodeToString(h[:])[:10]
	}
	for _, s := range sites {
		if siteStr(s, "id") == siteID {
			return NewHTTPError(400, "站点 ID 已存在，请更换名称")
		}
	}
	if (siteType == siteTypeStatic || siteType == siteTypeSubsite) && root != "" {
		_ = os.MkdirAll(HostPath(root), 0o755)
	}
	site := map[string]any{
		"id": siteID, "name": name, "type": siteType, "domains": domains,
		"root": root, "port": *req.Port, "ssl": ssl, "reverse_proxy": proxy,
		"locations": locations, "protocol": protocol, "upstream": upstream,
		"subdomain": subdomain, "domain": domain, "enabled": false,
		"created_at": time.Now().Format("2006-01-02T15:04:05"),
	}
	sites = append(sites, site)
	if err := saveSites(sites); err != nil {
		return err
	}
	return WriteJSON(w, 200, site)
}

func siteActionHandler(w http.ResponseWriter, r *http.Request) error {
	id := PathParam(r, "site_id")
	var req struct {
		Action string `json:"action"`
	}
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	sites := loadSites()
	var site map[string]any
	for _, s := range sites {
		if siteStr(s, "id") == id {
			site = s
			break
		}
	}
	if site == nil {
		return NewHTTPError(404, "Site not found")
	}
	switch req.Action {
	case "enable", "start":
		site["enabled"] = true
		if err := applySiteConfig(site, true); err != nil {
			return err
		}
		reloadIfNginx()
	case "disable", "stop":
		site["enabled"] = false
		if err := applySiteConfig(site, false); err != nil {
			return err
		}
		reloadIfNginx()
	case "restart":
		reloadIfNginx()
	default:
		return NewHTTPError(400, "未知操作: "+req.Action)
	}
	if err := saveSites(sites); err != nil {
		return err
	}
	return WriteJSON(w, 200, map[string]any{"ok": true, "enabled": siteBool(site, "enabled")})
}

func siteConfigHandler(w http.ResponseWriter, r *http.Request) error {
	id := PathParam(r, "site_id")
	var site map[string]any
	for _, s := range loadSites() {
		if siteStr(s, "id") == id {
			site = s
			break
		}
	}
	if site == nil {
		site = findExternalSite(id)
		if site == nil {
			return NewHTTPError(404, "Site not found")
		}
	}
	var config string
	if siteStr(site, "external") == "true" || boolOf(site["external"]) {
		if data, err := os.ReadFile(HostPath(siteStr(site, "conf_path"))); err == nil {
			config = string(data)
		}
	}
	if config == "" {
		switch webServerType() {
		case "nginx", "openresty":
			config = nginxSiteConfig(site, "")
		default:
			config = "# No web server detected on this system."
		}
	}
	return WriteJSON(w, 200, map[string]any{"site": site, "config": config})
}

func siteUpdateHandler(w http.ResponseWriter, r *http.Request) error {
	id := PathParam(r, "site_id")
	var req sitePayload
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	sites := loadSites()
	var site map[string]any
	external := false
	for _, s := range sites {
		if siteStr(s, "id") == id {
			site = s
			break
		}
	}
	if site == nil {
		site = findExternalSite(id)
		if site == nil {
			return NewHTTPError(404, "Site not found")
		}
		external = true
	}
	siteType, domains, root, proxy, locations, protocol, upstream, subdomain, domain, ssl := extractSitePayload(req)
	if req.Type == nil {
		siteType = siteStr(site, "type")
	}
	if req.Domains == nil {
		domains = siteDomains(site)
	}
	if req.Root == nil {
		root = siteStr(site, "root")
	}
	if req.ReverseProxy == nil {
		proxy = siteStr(site, "reverse_proxy")
	}
	if req.Locations == nil {
		if arr, ok := site["locations"].([]any); ok {
			for _, item := range arr {
				if m, ok := item.(map[string]any); ok {
					locations = append(locations, m)
				}
			}
		}
	}
	if req.Protocol == nil {
		protocol = siteStr(site, "protocol")
	}
	if req.Upstream == nil {
		upstream = siteStr(site, "upstream")
	}
	if req.Subdomain == nil {
		subdomain = siteStr(site, "subdomain")
	}
	if req.Domain == nil {
		domain = siteStr(site, "domain")
	}
	if req.SSL == nil {
		ssl = mapOf(site["ssl"])
	}
	if err := validateSitePayload(siteType, domains, root, proxy, locations, protocol, upstream, subdomain, domain, ssl); err != nil {
		return err
	}
	site["type"] = siteType
	if req.Name != nil {
		site["name"] = *req.Name
	}
	site["domains"] = domains
	if req.Root != nil && root != "" {
		_ = os.MkdirAll(HostPath(root), 0o755)
	}
	site["root"] = root
	if req.Port != nil {
		if *req.Port < 1 || *req.Port > 65535 {
			return NewHTTPError(400, "端口需在 1-65535 之间")
		}
		site["port"] = *req.Port
	}
	site["ssl"] = ssl
	site["reverse_proxy"] = proxy
	site["locations"] = locations
	site["protocol"] = protocol
	site["upstream"] = upstream
	site["subdomain"] = subdomain
	site["domain"] = domain
	if external {
		if req.Name != nil {
			names := loadExternalNames()
			if strings.TrimSpace(*req.Name) != "" {
				names[id] = strings.TrimSpace(*req.Name)
			} else {
				delete(names, id)
			}
			saveExternalNames(names)
		}
		if siteBool(site, "enabled") {
			if err := writeHostFile(siteStr(site, "conf_path"), nginxSiteConfig(site, "")); err != nil {
				return err
			}
			reloadIfNginx()
		}
		return WriteJSON(w, 200, site)
	}
	if siteBool(site, "enabled") {
		if err := applySiteConfig(site, true); err != nil {
			return err
		}
		reloadIfNginx()
	}
	if err := saveSites(sites); err != nil {
		return err
	}
	return WriteJSON(w, 200, site)
}

func siteDeleteHandler(w http.ResponseWriter, r *http.Request) error {
	id := PathParam(r, "site_id")
	sites := loadSites()
	var site map[string]any
	kept := []map[string]any{}
	for _, s := range sites {
		if siteStr(s, "id") == id {
			site = s
		} else {
			kept = append(kept, s)
		}
	}
	if site == nil {
		return NewHTTPError(404, "Site not found")
	}
	_ = applySiteConfig(site, false)
	reloadIfNginx()
	if err := saveSites(kept); err != nil {
		return err
	}
	return WriteJSON(w, 200, map[string]any{"ok": true})
}

func siteMaintenanceHandler(w http.ResponseWriter, r *http.Request) error {
	id := PathParam(r, "site_id")
	var req struct {
		Enabled bool    `json:"enabled"`
		HTML    *string `json:"html"`
	}
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	var site map[string]any
	var sites []map[string]any
	inStore := false
	for _, s := range loadSites() {
		if siteStr(s, "id") == id {
			site = s
			inStore = true
		}
	}
	if site == nil {
		site = findExternalSite(id)
		if site == nil {
			return NewHTTPError(404, "Site not found")
		}
	}
	if req.HTML != nil {
		saveMaintenanceHTML(id, *req.HTML)
	}
	site["maintenance"] = req.Enabled
	if !inStore {
		if siteBool(site, "enabled") {
			if err := writeHostFile(siteStr(site, "conf_path"), nginxSiteConfig(site, func() string {
				if req.Enabled {
					return maintenanceRoot(site)
				}
				return ""
			}())); err != nil {
				return err
			}
			reloadIfNginx()
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "external": true, "maintenance": req.Enabled})
	}
	if siteBool(site, "enabled") {
		if err := applySiteConfig(site, true); err != nil {
			return err
		}
		reloadIfNginx()
	}
	sites = loadSites()
	if err := saveSites(sites); err != nil {
		return err
	}
	return WriteJSON(w, 200, map[string]any{"ok": true, "maintenance": req.Enabled})
}
