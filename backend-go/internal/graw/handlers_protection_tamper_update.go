package graw

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

// 保护机制（数据库文件备份）/ 网页防篡改 / 面板自身更新。

// --- 保护机制 ---

func loadProtection() map[string]any {
	data := map[string]any{"ignored": []any{}, "backup_items": []any{}}
	_ = ReadJSON(DataPath("protection.json"), &data)
	if data == nil {
		return map[string]any{"ignored": []any{}, "backup_items": []any{}}
	}
	return data
}

func protectionList(data map[string]any, key string) []map[string]any {
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

var dbFileCandidates = []string{
	"/var/lib/mysql", "/var/lib/mariadb", "/var/lib/postgresql",
	"/data/mysql", "/data/postgres", "/opt/mysql", "/opt/mariadb",
}

func registerProtectionRoutes(a *App) {
	a.Get("/api/protection/status", perm("firewall"), func(w http.ResponseWriter, r *http.Request) error {
		data := loadProtection()
		version, _ := dockerAvailable()
		return WriteJSON(w, 200, map[string]any{
			"docker_available": version != "",
			"ignored_count":    len(protectionCurrentIgnored()),
			"backup_count":     len(protectionList(data, "backup_items")),
			"backup_dir":       protectionBackupDir(),
		})
	})
	a.Get("/api/protection/docker", perm("firewall"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, protectionScanDocker())
	})
	a.Post("/api/protection/docker/{name}/map", perm("firewall"), func(w http.ResponseWriter, r *http.Request) error {
		name := PathParam(r, "name")
		if !containerIDRe.MatchString(name) {
			return NewHTTPError(400, "非法容器名")
		}
		res, err := mapDockerContainerData(name)
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, res)
	})
	a.Get("/api/protection/db-files", perm("firewall"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, protectionScanDBFiles())
	})
	a.Post("/api/protection/db-files/backup", perm("firewall"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Path     string `json:"path"`
			Schedule string `json:"schedule"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		res, _, err := protectionAddBackup(req.Path, strings.TrimSpace(req.Schedule))
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, res)
	})
	a.Post("/api/protection/db-files/unbackup", perm("firewall"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Path string `json:"path"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		data := loadProtection()
		kept := []map[string]any{}
		for _, item := range protectionList(data, "backup_items") {
			if str(item["path"]) != req.Path {
				kept = append(kept, item)
			}
		}
		data["backup_items"] = kept
		if err := WriteJSONFile(DataPath("protection.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/protection/db-files/batch-backup", perm("firewall"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Paths []string `json:"paths"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		results := []map[string]any{}
		for _, p := range req.Paths {
			p = strings.TrimSpace(p)
			if p == "" {
				continue
			}
			res, already, err := protectionAddBackup(p, "")
			if err != nil {
				results = append(results, map[string]any{"path": p, "ok": false, "error": "加入备份失败"})
				continue
			}
			if already {
				results = append(results, map[string]any{"path": p, "ok": true, "already": true})
			} else {
				results = append(results, map[string]any{
					"path": p, "ok": true, "already": false,
					"task_id": str(mapOf(res["task"])["id"]),
				})
			}
		}
		return WriteJSON(w, 200, map[string]any{"results": results})
	})
	a.Post("/api/protection/ignore", perm("firewall"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Kind      string `json:"kind"`
			Key       string `json:"key"`
			Name      string `json:"name"`
			Permanent bool   `json:"permanent"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if req.Kind != "docker" && req.Kind != "db_file" {
			return NewHTTPError(400, "kind 必须是 docker 或 db_file")
		}
		key := strings.TrimSpace(req.Key)
		if key == "" {
			return NewHTTPError(400, "key 不能为空")
		}
		data := loadProtection()
		items := protectionList(data, "ignored")
		now := time.Now().Format("2006-01-02T15:04:05")
		for _, item := range items {
			if str(item["kind"]) == req.Kind && str(item["key"]) == key {
				item["ignored_at"] = now
				item["permanent"] = req.Permanent
				if req.Name != "" {
					item["name"] = req.Name
				}
				data["ignored"] = items
				if err := WriteJSONFile(DataPath("protection.json"), data); err != nil {
					return err
				}
				return WriteJSON(w, 200, map[string]any{"ok": true, "already": true, "permanent": req.Permanent})
			}
		}
		items = append(items, map[string]any{
			"kind": req.Kind, "key": key, "name": req.Name,
			"ignored_at": now, "permanent": req.Permanent,
		})
		data["ignored"] = items
		if err := WriteJSONFile(DataPath("protection.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "already": false, "permanent": req.Permanent})
	})
	a.Post("/api/protection/unignore", perm("firewall"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Kind string `json:"kind"`
			Key  string `json:"key"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		data := loadProtection()
		kept := []map[string]any{}
		for _, item := range protectionList(data, "ignored") {
			if !(str(item["kind"]) == req.Kind && str(item["key"]) == req.Key) {
				kept = append(kept, item)
			}
		}
		data["ignored"] = kept
		if err := WriteJSONFile(DataPath("protection.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Get("/api/protection/ignored", perm("firewall"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"ignored": protectionCurrentIgnored()})
	})
	a.Get("/api/protection/backups", perm("firewall"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"backups": protectionList(loadProtection(), "backup_items")})
	})
}

var protectionMu sync.Mutex

// dbDataPathForImage 返回常见数据库镜像的数据目录。
func dbDataPathForImage(image string) string {
	img := strings.ToLower(image)
	switch {
	case strings.Contains(img, "mysql"), strings.Contains(img, "mariadb"):
		return "/var/lib/mysql"
	case strings.Contains(img, "postgres"):
		return "/var/lib/postgresql/data"
	case strings.Contains(img, "mongo"):
		return "/data/db"
	case strings.Contains(img, "redis"):
		return "/data"
	}
	return ""
}

// mapDockerContainerData 把数据库容器的数据目录迁移到命名卷并重建容器。
func mapDockerContainerData(name string) (map[string]any, error) {
	raw, err := dockerInspectRaw(name)
	if err != nil {
		return nil, NewHTTPError(404, "容器不存在")
	}
	config := mapOf(raw["Config"])
	image := str(config["Image"])
	dataPath := dbDataPathForImage(image)
	if dataPath == "" {
		return nil, NewHTTPError(400, "该容器镜像不是已支持的数据库类型（MySQL/MariaDB/PostgreSQL/MongoDB/Redis）")
	}
	cname := strings.TrimPrefix(str(raw["Name"]), "/")
	if cname == "" {
		cname = name
	}
	for _, m := range sliceOf(raw["Mounts"]) {
		mm := mapOf(m)
		if str(mm["Destination"]) == dataPath {
			typ := str(mm["Type"])
			if typ == "bind" || (typ == "volume" && !strings.HasPrefix(str(mm["Name"]), "/")) {
				return map[string]any{
					"ok": true, "already": true, "container": cname,
					"volume": str(mm["Name"]), "path": dataPath,
					"message": "数据目录已持久化映射，无需迁移",
				}, nil
			}
		}
	}
	volume := "graw_" + strings.NewReplacer("/", "_", ".", "_", ":", "_").Replace(cname) + "_data"
	if res := dockerRun(60*time.Second, "volume", "create", volume); res.Code != 0 {
		return nil, NewHTTPError(500, "创建命名卷失败: "+strings.TrimSpace(res.Stderr+res.Stdout))
	}
	// 停止旧容器，保证复制期间数据一致。
	_ = dockerRun(120*time.Second, "stop", cname)
	helper := "graw_map_tmp_" + randomHex(4)
	dockerRun(60*time.Second, "rm", "-f", helper)
	if res := dockerRun(180*time.Second, "run", "-d", "--name", helper,
		"-v", volume+":/graw-target", "busybox", "sleep", "3600"); res.Code != 0 {
		return nil, NewHTTPError(500, "创建迁移辅助容器失败（需要 busybox 镜像）: "+strings.TrimSpace(res.Stderr+res.Stdout))
	}
	// 将旧容器数据复制进辅助容器的命名卷。
	dockerRun(600*time.Second, "cp", cname+":"+dataPath+"/.", helper+":/graw-target/")
	dockerRun(60*time.Second, "rm", "-f", helper)
	// 在 inspect 数据中追加命名卷挂载后重建。
	mounts := sliceOf(raw["Mounts"])
	filtered := []any{}
	for _, m := range mounts {
		if str(mapOf(m)["Destination"]) != dataPath {
			filtered = append(filtered, m)
		}
	}
	filtered = append(filtered, map[string]any{
		"Type": "volume", "Name": volume, "Source": volume, "Destination": dataPath,
	})
	raw["Mounts"] = filtered
	newID, err := dockerRecreate(raw, nil, nil)
	if err != nil {
		return nil, NewHTTPError(500, "重建容器失败: "+err.Error())
	}
	return map[string]any{
		"ok": true, "already": false, "container": cname,
		"volume": volume, "path": dataPath, "new_container_id": newID,
		"message": "已迁移到命名卷并重建容器",
	}, nil
}

func createProtectionBackup(w http.ResponseWriter, paths []string, name string) error {
	protectionMu.Lock()
	defer protectionMu.Unlock()
	dir := DataPath("db_backups")
	_ = EnsureDir(dir)
	data := loadProtection()
	items := protectionList(data, "backup_items")
	for _, p := range paths {
		real := HostPath(p)
		if !Exists(real) {
			continue
		}
		base := filepath.Base(p)
		if base == "" || base == "." {
			base = "db"
		}
		archive := filepath.Join(dir, base+"_"+time.Now().Format("20060102_150405")+".tar.gz")
		if err := createArchive(archive, []string{real}, "tar.gz"); err != nil {
			return err
		}
		items = append(items, map[string]any{
			"path": p, "archive": archive, "name": defaultString(name, base),
			"created_at": time.Now().Format("2006-01-02T15:04:05"),
		})
	}
	data["backup_items"] = items
	if err := WriteJSONFile(DataPath("protection.json"), data); err != nil {
		return err
	}
	return WriteJSON(w, 200, map[string]any{"ok": true, "path": paths[0]})
}

// --- 网页防篡改 ---

const defaultBackupInterval = 30
const defaultScanInterval = 60

var defaultIgnorePatterns = []string{".git", "node_modules", "*.log", "*.tmp", "cache", ".well-known"}

func loadTamper() map[string]any {
	data := map[string]any{"enabled": true, "disabled_until": nil, "sites": []any{}, "history": []any{}}
	_ = ReadJSON(DataPath("tamper.json"), &data)
	if data == nil {
		return map[string]any{"enabled": true, "sites": []any{}, "history": []any{}}
	}
	return data
}

func tamperSites(data map[string]any) []map[string]any {
	out := []map[string]any{}
	if arr, ok := data["sites"].([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok {
				out = append(out, m)
			}
		}
	}
	return out
}

func tamperSummary(site map[string]any) map[string]any {
	root := str(site["root"])
	protected := toStringSlice(site["protected_files"])
	ignores := toStringSlice(site["ignore_patterns"])
	return map[string]any{
		"site_id": str(site["site_id"]), "site_name": defaultString(str(site["site_name"]), str(site["site_id"])),
		"root": root, "enabled": boolOf(site["enabled"]),
		"protected_files": protected, "protected_count": len(protected),
		"ignore_patterns": ignores, "ignore_count": len(ignores),
		"default_ignore_patterns": defaultIgnorePatterns,
		"backup_interval_minutes": site["backup_interval_minutes"],
		"scan_interval_seconds":   site["scan_interval_seconds"],
		"last_backup_at":          site["last_backup_at"],
		"last_scan_at":            site["last_scan_at"],
		"last_tamper_at":          site["last_tamper_at"],
		"root_exists":             IsDir(HostPath(root)),
	}
}

func tamperSiteBackupDir(siteID string) string {
	return DataPath("tamper_backups", siteID)
}

func siteResolveFile(root, rel string) (string, bool) {
	rel = strings.TrimLeft(strings.ReplaceAll(rel, "\\", "/"), "/")
	if !filepath.IsLocal(rel) {
		return "", false
	}
	full := filepath.Join(HostPath(root), filepath.FromSlash(rel))
	base := filepath.Clean(HostPath(root))
	if !withinBase(base, filepath.Clean(full)) {
		return "", false
	}
	return full, true
}

func fileSHA256(path string) string {
	f, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer f.Close()
	h := sha256.New()
	_, _ = io.Copy(h, f)
	return hex.EncodeToString(h.Sum(nil))
}

func tamperBackupSite(site map[string]any) int {
	siteID := str(site["site_id"])
	root := str(site["root"])
	backupDir := tamperSiteBackupDir(siteID)
	_ = EnsureDir(backupDir)
	hashes := map[string]any{}
	count := 0
	for _, rel := range toStringSlice(site["protected_files"]) {
		real, ok := siteResolveFile(root, rel)
		if !ok || !Exists(real) {
			continue
		}
		dst := filepath.Join(backupDir, filepath.FromSlash(strings.TrimLeft(rel, "/")))
		_ = os.MkdirAll(filepath.Dir(dst), 0o700)
		_ = CopyFile(real, dst)
		hashes[rel] = fileSHA256(real)
		count++
	}
	site["hashes"] = hashes
	site["last_backup_at"] = time.Now().Format("2006-01-02T15:04:05")
	return count
}

func discoverProtectedFiles(root string, ignores []string) []string {
	out := []string{}
	real := HostPath(root)
	if !IsDir(real) {
		return out
	}
	_ = filepath.Walk(real, func(p string, info os.FileInfo, err error) error {
		if err != nil || info == nil || info.IsDir() {
			return nil
		}
		rel, err := filepath.Rel(real, p)
		if err != nil {
			return nil
		}
		rel = filepath.ToSlash(rel)
		for _, ig := range ignores {
			if ok, _ := filepath.Match(ig, filepath.Base(rel)); ok || strings.Contains(rel, ig+"/") {
				return nil
			}
		}
		out = append(out, rel)
		if len(out) >= 2000 {
			return io.EOF
		}
		return nil
	})
	sort.Strings(out)
	return out
}

var (
	tamperMu sync.Mutex
)

func registerTamperRoutes(a *App) {
	read := protected()
	write := perm("tamper")
	a.Get("/api/tamper/status", read, func(w http.ResponseWriter, r *http.Request) error {
		data := loadTamper()
		sites := tamperSites(data)
		enabledSites := 0
		for _, s := range sites {
			if boolOf(s["enabled"]) {
				enabledSites++
			}
		}
		history := []map[string]any{}
		if arr, ok := data["history"].([]any); ok {
			for _, item := range arr {
				if m, ok := item.(map[string]any); ok {
					history = append(history, m)
				}
			}
		}
		var last any
		if len(history) > 0 {
			last = history[len(history)-1]
		}
		disabledUntil := data["disabled_until"]
		return WriteJSON(w, 200, map[string]any{
			"enabled": boolOf(data["enabled"]), "disabled_until": disabledUntil,
			"temporarily_disabled": disabledUntil != nil,
			"site_count":           len(sites), "enabled_site_count": enabledSites,
			"last_event": last, "sites": summaries(sites),
		})
	})
	a.Get("/api/tamper/sites", read, func(w http.ResponseWriter, r *http.Request) error {
		data := loadTamper()
		candidates := []map[string]any{}
		for _, st := range loadSites() {
			candidates = append(candidates, map[string]any{
				"site_id": str(st["id"]), "name": str(st["name"]), "type": str(st["type"]),
				"root": str(st["root"]), "enabled": boolOf(st["enabled"]),
			})
		}
		return WriteJSON(w, 200, map[string]any{
			"protections": summaries(tamperSites(data)), "candidates": candidates,
		})
	})
	a.Get("/api/tamper/sites/{site_id}", read, func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "site_id")
		for _, s := range tamperSites(loadTamper()) {
			if str(s["site_id"]) == id {
				return WriteJSON(w, 200, tamperSummary(s))
			}
		}
		return NewHTTPError(404, "未找到该站点的防篡改配置")
	})
	a.Post("/api/tamper/sites", write, func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			SiteID                string   `json:"site_id"`
			SiteName              string   `json:"site_name"`
			Root                  string   `json:"root"`
			ProtectedFiles        []string `json:"protected_files"`
			IgnorePatterns        []string `json:"ignore_patterns"`
			BackupIntervalMinutes int      `json:"backup_interval_minutes"`
			ScanIntervalSeconds   int      `json:"scan_interval_seconds"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if req.SiteID == "" || req.Root == "" {
			return NewHTTPError(400, "site_id 与 root 必填")
		}
		data := loadTamper()
		sites := tamperSites(data)
		for _, s := range sites {
			if str(s["site_id"]) == req.SiteID {
				return NewHTTPError(400, "该站点已配置防篡改")
			}
		}
		protected := req.ProtectedFiles
		if len(protected) == 0 {
			protected = discoverProtectedFiles(req.Root, req.IgnorePatterns)
		}
		site := map[string]any{
			"site_id": req.SiteID, "site_name": defaultString(req.SiteName, req.SiteID),
			"root": req.Root, "enabled": true, "protected_files": protected,
			"ignore_patterns": req.IgnorePatterns,
			"backup_interval_minutes": func() int {
				if req.BackupIntervalMinutes <= 0 {
					return defaultBackupInterval
				}
				return req.BackupIntervalMinutes
			}(),
			"scan_interval_seconds": func() int {
				if req.ScanIntervalSeconds <= 0 {
					return defaultScanInterval
				}
				return req.ScanIntervalSeconds
			}(),
		}
		tamperMu.Lock()
		tamperBackupSite(site)
		sites = append(sites, site)
		data["sites"] = sites
		err := WriteJSONFile(DataPath("tamper.json"), data)
		tamperMu.Unlock()
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, tamperSummary(site))
	})
	a.Put("/api/tamper/sites/{site_id}", write, func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "site_id")
		var req map[string]any
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		tamperMu.Lock()
		defer tamperMu.Unlock()
		data := loadTamper()
		sites := tamperSites(data)
		var site map[string]any
		for _, s := range sites {
			if str(s["site_id"]) == id {
				site = s
				for _, k := range []string{"site_name", "root", "protected_files", "ignore_patterns",
					"backup_interval_minutes", "scan_interval_seconds", "enabled"} {
					if v, ok := req[k]; ok {
						s[k] = v
					}
				}
			}
		}
		if site == nil {
			return NewHTTPError(404, "未找到该站点的防篡改配置")
		}
		tamperBackupSite(site)
		data["sites"] = sites
		if err := WriteJSONFile(DataPath("tamper.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, tamperSummary(site))
	})
	a.Delete("/api/tamper/sites/{site_id}", write, func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "site_id")
		data := loadTamper()
		kept := []map[string]any{}
		for _, s := range tamperSites(data) {
			if str(s["site_id"]) != id {
				kept = append(kept, s)
			}
		}
		data["sites"] = kept
		_ = WriteJSONFile(DataPath("tamper.json"), data)
		_ = os.RemoveAll(tamperSiteBackupDir(id))
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/tamper/sites/{site_id}/backup-now", write, func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "site_id")
		tamperMu.Lock()
		defer tamperMu.Unlock()
		data := loadTamper()
		sites := tamperSites(data)
		var site map[string]any
		for _, s := range sites {
			if str(s["site_id"]) == id {
				site = s
			}
		}
		if site == nil {
			return NewHTTPError(404, "未找到该站点的防篡改配置")
		}
		count := tamperBackupSite(site)
		data["sites"] = sites
		_ = WriteJSONFile(DataPath("tamper.json"), data)
		return WriteJSON(w, 200, map[string]any{
			"ok": true, "protected_count": count, "last_backup_at": site["last_backup_at"],
		})
	})
	a.Post("/api/tamper/sites/{site_id}/scan-now", write, func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "site_id")
		tamperMu.Lock()
		defer tamperMu.Unlock()
		data := loadTamper()
		sites := tamperSites(data)
		var site map[string]any
		for _, s := range sites {
			if str(s["site_id"]) == id {
				site = s
			}
		}
		if site == nil {
			return NewHTTPError(404, "未找到该站点的防篡改配置")
		}
		events := tamperScan(site)
		site["last_scan_at"] = time.Now().Format("2006-01-02T15:04:05")
		if len(events) > 0 {
			site["last_tamper_at"] = site["last_scan_at"]
		}
		history := []map[string]any{}
		if arr, ok := data["history"].([]any); ok {
			for _, item := range arr {
				if m, ok := item.(map[string]any); ok {
					history = append(history, m)
				}
			}
		}
		history = append(history, events...)
		if len(history) > 200 {
			history = history[len(history)-200:]
		}
		data["history"] = history
		data["sites"] = sites
		_ = WriteJSONFile(DataPath("tamper.json"), data)
		return WriteJSON(w, 200, map[string]any{"ok": true, "tampered_count": len(events), "events": events})
	})
	a.Post("/api/tamper/sites/{site_id}/restore", write, func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "site_id")
		var req struct {
			File string `json:"file"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		tamperMu.Lock()
		defer tamperMu.Unlock()
		data := loadTamper()
		for _, site := range tamperSites(data) {
			if str(site["site_id"]) != id {
				continue
			}
			src := filepath.Join(tamperSiteBackupDir(id), filepath.FromSlash(strings.TrimLeft(req.File, "/")))
			dst, ok := siteResolveFile(str(site["root"]), req.File)
			if !ok || !Exists(src) {
				return NewHTTPError(404, "备份文件不存在")
			}
			if err := CopyFile(src, dst); err != nil {
				return NewHTTPError(500, "恢复失败: "+err.Error())
			}
			return WriteJSON(w, 200, map[string]any{"ok": true, "file": req.File})
		}
		return NewHTTPError(404, "未找到该站点的防篡改配置")
	})
	a.Post("/api/tamper/disable", write, func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Minutes int `json:"minutes"`
		}
		_ = DecodeJSON(r, &req)
		data := loadTamper()
		until := ""
		if req.Minutes > 0 {
			until = time.Now().Add(time.Duration(req.Minutes) * time.Minute).Format("2006-01-02T15:04:05")
		}
		data["enabled"] = false
		data["disabled_until"] = until
		_ = WriteJSONFile(DataPath("tamper.json"), data)
		return WriteJSON(w, 200, map[string]any{"ok": true, "disabled_until": until})
	})
	a.Post("/api/tamper/enable", write, func(w http.ResponseWriter, r *http.Request) error {
		data := loadTamper()
		data["enabled"] = true
		data["disabled_until"] = nil
		_ = WriteJSONFile(DataPath("tamper.json"), data)
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Get("/api/tamper/history", read, func(w http.ResponseWriter, r *http.Request) error {
		history := []any{}
		if arr, ok := loadTamper()["history"].([]any); ok {
			history = arr
		}
		return WriteJSON(w, 200, map[string]any{"history": history})
	})
	a.wsRoute("/api/tamper/ws", tamperWS)
}

func summaries(sites []map[string]any) []map[string]any {
	out := []map[string]any{}
	for _, s := range sites {
		out = append(out, tamperSummary(s))
	}
	return out
}

// StartTamperMonitor 启动网页防篡改后台巡检（按站点 scan_interval 周期校验哈希）。
func StartTamperMonitor() {
	go func() {
		for {
			func() {
				defer func() { _ = recover() }()
				tamperMu.Lock()
				data := loadTamper()
				if !boolOf(data["enabled"]) {
					tamperMu.Unlock()
					return
				}
				sites := tamperSites(data)
				newEvents := []map[string]any{}
				for _, site := range sites {
					if !boolOf(site["enabled"]) {
						continue
					}
					interval := int(int64Of(site["scan_interval_seconds"]))
					if interval <= 0 {
						interval = defaultScanInterval
					}
					last := str(site["last_scan_at"])
					if last != "" {
						if t, err := time.Parse("2006-01-02 15:04:05", last); err == nil &&
							time.Since(t) < time.Duration(interval)*time.Second {
							continue
						}
					}
					events := tamperScan(site)
					site["last_scan_at"] = time.Now().Format("2006-01-02 15:04:05")
					if len(events) > 0 {
						site["last_tamper_at"] = site["last_scan_at"]
						newEvents = append(newEvents, events...)
					}
				}
				if len(newEvents) > 0 {
					history := []map[string]any{}
					if arr, ok := data["history"].([]any); ok {
						for _, item := range arr {
							if m, ok := item.(map[string]any); ok {
								history = append(history, m)
							}
						}
					}
					history = append(history, newEvents...)
					if len(history) > 200 {
						history = history[len(history)-200:]
					}
					data["history"] = history
					data["sites"] = sites
					_ = WriteJSONFile(DataPath("tamper.json"), data)
					tamperMu.Unlock()
					if PushAllFunc != nil {
						PushAllFunc("【Graw 防篡改告警】检测到 " + itoa(len(newEvents)) + " 个文件被修改/删除，请登录面板查看详情。")
					}
					return
				}
				tamperMu.Unlock()
			}()
			time.Sleep(30 * time.Second)
		}
	}()
}

func tamperScan(site map[string]any) []map[string]any {
	events := []map[string]any{}
	hashes := mapOf(site["hashes"])
	root := str(site["root"])
	siteID := str(site["site_id"])
	for _, rel := range toStringSlice(site["protected_files"]) {
		real, ok := siteResolveFile(root, rel)
		if !ok {
			continue
		}
		expect := str(hashes[rel])
		if !Exists(real) {
			events = append(events, map[string]any{
				"type": "deleted", "site_id": siteID, "file": rel,
				"time": time.Now().Format("2006-01-02 15:04:05"),
			})
			continue
		}
		actual := fileSHA256(real)
		if expect != "" && expect != actual {
			events = append(events, map[string]any{
				"type": "modified", "site_id": siteID, "file": rel,
				"time": time.Now().Format("2006-01-02 15:04:05"),
			})
		}
	}
	return events
}

func tamperWS(w http.ResponseWriter, r *http.Request) {
	u := AuthenticateWS(r)
	if u == nil || !HasPerm(u, "tamper") {
		WriteJSON(w, 403, map[string]any{"detail": "无权限"})
		return
	}
	if n := currentNode(); str(n["type"]) == "ssh" && agentReady(n) {
		bridgeAgentWS(w, r, n, "/api/tamper/ws")
		return
	}
	conn, err := upgradeWS(w, r)
	if err != nil {
		return
	}
	defer conn.Close()
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			data := loadTamper()
			_ = conn.WriteText(mustJSON(map[string]any{
				"type": "tamper", "data": map[string]any{
					"enabled": boolOf(data["enabled"]),
					"sites":   len(tamperSites(data)),
				},
			}))
		default:
		}
		if _, _, err := conn.ReadMessage(); err != nil {
			return
		}
	}
}

func mustJSON(v any) []byte {
	b, _ := json.Marshal(v)
	return b
}

// --- 面板更新 ---

var updateState struct {
	sync.Mutex
	running bool
}

func registerUpdateRoutes(a *App) {
	a.Get("/api/update/status", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		current := SelfAppVersion()
		latest := ""
		client := &http.Client{Timeout: 5 * time.Second}
		if resp, err := client.Get("https://hub.docker.com/v2/repositories/shunx/graw/tags?page_size=1"); err == nil {
			defer resp.Body.Close()
			var payload struct {
				Results []struct {
					Name string `json:"name"`
				} `json:"results"`
			}
			if json.NewDecoder(resp.Body).Decode(&payload) == nil && len(payload.Results) > 0 {
				latest = payload.Results[0].Name
			}
		}
		deployMode, deployDetail := "local", "local"
		if isRunningInContainer() {
			deployMode = "docker"
			deployDetail = "compose"
			if strings.TrimSpace(os.Getenv("GRAW_COMPOSE_DIR")) == "" {
				deployDetail = "docker-run"
			}
		}
		return WriteJSON(w, 200, map[string]any{
			"current_version": current, "latest_version": latest,
			"update_available": latest != "" && compareVersion(latest, current) > 0,
			"deploy_mode":      deployMode, "deploy_detail": deployDetail,
			"check_error": func() any {
				if latest == "" {
					return "无法连接 Docker Hub 获取最新版本"
				}
				return nil
			}(),
		})
	})
	a.Get("/api/update/log", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		data, err := os.ReadFile(DataPath("update.log"))
		if err != nil {
			return WriteJSON(w, 200, map[string]any{"log": "(暂无更新记录)"})
		}
		lines := strings.Split(strings.TrimRight(string(data), "\n"), "\n")
		if len(lines) > 150 {
			lines = lines[len(lines)-150:]
		}
		return WriteJSON(w, 200, map[string]any{"log": strings.Join(lines, "\n")})
	})
	a.Post("/api/update/apply", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		if !isRunningInContainer() {
			return NewHTTPError(400, "面板当前以本机方式运行，不支持一键更新。请使用 git pull 拉取最新代码后重启服务。")
		}
		updateState.Lock()
		if updateState.running {
			updateState.Unlock()
			return NewHTTPError(409, "已有更新任务正在进行，请稍后再试")
		}
		updateState.running = true
		updateState.Unlock()
		dir := strings.TrimSpace(os.Getenv("GRAW_COMPOSE_DIR"))
		if dir == "" {
			updateState.Lock()
			updateState.running = false
			updateState.Unlock()
			return NewHTTPError(400, "无法获取面板自身 compose 信息，请设置 GRAW_COMPOSE_DIR 或使用「Docker 管理-容器升级」手动更新。")
		}
		go func() {
			defer func() {
				updateState.Lock()
				updateState.running = false
				updateState.Unlock()
			}()
			logPath := DataPath("update.log")
			f, _ := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
			if f != nil {
				defer func() {
					if cerr := f.Close(); cerr != nil {
						log.Printf("graw: 关闭更新日志失败: %v", cerr)
					}
				}()
				fmt.Fprintf(f, "[%s] 开始一键更新（%s）\n", time.Now().Format("2006-01-02 15:04:05"), dir)
			}
			res := runCommand(600*time.Second, "sh", "-c", "cd "+shellQuote(dir)+" && docker compose pull && docker compose up -d")
			if f != nil {
				fmt.Fprintf(f, "[%s] 更新完成 code=%d\n%s\n", time.Now().Format("2006-01-02 15:04:05"), res.Code, res.Stdout+res.Stderr)
			}
		}()
		return WriteJSON(w, 200, map[string]any{
			"ok": true, "started": true,
			"message": "更新已启动，面板容器将自动重建。重建期间页面会短暂不可用，请稍后刷新。",
		})
	})
}

func versionKey(v string) []int {
	v = strings.TrimPrefix(strings.TrimSpace(v), "v")
	parts := strings.Split(v, ".")
	out := make([]int, 0, len(parts))
	for _, p := range parts {
		n, _ := strconv.Atoi(regexp.MustCompile(`[^0-9].*$`).ReplaceAllString(p, ""))
		out = append(out, n)
	}
	return out
}

func compareVersion(a, b string) int {
	ka, kb := versionKey(a), versionKey(b)
	n := len(ka)
	if len(kb) > n {
		n = len(kb)
	}
	for i := 0; i < n; i++ {
		va, vb := 0, 0
		if i < len(ka) {
			va = ka[i]
		}
		if i < len(kb) {
			vb = kb[i]
		}
		if va > vb {
			return 1
		}
		if va < vb {
			return -1
		}
	}
	return 0
}
