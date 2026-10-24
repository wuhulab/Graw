package graw

import (
	"crypto/x509"
	"encoding/pem"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

// 站点扩展：SSL 证书、伪静态、站点增强（防盗链/gzip/缓存）、PHP 版本、访问统计、WAF。

var rewriteTemplates = map[string]map[string]any{
	"wordpress":   {"name": "WordPress", "content": "location / {\n    try_files $uri $uri/ /index.php?$args;\n}"},
	"laravel":     {"name": "Laravel", "content": "location / {\n    try_files $uri $uri/ /index.php?$query_string;\n}"},
	"thinkphp":    {"name": "ThinkPHP", "content": "location / {\n    if (!-e $request_filename) {\n        rewrite ^(.*)$ /index.php?s=$1 last;\n    }\n}"},
	"typecho":     {"name": "Typecho", "content": "location / {\n    if (!-e $request_filename) {\n        rewrite ^(.*)$ /index.php$1 last;\n    }\n}"},
	"discuz":      {"name": "Discuz", "content": "location / {\n    rewrite ^([^\\.]*)/topic-(.+)\\.html$ /portal.php?mod=topic&topic=$2 last;\n    rewrite ^([^\\.]*)/([0-9]+)-(.*)\\.html$ /index.php?$1=$2 last;\n}"},
	"dedecms":     {"name": "DedeCMS", "content": "location / {\n    rewrite ^/list-([0-9]+)-([0-9]+)\\.html$ /plus/list.php?tid=$1&PageNo=$2 last;\n    rewrite ^/view-([0-9]+)-([0-9]+)\\.html$ /plus/view.php?aid=$1&pageno=$2 last;\n}"},
	"codeigniter": {"name": "CodeIgniter", "content": "location / {\n    try_files $uri $uri/ /index.php?$query_string;\n}"},
	"yii":         {"name": "Yii2", "content": "location / {\n    try_files $uri $uri/ /index.php?$args;\n}"},
	"symfony":     {"name": "Symfony", "content": "location / {\n    try_files $uri /index.php$is_args$args;\n}"},
	"drupal":      {"name": "Drupal", "content": "location / {\n    try_files $uri @rewrite;\n}\nlocation @rewrite {\n    rewrite ^/(.*)$ /index.php?q=$1 last;\n}"},
	"joomla":      {"name": "Joomla", "content": "location / {\n    try_files $uri $uri/ /index.php?$args;\n}"},
	"zblog":       {"name": "Z-Blog", "content": "location / {\n    try_files $uri $uri/ /index.php?$args;\n}"},
}

func rewriteFragment(id string) string {
	t, ok := rewriteTemplates[id]
	if !ok {
		return ""
	}
	return str(t["content"])
}

// 静态资源扩展名白名单（防盗链 / 缓存匹配目标），与 Python sitesopts 保持一致。
const optsStaticExts = "jpg|jpeg|png|gif|webp|bmp|ico|svg|css|js|mjs|mp3|mp4|webm|zip|rar|7z" +
	"|woff|woff2|ttf|eot|pdf|doc|docx|xls|xlsx"

// 缓存时长上限：7 天（秒），与 Python MAX_CACHE_SECONDS 一致。
const optsMaxCacheSeconds = 86400 * 7

var optsDomainRe = regexp.MustCompile(`^(\*\.)?([A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$`)

// boolOfDefault 取值缺失时回退默认值（用于 allow_empty_referer 默认 True）。
func boolOfDefault(v any, def bool) bool {
	if v == nil {
		return def
	}
	return boolOf(v)
}

// sanitizeOptsDomain 清洗允许来源域名：仅放行域名白名单（含通配子域）。
func sanitizeOptsDomain(d string) (string, bool) {
	d = strings.ToLower(strings.TrimSpace(d))
	if optsDomainRe.MatchString(d) {
		return d, true
	}
	return "", false
}

// cacheExpires 将秒数转为 nginx expires 时长；0/非法返回空串。
func cacheExpires(seconds int) string {
	if seconds <= 0 {
		return ""
	}
	if seconds > optsMaxCacheSeconds {
		seconds = optsMaxCacheSeconds
	}
	if seconds%86400 == 0 {
		return itoa(seconds/86400) + "d"
	}
	if seconds%3600 == 0 {
		return itoa(seconds/3600) + "h"
	}
	if seconds%60 == 0 {
		return itoa(seconds/60) + "m"
	}
	return itoa(seconds) + "s"
}

// siteOptsExtra 对应 Python sitesopts.get_nginx_extra：按站点顶层
// hotlink / gzip / cache_expire 生成注入 nginx server 块的片段。
func siteOptsExtra(site map[string]any) []string {
	siteType := siteStr(site, "type")
	if siteType == "" {
		siteType = siteTypeStatic
	}
	if siteType != siteTypeStatic && siteType != siteTypeSubsite {
		return nil
	}
	hotlink := mapOf(site["hotlink"])
	gzipEnabled := boolOf(mapOf(site["gzip"])["enabled"])
	cacheSeconds := int(int64Of(site["cache_expire"]))

	blocks := []string{}
	needStatic := boolOf(hotlink["enabled"]) || cacheSeconds > 0
	if needStatic {
		lines := []string{
			"# 防盗链 + 静态资源缓存（面板站点增强配置）",
			`    location ~* \.(` + optsStaticExts + `)$ {`,
		}
		if boolOf(hotlink["enabled"]) {
			domains := []string{}
			for _, raw := range toStringSlice(hotlink["allowed"]) {
				if clean, ok := sanitizeOptsDomain(raw); ok {
					domains = append(domains, clean)
				}
			}
			refs := "server_names"
			if boolOf(hotlink["allow_empty_referer"]) {
				refs = "none blocked " + refs
			}
			if len(domains) > 0 {
				refs += " " + strings.Join(domains, " ")
			}
			lines = append(lines, "        valid_referers "+refs+";",
				"        if ($invalid_referer) {", "            return 403;", "        }")
		}
		if exp := cacheExpires(cacheSeconds); exp != "" {
			lines = append(lines, "        expires "+exp+";")
		}
		lines = append(lines, "    }")
		blocks = append(blocks, strings.Join(lines, "\n"))
	}
	if gzipEnabled {
		blocks = append(blocks, strings.Join([]string{
			"# gzip 压缩（面板站点增强配置）",
			"    gzip on;",
			"    gzip_min_length 1024;",
			"    gzip_types text/css application/javascript application/json application/xml application/x-font-woff image/svg+xml text/plain;",
		}, "\n"))
	}
	return blocks
}

// --- SSL ---

type CertInfo struct {
	ID        string   `json:"id"`
	Name      string   `json:"name"`
	Domains   []string `json:"domains"`
	Type      string   `json:"type"`
	CertPath  string   `json:"cert_path"`
	KeyPath   string   `json:"key_path"`
	CreatedAt string   `json:"created_at"`
	ExpiresAt string   `json:"expires_at,omitempty"`
}

func loadSSLCerts() map[string]any {
	data := map[string]any{"certs": []any{}}
	_ = ReadJSON(DataPath("ssl.json"), &data)
	if _, ok := data["certs"]; !ok {
		data["certs"] = []any{}
	}
	return data
}

func sslCertsList(data map[string]any) []map[string]any {
	out := []map[string]any{}
	if arr, ok := data["certs"].([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok {
				out = append(out, m)
			}
		}
	}
	return out
}

var certDomainRe = regexp.MustCompile(`(?i)^(\*\.)?([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}$`)

func readCertExpiry(path string) string {
	b, err := os.ReadFile(HostPath(path))
	if err != nil {
		return ""
	}
	block, _ := pem.Decode(b)
	if block == nil {
		return ""
	}
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return ""
	}
	return cert.NotAfter.Format("2006-01-02T15:04:05")
}

func registerSSLRoutes(a *App) {
	a.Get("/api/ssl/list", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		data := loadSSLCerts()
		return WriteJSON(w, 200, map[string]any{
			"certs": sslCertsList(data), "certbot": HostWhich("certbot") != "",
		})
	})
	a.Post("/api/ssl/upload", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		if err := r.ParseMultipartForm(32 << 20); err != nil {
			return NewHTTPError(400, "上传表单解析失败")
		}
		name := strings.TrimSpace(r.FormValue("name"))
		if name == "" || len(name) > 64 || strings.HasPrefix(name, "-") {
			return NewHTTPError(400, "证书名称非法")
		}
		certFile, _, err := r.FormFile("cert")
		if err != nil {
			return NewHTTPError(400, "缺少证书文件")
		}
		defer certFile.Close()
		keyFile, _, err := r.FormFile("key")
		if err != nil {
			return NewHTTPError(400, "缺少私钥文件")
		}
		defer keyFile.Close()
		readLimited := func(f io.Reader, what string) ([]byte, error) {
			data, err := io.ReadAll(io.LimitReader(f, 1024*1024+1))
			if err != nil {
				return nil, NewHTTPError(400, "读取"+what+"失败")
			}
			if len(data) > 1024*1024 {
				return nil, NewHTTPError(413, what+" 文件过大（上限 1MB）")
			}
			if len(data) == 0 {
				return nil, NewHTTPError(400, what+" 文件为空")
			}
			return data, nil
		}
		certData, err := readLimited(certFile, "证书")
		if err != nil {
			return err
		}
		keyData, err := readLimited(keyFile, "私钥")
		if err != nil {
			return err
		}
		id := randomHex(8)
		dir := DataPath("ssl")
		_ = EnsureDir(dir)
		certPath := filepath.Join(dir, id+".crt")
		keyPath := filepath.Join(dir, id+".key")
		_ = os.WriteFile(certPath, certData, 0o600)
		_ = os.WriteFile(keyPath, keyData, 0o600)
		domains := []string{}
		for _, d := range strings.Split(r.FormValue("domains"), ",") {
			if s := strings.TrimSpace(d); s != "" {
				domains = append(domains, s)
			}
		}
		data := loadSSLCerts()
		certs := append(sslCertsList(data), map[string]any{
			"id": id, "name": name, "domains": domains, "type": "custom",
			"cert_path": certPath, "key_path": keyPath,
			"created_at": time.Now().Format("2006-01-02T15:04:05"),
			"expires_at": readCertExpiry(certPath),
		})
		data["certs"] = certs
		if err := WriteJSONFile(DataPath("ssl.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/ssl/letsencrypt", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Domains []string `json:"domains"`
			Email   string   `json:"email"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if HostWhich("certbot") == "" {
			return NewHTTPError(503, "certbot not installed")
		}
		if len(req.Domains) == 0 || len(req.Domains) > 32 {
			return NewHTTPError(400, "domains 必须是 1-32 个域名")
		}
		for _, d := range req.Domains {
			if !certDomainRe.MatchString(d) {
				return NewHTTPError(400, "域名格式非法: "+d)
			}
		}
		if !strings.Contains(req.Email, "@") || strings.ContainsAny(req.Email, " \t\r\n") {
			return NewHTTPError(400, "邮箱格式非法")
		}
		args := []string{"certonly", "--standalone", "--agree-tos", "-m", req.Email, "-n",
			"--preferred-challenges", "http"}
		for _, d := range req.Domains {
			args = append(args, "-d", d)
		}
		res := HostCmd(120*time.Second, "certbot", args...)
		if res.Code != 0 {
			return NewHTTPError(500, strings.TrimSpace(res.Stderr+res.Stdout))
		}
		first := req.Domains[0]
		data := loadSSLCerts()
		certs := append(sslCertsList(data), map[string]any{
			"id": "le_" + randomHex(6), "name": first, "domains": req.Domains,
			"type":       "letsencrypt",
			"cert_path":  "/etc/letsencrypt/live/" + first + "/fullchain.pem",
			"key_path":   "/etc/letsencrypt/live/" + first + "/privkey.pem",
			"created_at": time.Now().Format("2006-01-02T15:04:05"),
		})
		data["certs"] = certs
		if err := WriteJSONFile(DataPath("ssl.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/ssl/{cert_id}/delete", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "cert_id")
		data := loadSSLCerts()
		var cert map[string]any
		kept := []map[string]any{}
		for _, c := range sslCertsList(data) {
			if str(c["id"]) == id {
				cert = c
			} else {
				kept = append(kept, c)
			}
		}
		if cert == nil {
			return NewHTTPError(404, "Certificate not found")
		}
		if str(cert["type"]) == "letsencrypt" && HostWhich("certbot") != "" {
			HostCmd(30*time.Second, "certbot", "delete", "--cert-name", str(cert["name"]))
		}
		for _, p := range []string{str(cert["cert_path"]), str(cert["key_path"])} {
			if p != "" {
				_ = os.Remove(HostPath(p))
			}
		}
		data["certs"] = kept
		if err := WriteJSONFile(DataPath("ssl.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
}

// --- 伪静态 ---

func registerRewriteRoutes(a *App) {
	a.Get("/api/rewrite/templates", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		items := []map[string]any{}
		for id, t := range rewriteTemplates {
			items = append(items, map[string]any{
				"id": id, "name": t["name"], "content": t["content"],
			})
		}
		sort.Slice(items, func(i, j int) bool { return str(items[i]["name"]) < str(items[j]["name"]) })
		return WriteJSON(w, 200, map[string]any{"templates": items})
	})
	a.Get("/api/rewrite/sites", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		sites := []map[string]any{}
		for _, s := range loadSites() {
			sites = append(sites, map[string]any{
				"id": str(s["id"]), "name": str(s["name"]), "rewrite": str(s["rewrite"]),
			})
		}
		return WriteJSON(w, 200, map[string]any{"sites": sites})
	})
	a.Post("/api/rewrite/apply", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			SiteID    string `json:"site_id"`
			RewriteID string `json:"rewrite_id"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if _, ok := rewriteTemplates[req.RewriteID]; !ok {
			return NewHTTPError(400, "伪静态模板不存在")
		}
		sites := loadSites()
		var site map[string]any
		for _, s := range sites {
			if str(s["id"]) == req.SiteID {
				site = s
				break
			}
		}
		if site == nil {
			return NewHTTPError(404, "Site not found")
		}
		site["rewrite"] = req.RewriteID
		if siteBool(site, "enabled") {
			if err := applySiteConfig(site, true); err != nil {
				return err
			}
			reloadIfNginx()
		}
		if err := saveSites(sites); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "site_id": req.SiteID, "engine": webServerType()})
	})
	a.Post("/api/rewrite/clear", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			SiteID string `json:"site_id"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		sites := loadSites()
		for _, s := range sites {
			if str(s["id"]) == req.SiteID {
				delete(s, "rewrite")
				if siteBool(s, "enabled") {
					_ = applySiteConfig(s, true)
					reloadIfNginx()
				}
			}
		}
		if err := saveSites(sites); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "site_id": req.SiteID})
	})
}

// --- 站点增强 ---

// loadSiteOptsExternal 读取外部真实站点的增强配置（site_id -> opts）。
// 与 Python 一致：外部站点不在 sites.json，增强配置单独持久化。
func loadSiteOptsExternal() map[string]map[string]any {
	out := map[string]map[string]any{}
	var raw map[string]any
	_ = ReadJSON(DataPath("sitesopts_external.json"), &raw)
	for k, v := range raw {
		if m, ok := v.(map[string]any); ok {
			out[k] = m
		}
	}
	return out
}

func saveSiteOptsExternal(data map[string]map[string]any) error {
	return WriteJSONFile(DataPath("sitesopts_external.json"), data)
}

// findAnySite 在自建站点与真实外部站点中按 id 反查，并标记是否外部站点。
func findAnySite(id string) (map[string]any, bool) {
	for _, s := range loadSites() {
		if siteStr(s, "id") == id {
			return s, false
		}
	}
	if ext := findExternalSite(id); ext != nil {
		return ext, true
	}
	return nil, false
}

// writeExternalSiteConfig 把生成的 nginx 配置写回外部站点真实 conf 文件。
func writeExternalSiteConfig(site map[string]any) error {
	confPath := siteStr(site, "conf_path")
	if confPath == "" {
		return nil
	}
	root := ""
	if siteBool(site, "maintenance") {
		root = maintenanceRoot(site)
	}
	if err := writeHostFile(confPath, nginxSiteConfig(site, root)); err != nil {
		return err
	}
	reloadIfNginx()
	return nil
}

func registerSiteOptsRoutes(a *App) {
	a.Get("/api/sitesopts/sites", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		extOpts := loadSiteOptsExternal()
		items := []map[string]any{}
		for _, s := range mergedSites() {
			id := str(s["id"])
			var hotlink map[string]any
			var gzipEnabled bool
			var cacheSeconds int
			if boolOf(s["external"]) {
				o := extOpts[id]
				hotlink = mapOf(o["hotlink"])
				gzipEnabled = boolOf(mapOf(o["gzip"])["enabled"])
				cacheSeconds = int(int64Of(o["cache_expire"]))
			} else {
				hotlink = mapOf(s["hotlink"])
				gzipEnabled = boolOf(mapOf(s["gzip"])["enabled"])
				cacheSeconds = int(int64Of(s["cache_expire"]))
			}
			items = append(items, map[string]any{
				"id": id, "name": str(s["name"]), "type": str(s["type"]),
				"external":                    boolOf(s["external"]),
				"enabled":                     boolOf(s["enabled"]),
				"hotlink_enabled":             boolOf(hotlink["enabled"]),
				"hotlink_allowed":             toStringSlice(hotlink["allowed"]),
				"hotlink_allow_empty_referer": boolOfDefault(hotlink["allow_empty_referer"], true),
				"gzip_enabled":                gzipEnabled,
				"cache_expire":                cacheSeconds,
			})
		}
		return WriteJSON(w, 200, map[string]any{"sites": items})
	})
	a.Post("/api/sitesopts/apply", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			SiteID         string   `json:"site_id"`
			Hotlink        bool     `json:"hotlink_enabled"`
			HotlinkAllowed []string `json:"hotlink_allowed"`
			AllowEmpty     *bool    `json:"hotlink_allow_empty_referer"`
			Gzip           bool     `json:"gzip_enabled"`
			CacheExpire    int      `json:"cache_expire"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if strings.TrimSpace(req.SiteID) == "" {
			return NewHTTPError(400, "请选择站点")
		}
		site, external := findAnySite(req.SiteID)
		if site == nil {
			return NewHTTPError(404, "站点不存在")
		}
		siteType := siteStr(site, "type")
		if siteType != siteTypeStatic && siteType != siteTypeSubsite {
			return NewHTTPError(400, "仅静态网址与子网站支持防盗链/缓存配置")
		}
		allowed := []string{}
		seen := map[string]bool{}
		for _, d := range req.HotlinkAllowed {
			if clean, ok := sanitizeOptsDomain(d); ok && !seen[clean] {
				seen[clean] = true
				allowed = append(allowed, clean)
			}
		}
		if len(allowed) > 32 {
			return NewHTTPError(400, "允许来源域名过多（最多 32 个）")
		}
		cache := req.CacheExpire
		if cache < 0 {
			return NewHTTPError(400, "缓存时长不能为负数")
		}
		if cache > optsMaxCacheSeconds {
			cache = optsMaxCacheSeconds
		}
		allowEmpty := true
		if req.AllowEmpty != nil {
			allowEmpty = *req.AllowEmpty
		}
		opts := map[string]any{
			"hotlink": map[string]any{
				"enabled": req.Hotlink, "allowed": allowed, "allow_empty_referer": allowEmpty,
			},
			"gzip":         map[string]any{"enabled": req.Gzip},
			"cache_expire": cache,
		}
		if external {
			ext := loadSiteOptsExternal()
			ext[req.SiteID] = opts
			if err := saveSiteOptsExternal(ext); err != nil {
				return err
			}
			site["hotlink"] = opts["hotlink"]
			site["gzip"] = opts["gzip"]
			site["cache_expire"] = cache
			if siteBool(site, "enabled") {
				if err := writeExternalSiteConfig(site); err != nil {
					return err
				}
			}
			return WriteJSON(w, 200, map[string]any{"ok": true, "site_id": req.SiteID, "engine": webServerType()})
		}
		sites := loadSites()
		for _, s := range sites {
			if siteStr(s, "id") == req.SiteID {
				s["hotlink"] = opts["hotlink"]
				s["gzip"] = opts["gzip"]
				s["cache_expire"] = cache
			}
		}
		if err := saveSites(sites); err != nil {
			return err
		}
		ws := webServerType()
		if siteBool(site, "enabled") && (ws == "nginx" || ws == "openresty") {
			if err := applySiteConfig(site, true); err != nil {
				return err
			}
			reloadIfNginx()
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "site_id": req.SiteID, "engine": ws})
	})
	a.Post("/api/sitesopts/clear", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			SiteID string `json:"site_id"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if strings.TrimSpace(req.SiteID) == "" {
			return NewHTTPError(400, "请选择站点")
		}
		site, external := findAnySite(req.SiteID)
		if site == nil {
			return NewHTTPError(404, "站点不存在")
		}
		if external {
			ext := loadSiteOptsExternal()
			delete(ext, req.SiteID)
			if err := saveSiteOptsExternal(ext); err != nil {
				return err
			}
			delete(site, "hotlink")
			delete(site, "gzip")
			delete(site, "cache_expire")
			if siteBool(site, "enabled") {
				if err := writeExternalSiteConfig(site); err != nil {
					return err
				}
			}
			return WriteJSON(w, 200, map[string]any{"ok": true, "site_id": req.SiteID})
		}
		sites := loadSites()
		for _, s := range sites {
			if siteStr(s, "id") == req.SiteID {
				delete(s, "hotlink")
				delete(s, "gzip")
				delete(s, "cache_expire")
			}
		}
		if err := saveSites(sites); err != nil {
			return err
		}
		ws := webServerType()
		if siteBool(site, "enabled") && (ws == "nginx" || ws == "openresty") {
			if err := applySiteConfig(site, true); err != nil {
				return err
			}
			reloadIfNginx()
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "site_id": req.SiteID})
	})
}

// registerSiteExtraRoutes 注册网站扩展模块（SSL/伪静态/站点增强/PHP/统计/WAF）。
func registerSiteExtraRoutes(a *App) {
	registerSSLRoutes(a)
	registerRewriteRoutes(a)
	registerSiteOptsRoutes(a)
	registerPHPVersionRoutes(a)
	registerWebStatsRoutes(a)
	registerWAFRoutes(a)
}

// --- PHP 版本 ---

func detectPHPVersions() []map[string]any {
	out := []map[string]any{}
	for _, dir := range []string{"/usr/bin", "/usr/local/bin"} {
		realDir := HostPath(dir)
		entries, _ := os.ReadDir(realDir)
		for _, e := range entries {
			name := e.Name()
			if !regexp.MustCompile(`^php[0-9.]*$`).MatchString(name) || e.IsDir() {
				continue
			}
			full := filepath.Join(realDir, name)
			res := HostCmd(5*time.Second, full, "-v")
			ver := ""
			if res.Code == 0 {
				f := strings.Fields(res.Stdout)
				if len(f) > 1 {
					ver = f[1]
				}
			}
			if ver == "" {
				continue
			}
			short := strings.Split(ver, ".")
			if len(short) >= 2 {
				ver = short[0] + "." + short[1]
			}
			fpm := ""
			for _, sdir := range []string{"/usr/sbin", "/usr/local/sbin", "/usr/bin"} {
				for _, cand := range []string{"php-fpm" + ver, "php-fpm"} {
					p := filepath.Join(HostPath(sdir), cand)
					if Exists(p) {
						fpm = p
						break
					}
				}
				if fpm != "" {
					break
				}
			}
			out = append(out, map[string]any{
				"version": ver, "binary": full, "fpm": fpm,
				"socket": "/run/php/php" + ver + "-fpm.sock",
			})
		}
	}
	return out
}

func registerPHPVersionRoutes(a *App) {
	a.Get("/api/phpversions/list", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"versions": detectPHPVersions()})
	})
	a.Get("/api/phpversions/status", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		versions := detectPHPVersions()
		reason := ""
		if len(versions) == 0 {
			reason = "未检测到 PHP 可执行文件"
		}
		return WriteJSON(w, 200, map[string]any{
			"available": len(versions) > 0, "php_versions": versions, "reason": reason,
		})
	})
	a.Get("/api/phpversions/sites", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		items := []map[string]any{}
		for _, s := range loadSites() {
			items = append(items, map[string]any{
				"id": str(s["id"]), "name": str(s["name"]), "php_version": str(s["php_version"]),
			})
		}
		return WriteJSON(w, 200, map[string]any{"sites": items})
	})
	a.Post("/api/phpversions/site/{site_id}/set-php", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "site_id")
		var req struct {
			Version string `json:"version"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		sites := loadSites()
		for _, s := range sites {
			if str(s["id"]) == id {
				s["php_version"] = strings.TrimSpace(req.Version)
				if siteBool(s, "enabled") {
					_ = applySiteConfig(s, true)
					reloadIfNginx()
				}
			}
		}
		if err := saveSites(sites); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
}

// --- 访问统计 ---

var accessLogCandidates = []string{
	"/var/log/nginx/access.log",
	"/var/log/nginx/openresty/access.log",
	"/usr/local/openresty/nginx/logs/access.log",
	"/usr/local/nginx/logs/access.log",
	"/opt/1panel/apps/openresty/openresty/log/access.log",
	"/var/log/httpd/access_log",
}

var combinedLogRe = regexp.MustCompile(`^(\S+)\s+-\s+(\S+)\s+\[([^\]]+)\]\s+"([^"]*)"\s+(\d{3})\s+(\d+)\s+"([^"]*)"\s+"([^"]*)"`)

func discoverAccessLogs() []string {
	found := []string{}
	for _, p := range accessLogCandidates {
		if Exists(HostPath(p)) {
			found = append(found, p)
		}
	}
	globs := []string{"/opt/1panel/www/sites/*/log/access.log", "/opt/1panel/wwwlogs/*.log"}
	for _, g := range globs {
		matches, _ := filepath.Glob(HostPath(g))
		for _, m := range matches {
			found = append(found, UnhostPath(m))
		}
	}
	return found
}

func registerWebStatsRoutes(a *App) {
	a.Get("/api/webstats/logs", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"logs": discoverAccessLogs()})
	})
	a.Get("/api/webstats/analyze", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		path := r.URL.Query().Get("log_path")
		days := QueryInt(r, "days", 7)
		domain := r.URL.Query().Get("domain")
		if days < 1 || days > 365 {
			days = 7
		}
		if path == "" {
			found := discoverAccessLogs()
			if len(found) == 0 {
				return NewHTTPError(400, "未在常见路径找到 nginx 访问日志，请手动指定日志路径")
			}
			path = found[0]
		}
		if strings.Contains(path, "..") || strings.HasPrefix(path, `\\`) || strings.HasPrefix(path, "//") {
			return NewHTTPError(400, "非法日志路径")
		}
		real := HostPath(path)
		st, err := os.Stat(real)
		if err != nil || st.IsDir() {
			return NewHTTPError(400, "日志文件不存在: "+path)
		}
		if forbiddenDataPath(path) {
			return NewHTTPError(403, "无权访问面板数据目录")
		}
		data, err := os.ReadFile(real)
		if err != nil {
			return NewHTTPError(400, "读取日志失败")
		}
		if len(data) > 200*1024*1024 {
			data = data[len(data)-200*1024*1024:]
		}
		result := aggregateAccessLog(string(data), days, domain)
		result["file"] = path
		result["days"] = days
		result["domain"] = domain
		result["lines"] = strings.Count(string(data), "\n")
		result["truncated"] = st.Size() > 200*1024*1024
		return WriteJSON(w, 200, result)
	})
}

func parseNginxTime(value string) time.Time {
	layouts := []string{"02/Jan/2006:15:04:05 -0700", "02/Jan/2006:15:04:05"}
	for _, l := range layouts {
		if t, err := time.Parse(l, value); err == nil {
			return t
		}
	}
	return time.Time{}
}

func aggregateAccessLog(text string, days int, domain string) map[string]any {
	cut := time.Now().AddDate(0, 0, -days)
	pv, pagePV, bots := 0, 0, 0
	ipSet, uaSet := map[string]bool{}, map[string]bool{}
	dailyPV := map[string]int{}
	dailyIP := map[string]map[string]bool{}
	status := map[string]int{}
	pages, ips, referers := map[string]int{}, map[string]int{}, map[string]int{}
	staticRe := regexp.MustCompile(`(?i)\.(css|js|png|jpe?g|gif|webp|svg|ico|woff2?|ttf|eot|map)(\?.*)?$`)
	botRe := regexp.MustCompile(`(?i)bot|spider|crawl|slurp|scan|curl|wget|python-requests|Go-http|postman`)
	for _, line := range strings.Split(text, "\n") {
		m := combinedLogRe.FindStringSubmatch(strings.TrimSpace(line))
		if m == nil {
			continue
		}
		ip, tstr, request, code, referer, ua := m[1], m[3], m[4], m[5], m[7], m[8]
		tm := parseNginxTime(tstr)
		if tm.IsZero() || tm.Before(cut) {
			continue
		}
		reqPath := request
		if parts := strings.Split(request, " "); len(parts) >= 2 {
			reqPath = parts[1]
		}
		if domain != "" {
			hostPart := "/"
			_ = hostPart
		}
		pv++
		ipSet[ip] = true
		uaSet[ua] = true
		if botRe.MatchString(ua) {
			bots++
		}
		status[code]++
		day := tm.Format("2006-01-02")
		dailyPV[day]++
		if dailyIP[day] == nil {
			dailyIP[day] = map[string]bool{}
		}
		dailyIP[day][ip] = true
		if !staticRe.MatchString(reqPath) {
			pagePV++
			clean := strings.Split(reqPath, "?")[0]
			if len(clean) > 200 {
				clean = clean[:200]
			}
			pages[clean]++
		}
		ips[ip]++
		if referer != "" && referer != "-" && !strings.HasPrefix(referer, "/") {
			host := referer
			if idx := strings.Index(referer, "://"); idx >= 0 {
				host = referer[idx+3:]
			}
			if idx := strings.IndexAny(host, "/?#"); idx >= 0 {
				host = host[:idx]
			}
			if host != "" {
				referers[host]++
			}
		}
	}
	daily := []map[string]any{}
	keys := []string{}
	for k := range dailyPV {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		daily = append(daily, map[string]any{"date": k, "pv": dailyPV[k], "uv": len(dailyIP[k])})
	}
	statusOut := map[string]int{}
	for k, v := range status {
		statusOut[k] = v
	}
	type kv struct {
		K string
		V int
	}
	topList := func(m map[string]int, limit int) []kv {
		list := []kv{}
		for k, v := range m {
			list = append(list, kv{k, v})
		}
		sort.Slice(list, func(i, j int) bool { return list[i].V > list[j].V })
		if len(list) > limit {
			list = list[:limit]
		}
		return list
	}
	topPages := []map[string]any{}
	for _, p := range topList(pages, 20) {
		topPages = append(topPages, map[string]any{"path": p.K, "count": p.V})
	}
	topIPs := []map[string]any{}
	for _, p := range topList(ips, 20) {
		topIPs = append(topIPs, map[string]any{"ip": p.K, "count": p.V})
	}
	topRefs := []map[string]any{}
	for _, p := range topList(referers, 20) {
		topRefs = append(topRefs, map[string]any{"host": p.K, "count": p.V})
	}
	return map[string]any{
		"pv": pv, "page_pv": pagePV, "uv": len(uaSet), "ip_count": len(ipSet),
		"bots": bots, "daily": daily, "status": statusOut,
		"top_pages": topPages, "top_ips": topIPs, "referers": topRefs,
	}
}

// --- WAF ---

func wafDefaultConfig(site string) map[string]any {
	return map[string]any{
		"site": site, "enabled": false,
		"frequency": map[string]any{
			"access":   map[string]any{"mode": "url", "period": 60, "count": 100, "ban": 600},
			"attack":   map[string]any{"mode": "url", "period": 60, "count": 30, "ban": 600},
			"notfound": map[string]any{"mode": "url", "period": 60, "count": 10, "ban": 600},
		},
		"defense": map[string]any{
			"sql": true, "webshell": true, "directory": true, "xss": true,
			"param": true, "ua": true, "header": true, "cookie": true, "http": true, "url": true,
		},
		"custom": map[string]any{"upload_limit_mb": 20, "cdn": false},
		"other":  map[string]any{"malicious_ip_groups": []any{}, "spider_pool": true},
		"blackwhite": map[string]any{
			"ip_whitelist": []any{}, "ip_blacklist": []any{}, "url_whitelist": []any{},
			"url_blacklist": []any{}, "ua_whitelist": []any{}, "ua_blacklist": []any{}, "ip_groups": []any{},
		},
		"geo":          map[string]any{"enabled": false, "action": "block", "countries": []any{}},
		"acl":          []any{},
		"waiting_hall": map[string]any{"enabled": false, "url": "/waf_challenge"},
		"updated_at":   time.Now().Format("2006-01-02T15:04:05"),
	}
}

func loadWAF() map[string]any {
	data := map[string]any{"enabled": false, "sites": []any{}}
	_ = ReadJSON(DataPath("waf.json"), &data)
	if data == nil {
		return map[string]any{"enabled": false, "sites": []any{}}
	}
	return data
}

func saveWAF(data map[string]any) error {
	CaptureSnapshot("waf", "waf", DataPath("waf.json"))
	return WriteJSONFile(DataPath("waf.json"), data)
}

// wafSiteActive 判断某个站点的 WAF 是否生效（全局开关 + 站点开关）。
func wafSiteActive(siteID string) bool {
	data := loadWAF()
	if !boolOf(data["enabled"]) {
		return false
	}
	if arr, ok := data["sites"].([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok && str(m["site"]) == siteID {
				return boolOf(m["enabled"])
			}
		}
	}
	return false
}

func nginxHasGeoIP() bool {
	if webServerAvailable(webServerBinary()) == false {
		return false
	}
	res := HostCmd(10*time.Second, webServerBinary(), "-V")
	return strings.Contains(res.Stdout+res.Stderr, "geoip2")
}

func wafSafePattern(s string) string {
	s = strings.ReplaceAll(s, "\n", "")
	s = strings.ReplaceAll(s, "\r", "")
	s = strings.ReplaceAll(s, "\\", "\\\\")
	s = strings.ReplaceAll(s, "\"", "\\\"")
	s = strings.ReplaceAll(s, "}", "")
	s = strings.ReplaceAll(s, "{", "")
	if len(s) > 200 {
		s = s[:200]
	}
	return s
}

func wafStringList(v any) []string {
	out := []string{}
	for _, item := range toStringSlice(v) {
		if s := strings.TrimSpace(item); s != "" {
			out = append(out, s)
		}
	}
	return out
}

// wafZonesConfig 生成 http 上下文的 map/limit_req_zone 定义（所有启用站点汇总）。
func wafZonesConfig(data map[string]any) string {
	lines := []string{"# Managed by Graw WAF - zones (http context)", ""}
	if arr, ok := data["sites"].([]any); ok {
		for _, item := range arr {
			m, ok := item.(map[string]any)
			if !ok || !boolOf(m["enabled"]) {
				continue
			}
			id := str(m["site"])
			if !siteIDRe.MatchString(id) {
				continue
			}
			bw := mapOf(m["blackwhite"])
			lines = append(lines, "map $remote_addr $graw_waf_bl_"+id+" {", "    default 0;")
			for _, ip := range wafStringList(bw["ip_blacklist"]) {
				if validateIPValue(ip) == nil {
					lines = append(lines, "    "+ip+" 1;")
				}
			}
			lines = append(lines, "}")
			lines = append(lines, "map $remote_addr $graw_waf_wl_"+id+" {", "    default 0;")
			for _, ip := range wafStringList(bw["ip_whitelist"]) {
				if validateIPValue(ip) == nil {
					lines = append(lines, "    "+ip+" 1;")
				}
			}
			lines = append(lines, "}")
			freq := mapOf(mapOf(m["frequency"])["access"])
			count := int(int64Of(freq["count"]))
			period := int(int64Of(freq["period"]))
			if count > 0 && period > 0 {
				lines = append(lines, "limit_req_zone $binary_remote_addr zone=graw_waf_req_"+id+
					":10m rate="+itoa(count)+"r/"+itoa(period)+"s;")
			}
			lines = append(lines, "")
		}
	}
	return strings.Join(lines, "\n")
}

// wafNginxConfig 生成站点级 WAF include 片段（IP/URL/UA 黑白名单、频率限制、Geo、上传限制）。
func wafNginxConfig(cfg map[string]any) string {
	id := str(cfg["site"])
	if !siteIDRe.MatchString(id) {
		return "# invalid site id\n"
	}
	lines := []string{"# Managed by Graw WAF - site " + id, "set $graw_waf_deny 0;", ""}
	bw := mapOf(cfg["blackwhite"])
	for _, ip := range wafStringList(bw["ip_blacklist"]) {
		if validateIPValue(ip) == nil {
			lines = append(lines, "deny "+ip+";")
		}
	}
	for _, ip := range wafStringList(bw["ip_whitelist"]) {
		if validateIPValue(ip) == nil {
			lines = append(lines, "allow "+ip+";")
		}
	}
	lines = append(lines, "if ($graw_waf_bl_"+id+" = 1) { set $graw_waf_deny 1; }")
	lines = append(lines, "if ($graw_waf_wl_"+id+" = 1) { set $graw_waf_deny 0; }")
	for _, p := range wafStringList(bw["url_blacklist"]) {
		if pat := wafSafePattern(p); pat != "" {
			lines = append(lines, "if ($request_uri ~* \""+pat+"\") { set $graw_waf_deny 1; }")
		}
	}
	for _, p := range wafStringList(bw["ua_blacklist"]) {
		if pat := wafSafePattern(p); pat != "" {
			lines = append(lines, "if ($http_user_agent ~* \""+pat+"\") { set $graw_waf_deny 1; }")
		}
	}
	// 基础攻击特征（SQL 注入 / 路径穿越 / XSS / 命令注入）。
	lines = append(lines,
		"if ($query_string ~* \"(union[[:space:]]+select|select[[:space:]]+.*[[:space:]]+from|sleep\\\\([0-9]+\\\\)|benchmark\\\\(|information_schema)\") { set $graw_waf_deny 1; }",
		"if ($request_uri ~* \"(\\\\.\\\\./|/etc/passwd|/proc/self|%00|\\\\.git/)\") { set $graw_waf_deny 1; }",
		"if ($query_string ~* \"(\\\\.\\\\./|<script|javascript:|onerror[[:space:]]*=)\") { set $graw_waf_deny 1; }",
		"if ($request_uri ~* \"(;|\\\\||`|\\\\$\\\\(|wget[[:space:]]|curl[[:space:]])\") { set $graw_waf_deny 1; }",
	)
	for _, p := range wafStringList(bw["url_whitelist"]) {
		if pat := wafSafePattern(p); pat != "" {
			lines = append(lines, "if ($request_uri ~* \""+pat+"\") { set $graw_waf_deny 0; }")
		}
	}
	for _, p := range wafStringList(bw["ua_whitelist"]) {
		if pat := wafSafePattern(p); pat != "" {
			lines = append(lines, "if ($http_user_agent ~* \""+pat+"\") { set $graw_waf_deny 0; }")
		}
	}
	lines = append(lines, "if ($graw_waf_deny = 1) { return 403; }")
	if geo := mapOf(cfg["geo"]); boolOf(geo["enabled"]) && nginxHasGeoIP() {
		countries := wafStringList(geo["countries"])
		if len(countries) > 0 {
			action := "return 403"
			if str(geo["action"]) == "challenge" {
				action = "return 302 /waf_challenge"
			}
			lines = append(lines, "if ($geoip2_country_code ~* \"^("+
				wafSafePattern(strings.Join(countries, "|"))+")$\") { "+action+"; }")
		}
	}
	if freq := mapOf(mapOf(cfg["frequency"])["access"]); int(int64Of(freq["count"])) > 0 {
		burst := int(int64Of(freq["count"]))
		lines = append(lines, "limit_req zone=graw_waf_req_"+id+" burst="+itoa(burst)+" nodelay;")
		lines = append(lines, "limit_req_status 429;")
	}
	if uploadMB := int(int64Of(mapOf(cfg["custom"])["upload_limit_mb"])); uploadMB > 0 {
		lines = append(lines, "client_max_body_size "+itoa(uploadMB)+"m;")
	}
	if hall := mapOf(cfg["waiting_hall"]); boolOf(hall["enabled"]) {
		urlVal := defaultString(str(hall["url"]), "/waf_challenge")
		lines = append(lines, "error_page 429 = @graw_waf_waiting;",
			"location @graw_waf_waiting { return 302 "+wafSafePattern(urlVal)+"; }")
	}
	return strings.Join(lines, "\n") + "\n"
}

// ensureWAFInclude 把 zones 文件 include 到 nginx.conf 的 http 块中。
func ensureWAFInclude() {
	confPath := HostPath(webServerBaseDir() + "/nginx.conf")
	content, err := os.ReadFile(confPath)
	if err != nil {
		return
	}
	include := "include " + webServerBaseDir() + "/waf/zones.conf;"
	if strings.Contains(string(content), include) {
		return
	}
	text := string(content)
	idx := strings.Index(text, "http {")
	if idx < 0 {
		return
	}
	end := strings.Index(text[idx:], "}")
	if end < 0 {
		return
	}
	end += idx + 1
	text = text[:end] + "\n    " + include + "\n" + text[end:]
	_ = os.WriteFile(confPath, []byte(text), 0o644)
}

// wafApplyAll 生成 zones 与各站点 WAF include，并重写站点配置以挂载/移除 include。
func wafApplyAll(data map[string]any) int {
	dir := webServerBaseDir() + "/waf"
	_ = os.MkdirAll(HostPath(dir), 0o755)
	zones := "# Managed by Graw WAF\n"
	if boolOf(data["enabled"]) {
		zones = wafZonesConfig(data)
		ensureWAFInclude()
	}
	_ = writeHostFile(dir+"/zones.conf", zones)
	written := 0
	enabledIDs := map[string]bool{}
	if boolOf(data["enabled"]) {
		if arr, ok := data["sites"].([]any); ok {
			for _, item := range arr {
				m, ok := item.(map[string]any)
				if !ok || !boolOf(m["enabled"]) {
					continue
				}
				id := str(m["site"])
				if !siteIDRe.MatchString(id) {
					continue
				}
				if err := writeHostFile(dir+"/"+id+".conf", wafNginxConfig(m)); err != nil {
					continue
				}
				enabledIDs[id] = true
				written++
			}
		}
	}
	// 清理已停用的站点片段，避免 include 残留导致 nginx 报错。
	entries, _ := os.ReadDir(HostPath(dir))
	for _, e := range entries {
		name := e.Name()
		if !strings.HasSuffix(name, ".conf") || name == "zones.conf" {
			continue
		}
		id := strings.TrimSuffix(name, ".conf")
		if !enabledIDs[id] {
			_ = os.Remove(HostPath(dir + "/" + name))
		}
	}
	for _, site := range loadSites() {
		if boolOf(site["enabled"]) {
			_ = applySiteConfig(site, true)
		}
	}
	reloadIfNginx()
	return written
}

// wafRewriteSite 重新生成某站点的 WAF 片段并刷新其 nginx 配置。
func wafRewriteSite(id string) {
	data := loadWAF()
	var cfg map[string]any
	if arr, ok := data["sites"].([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok && str(m["site"]) == id {
				cfg = m
			}
		}
	}
	dir := webServerBaseDir() + "/waf"
	_ = os.MkdirAll(HostPath(dir), 0o755)
	if cfg != nil && boolOf(data["enabled"]) && boolOf(cfg["enabled"]) && siteIDRe.MatchString(id) {
		_ = writeHostFile(dir+"/"+id+".conf", wafNginxConfig(cfg))
	} else {
		_ = os.Remove(HostPath(dir + "/" + id + ".conf"))
	}
	for _, site := range loadSites() {
		if str(site["id"]) == id && boolOf(site["enabled"]) {
			_ = applySiteConfig(site, true)
		}
	}
	reloadIfNginx()
}

func registerWAFRoutes(a *App) {
	a.Get("/api/waf/status", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		data := loadWAF()
		platform := "linux"
		if windowsTarget() {
			platform = "windows"
		}
		return WriteJSON(w, 200, map[string]any{
			"enabled": boolOf(data["enabled"]), "platform": platform,
			"nginx_available": webServerAvailable("nginx") || webServerAvailable("openresty"),
			"waf_dir":         webServerBaseDir() + "/waf",
		})
	})
	a.Post("/api/waf/toggle", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		var body map[string]any
		_ = DecodeJSON(r, &body)
		enabled := boolOf(body["enabled"])
		data := loadWAF()
		data["enabled"] = enabled
		if err := saveWAF(data); err != nil {
			return err
		}
		wafApplyAll(data)
		return WriteJSON(w, 200, map[string]any{"enabled": enabled})
	})
	a.Get("/api/waf/sites", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		enabledMap := map[string]bool{}
		if arr, ok := loadWAF()["sites"].([]any); ok {
			for _, item := range arr {
				if m, ok := item.(map[string]any); ok {
					enabledMap[str(m["site"])] = boolOf(m["enabled"])
				}
			}
		}
		result := []map[string]any{}
		for _, s := range loadSites() {
			name := str(s["id"])
			result = append(result, map[string]any{
				"site": name, "enabled": enabledMap[name], "secured": enabledMap[name],
			})
		}
		return WriteJSON(w, 200, map[string]any{"sites": result, "global_enabled": boolOf(loadWAF()["enabled"])})
	})
	a.Get("/api/waf/site/{site_id}", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "site_id")
		if arr, ok := loadWAF()["sites"].([]any); ok {
			for _, item := range arr {
				if m, ok := item.(map[string]any); ok && str(m["site"]) == id {
					return WriteJSON(w, 200, m)
				}
			}
		}
		return WriteJSON(w, 200, wafDefaultConfig(id))
	})
	a.Put("/api/waf/site/{site_id}", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "site_id")
		var body map[string]any
		if err := DecodeJSON(r, &body); err != nil {
			return err
		}
		body["site"] = id
		body["updated_at"] = time.Now().Format("2006-01-02T15:04:05")
		data := loadWAF()
		arr := []map[string]any{}
		replaced := false
		if old, ok := data["sites"].([]any); ok {
			for _, item := range old {
				m, ok := item.(map[string]any)
				if !ok {
					continue
				}
				if str(m["site"]) == id {
					arr = append(arr, body)
					replaced = true
				} else {
					arr = append(arr, m)
				}
			}
		}
		if !replaced {
			arr = append(arr, body)
		}
		data["sites"] = arr
		if err := saveWAF(data); err != nil {
			return err
		}
		wafApplyAll(data)
		return WriteJSON(w, 200, map[string]any{"saved": true, "config": body})
	})
	a.Post("/api/waf/site/{site_id}/disable", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "site_id")
		data := loadWAF()
		if arr, ok := data["sites"].([]any); ok {
			for _, item := range arr {
				if m, ok := item.(map[string]any); ok && str(m["site"]) == id {
					m["enabled"] = false
				}
			}
		}
		_ = saveWAF(data)
		_ = os.Remove(HostPath(webServerBaseDir() + "/waf/" + id + ".conf"))
		wafRewriteSite(id)
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Get("/api/waf/preview", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		id := r.URL.Query().Get("site_id")
		cfg := wafDefaultConfig(id)
		if arr, ok := loadWAF()["sites"].([]any); ok {
			for _, item := range arr {
				if m, ok := item.(map[string]any); ok && str(m["site"]) == id {
					cfg = m
				}
			}
		}
		content := wafNginxConfig(cfg)
		return WriteJSON(w, 200, map[string]any{"site": id, "content": content})
	})
	a.Post("/api/waf/apply", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		data := loadWAF()
		if !boolOf(data["enabled"]) {
			wafApplyAll(data)
			return WriteJSON(w, 200, map[string]any{"ok": false, "message": "全局 WAF 未开启，已移除站点 WAF 引用"})
		}
		written := wafApplyAll(data)
		return WriteJSON(w, 200, map[string]any{"ok": true, "written": written})
	})
	a.Post("/api/waf/logs/record", customAuth(), func(w http.ResponseWriter, r *http.Request) error {
		// 供 nginx / 外部脚本上报拦截记录（管理员登录或插件令牌均可）。
		authed := false
		if BearerToken(r) != "" {
			if _, err := Authenticate(r, true); err == nil {
				authed = true
			}
		}
		if !authed {
			if _, err := pluginAuth(r); err == nil {
				authed = true
			}
		}
		if !authed {
			return NewHTTPError(401, "未认证")
		}
		var logs []map[string]any
		_ = ReadJSON(DataPath("waf_logs.json"), &logs)
		var entry map[string]any
		if err := DecodeJSON(r, &entry); err == nil {
			entry["time"] = time.Now().Format("2006-01-02 15:04:05")
			logs = append([]map[string]any{entry}, logs...)
			if len(logs) > 2000 {
				logs = logs[:2000]
			}
			_ = WriteJSONFile(DataPath("waf_logs.json"), logs)
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "count": len(logs)})
	})
	a.Get("/api/waf/logs", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		var logs []map[string]any
		_ = ReadJSON(DataPath("waf_logs.json"), &logs)
		site := r.URL.Query().Get("site")
		action := r.URL.Query().Get("action")
		ip := r.URL.Query().Get("ip")
		limit := QueryInt(r, "limit", 200)
		out := []map[string]any{}
		for _, l := range logs {
			if site != "" && str(l["site"]) != site {
				continue
			}
			if action != "" && str(l["action"]) != action {
				continue
			}
			if ip != "" && str(l["ip"]) != ip {
				continue
			}
			out = append(out, l)
		}
		total := len(out)
		if limit > 0 && len(out) > limit {
			out = out[:limit]
		}
		return WriteJSON(w, 200, map[string]any{"logs": out, "total": total})
	})
	a.Post("/api/waf/logs/clear", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		_ = WriteJSONFile(DataPath("waf_logs.json"), []any{})
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Get("/api/waf/blockmap", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		var logs []map[string]any
		_ = ReadJSON(DataPath("waf_logs.json"), &logs)
		counts := map[string]int{}
		for _, l := range logs {
			ip := str(l["ip"])
			if ip != "" {
				counts[ip]++
			}
		}
		items := []map[string]any{}
		for ip, c := range counts {
			items = append(items, map[string]any{"ip": ip, "count": c})
		}
		sort.Slice(items, func(i, j int) bool {
			return int(int64Of(items[i]["count"])) > int(int64Of(items[j]["count"]))
		})
		if len(items) > 100 {
			items = items[:100]
		}
		return WriteJSON(w, 200, map[string]any{"items": items})
	})
}
