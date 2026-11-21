package graw

import (
	"net"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// 网站监控增强（P1）：在原有 PV/UV 聚合之外补四类视图：
//   - realtime：最近请求、每秒请求数、每秒流量；
//   - requests：按方法/状态码/关键词过滤的请求明细；
//   - summary：按日志文件（站点）汇总的请求量、流量与错误率；
//   - visitors：访客 IP 分布与地区聚合。
//
// 地区解析不内置 GeoIP 数据库：优先读取 data/geoip_countries.json
// （[{"cidr":"1.2.3.0/24","country":"CN"}]），未命中时内网地址标「内网」，
// 其余标「未知」。这样不引入额外依赖，也不会把查询发到第三方服务。

type webAccessEntry struct {
	Time    string `json:"time"`
	IP      string `json:"ip"`
	Method  string `json:"method"`
	Path    string `json:"path"`
	Status  int    `json:"status"`
	Bytes   int64  `json:"bytes"`
	Referer string `json:"referer"`
	UA      string `json:"ua"`
}

func webstatsLogPath(path string) (string, string, error) {
	if path == "" {
		found := discoverAccessLogs()
		if len(found) == 0 {
			return "", "", NewHTTPError(400, "未在常见路径找到 nginx 访问日志，请手动指定日志路径")
		}
		path = found[0]
	}
	if strings.Contains(path, "..") || strings.HasPrefix(path, `\\`) || strings.HasPrefix(path, "//") {
		return "", "", NewHTTPError(400, "非法日志路径")
	}
	if forbiddenDataPath(path) {
		return "", "", NewHTTPError(403, "无权访问面板数据目录")
	}
	real := HostPath(path)
	st, err := os.Stat(real)
	if err != nil || st.IsDir() {
		return "", "", NewHTTPError(400, "日志文件不存在: "+path)
	}
	return path, real, nil
}

func readTailText(real string, maxBytes int64) string {
	f, err := os.Open(real)
	if err != nil {
		return ""
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return ""
	}
	offset := int64(0)
	if st.Size() > maxBytes {
		offset = st.Size() - maxBytes
	}
	if _, err := f.Seek(offset, 0); err != nil {
		return ""
	}
	buf := make([]byte, st.Size()-offset)
	n, _ := f.Read(buf)
	return string(buf[:n])
}

// parseAccessEntries 解析 combined 日志；无法解析的行直接跳过。
func parseAccessEntries(text string, limit int) []webAccessEntry {
	out := []webAccessEntry{}
	for _, line := range strings.Split(text, "\n") {
		m := combinedLogRe.FindStringSubmatch(strings.TrimSpace(line))
		if m == nil {
			continue
		}
		request := m[4]
		parts := strings.Split(request, " ")
		method, reqPath := "", request
		if len(parts) >= 2 {
			method, reqPath = parts[0], parts[1]
		}
		out = append(out, webAccessEntry{
			Time: m[3], IP: m[1], Method: method, Path: reqPath,
			Status: int(int64Of(m[5])), Bytes: int64Of(m[6]), Referer: m[7], UA: m[8],
		})
	}
	if limit > 0 && len(out) > limit {
		out = out[len(out)-limit:]
	}
	return out
}

var geoCountryRules []map[string]any
var geoCountryLoaded bool

func loadGeoCountryRules() []map[string]any {
	if geoCountryLoaded {
		return geoCountryRules
	}
	geoCountryLoaded = true
	var items []map[string]any
	_ = ReadJSON(DataPath("geoip_countries.json"), &items)
	geoCountryRules = items
	return geoCountryRules
}

func visitorRegion(ipStr string) string {
	ip := net.ParseIP(ipStr)
	if ip == nil {
		return "未知"
	}
	if ip.IsLoopback() || ip.IsPrivate() {
		return "内网"
	}
	for _, rule := range loadGeoCountryRules() {
		cidr := str(rule["cidr"])
		if cidr == "" {
			continue
		}
		_, network, err := net.ParseCIDR(cidr)
		if err == nil && network.Contains(ip) {
			return defaultString(str(rule["country"]), "未知")
		}
	}
	return "未知"
}

func webstatsRealtime(path string, limit int) (map[string]any, error) {
	display, real, err := webstatsLogPath(path)
	if err != nil {
		return nil, err
	}
	if limit < 1 || limit > 200 {
		limit = 50
	}
	text := readTailText(real, 512*1024)
	all := parseAccessEntries(text, 0)
	entries := all
	if len(entries) > limit {
		entries = entries[len(entries)-limit:]
	}
	now := time.Now()
	window := now.Add(-60 * time.Second)
	rpsCount := 0
	bytesCount := int64(0)
	// 统计窗口必须基于全部尾部记录，不能只用展示用的最近 limit 条，
	// 否则高流量时会把 rps / 每秒流量算小。
	for _, e := range all {
		tm := parseNginxTime(e.Time)
		if tm.IsZero() || tm.Before(window) {
			continue
		}
		rpsCount++
		bytesCount += e.Bytes
	}
	return map[string]any{
		"file": display, "entries": entries,
		"rps":            float64(rpsCount) / 60.0,
		"bytes_per_sec":  float64(bytesCount) / 60.0,
		"window_seconds": 60,
	}, nil
}

func webstatsRequests(path, query, method, status string, limit int) (map[string]any, error) {
	display, real, err := webstatsLogPath(path)
	if err != nil {
		return nil, err
	}
	if limit < 1 || limit > 1000 {
		limit = 200
	}
	text := readTailText(real, 8*1024*1024)
	entries := parseAccessEntries(text, 0)
	q := strings.ToLower(strings.TrimSpace(query))
	method = strings.ToUpper(strings.TrimSpace(method))
	status = strings.TrimSpace(status)
	out := []webAccessEntry{}
	for i := len(entries) - 1; i >= 0 && len(out) < limit; i-- {
		e := entries[i]
		if method != "" && e.Method != method {
			continue
		}
		if status != "" && itoa(e.Status) != status {
			continue
		}
		if q != "" {
			hay := strings.ToLower(e.IP + " " + e.Path + " " + e.UA + " " + e.Referer)
			if !strings.Contains(hay, q) {
				continue
			}
		}
		out = append(out, e)
	}
	return map[string]any{"file": display, "entries": out}, nil
}

func webstatsSummary() map[string]any {
	logs := discoverAccessLogs()
	if len(logs) > 20 {
		logs = logs[:20]
	}
	items := []map[string]any{}
	for _, path := range logs {
		_, real, err := webstatsLogPath(path)
		if err != nil {
			continue
		}
		text := readTailText(real, 4*1024*1024)
		entries := parseAccessEntries(text, 0)
		if len(entries) == 0 {
			items = append(items, map[string]any{"file": path, "site": filepath.Base(filepath.Dir(path)), "pv": 0, "uv": 0, "bytes": 0, "errors": 0})
			continue
		}
		ipSet := map[string]bool{}
		bytes := int64(0)
		errors := 0
		for _, e := range entries {
			ipSet[e.IP] = true
			bytes += e.Bytes
			if e.Status >= 400 {
				errors++
			}
		}
		items = append(items, map[string]any{
			"file": path, "site": filepath.Base(filepath.Dir(path)),
			"pv": len(entries), "uv": len(ipSet), "bytes": bytes, "errors": errors,
			"error_rate": float64(errors) * 100 / float64(len(entries)),
		})
	}
	sort.Slice(items, func(i, j int) bool { return int64Of(items[i]["pv"]) > int64Of(items[j]["pv"]) })
	return map[string]any{"items": items, "count": len(items)}
}

func webstatsVisitors(path string, limit int) (map[string]any, error) {
	display, real, err := webstatsLogPath(path)
	if err != nil {
		return nil, err
	}
	if limit < 1 || limit > 100 {
		limit = 20
	}
	text := readTailText(real, 4*1024*1024)
	entries := parseAccessEntries(text, 0)
	ips := map[string]int{}
	regions := map[string]int{}
	for _, e := range entries {
		ips[e.IP]++
	}
	type kv struct {
		K string
		V int
	}
	top := []kv{}
	for ip, count := range ips {
		regions[visitorRegion(ip)] += count
		top = append(top, kv{ip, count})
	}
	sort.Slice(top, func(i, j int) bool { return top[i].V > top[j].V })
	if len(top) > limit {
		top = top[:limit]
	}
	topOut := []map[string]any{}
	for _, t := range top {
		topOut = append(topOut, map[string]any{"ip": t.K, "count": t.V, "region": visitorRegion(t.K)})
	}
	regionOut := []map[string]any{}
	for name, count := range regions {
		regionOut = append(regionOut, map[string]any{"region": name, "count": count})
	}
	sort.Slice(regionOut, func(i, j int) bool { return int64Of(regionOut[i]["count"]) > int64Of(regionOut[j]["count"]) })
	return map[string]any{
		"file": display, "regions": regionOut, "top_ips": topOut,
		"ip_count": len(ips), "has_geoip": len(loadGeoCountryRules()) > 0,
	}, nil
}

func registerWebStatsEnhancedRoutes(a *App) {
	a.Get("/api/webstats/realtime", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		res, err := webstatsRealtime(r.URL.Query().Get("log_path"), QueryInt(r, "limit", 50))
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, res)
	})
	a.Get("/api/webstats/requests", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		res, err := webstatsRequests(
			r.URL.Query().Get("log_path"), r.URL.Query().Get("q"),
			r.URL.Query().Get("method"), r.URL.Query().Get("status"),
			QueryInt(r, "limit", 200))
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, res)
	})
	a.Get("/api/webstats/summary", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, webstatsSummary())
	})
	a.Get("/api/webstats/visitors", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		res, err := webstatsVisitors(r.URL.Query().Get("log_path"), QueryInt(r, "limit", 20))
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, res)
	})
}
