package graw

import (
	"archive/tar"
	"compress/gzip"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

// 备份中心 / 面板配置备份 / 配置快照回滚 / 巡检报告。

var backupFileRe = regexp.MustCompile(`^[A-Za-z0-9_.-]+_\d{8}_\d{6}\.tar\.gz$`)

// backupFilePartsRe 解析备份文件名：<safe>_<yyyyMMdd_HHmmss>.tar.gz
var backupFilePartsRe = regexp.MustCompile(`^([A-Za-z0-9_.-]+)_(\d{8}_\d{6})\.tar\.gz$`)

func defaultBackupDir() string {
	if isWindows() {
		return `C:\GrawBackups`
	}
	return "/data/graw-backups"
}

func loadBackupConfig() map[string]any {
	data := map[string]any{"backup_dir": defaultBackupDir(), "tasks": []any{}, "remotes": []any{}}
	_ = ReadJSON(DataPath("backup.json"), &data)
	if data == nil {
		data = map[string]any{"backup_dir": defaultBackupDir(), "tasks": []any{}, "remotes": []any{}}
	}
	if str(data["backup_dir"]) == "" {
		data["backup_dir"] = defaultBackupDir()
	}
	return data
}

func backupItems(data map[string]any, key string) []map[string]any {
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

func backupDirOf(data map[string]any) string {
	return defaultString(strings.TrimSpace(str(data["backup_dir"])), defaultBackupDir())
}

func registerBackupRoutes(a *App) {
	a.Get("/api/backup/status", perm("backup"), func(w http.ResponseWriter, r *http.Request) error {
		data := loadBackupConfig()
		dir := backupDirOf(data)
		files, _ := os.ReadDir(HostPath(dir))
		total, size := 0, int64(0)
		for _, f := range files {
			if backupFileRe.MatchString(f.Name()) {
				total++
				if info, err := f.Info(); err == nil {
					size += info.Size()
				}
			}
		}
		return WriteJSON(w, 200, map[string]any{
			"backup_dir": dir, "task_count": len(backupItems(data, "tasks")),
			"file_count": total, "total_size": size,
		})
	})
	a.Get("/api/backup/tasks", perm("backup"), func(w http.ResponseWriter, r *http.Request) error {
		data := loadBackupConfig()
		tasks := backupItems(data, "tasks")
		counts := map[string]int{}
		for _, rec := range scanBackupRecords(data, tasks) {
			if tid := str(rec["task_id"]); tid != "" {
				counts[tid]++
			}
		}
		for _, t := range tasks {
			t["record_count"] = counts[str(t["id"])]
		}
		remotes := []map[string]any{}
		for _, rm := range backupItems(data, "remotes") {
			remotes = append(remotes, maskBackupRemote(rm))
		}
		return WriteJSON(w, 200, map[string]any{
			"tasks": tasks, "backup_dir": backupDirOf(data), "remotes": remotes,
		})
	})
	a.Post("/api/backup/tasks", perm("backup"), func(w http.ResponseWriter, r *http.Request) error {
		var req backupTaskRequest
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		name := strings.TrimSpace(req.Name)
		if name == "" || len(name) > 64 {
			return NewHTTPError(400, "任务名称必填且不超过 64 字符")
		}
		source, err := validateBackupSource(req.Source)
		if err != nil {
			return err
		}
		target := ""
		if strings.TrimSpace(req.Target) != "" {
			target, err = validateBackupTarget(req.Target)
			if err != nil {
				return err
			}
		}
		taskType := defaultString(req.Type, "dir")
		if taskType != "dir" && taskType != "file" {
			return NewHTTPError(422, "type 仅支持 dir/file")
		}
		if req.KeepCount < 0 || req.KeepCount > 10000 || req.KeepDays < 0 || req.KeepDays > 36500 {
			return NewHTTPError(422, "保留份数/天数超出范围")
		}
		data := loadBackupConfig()
		remoteID := strings.TrimSpace(req.RemoteID)
		if remoteID != "" && findBackupRemote(data, remoteID) == nil {
			return NewHTTPError(400, "绑定的远程备份目标不存在")
		}
		enabled := true
		if req.Enabled != nil {
			enabled = *req.Enabled
		}
		task := map[string]any{
			"id": "bk_" + randomHex(10), "name": name, "type": taskType,
			"source": source, "target": target, "schedule": strings.TrimSpace(req.Schedule),
			"keep_count": req.KeepCount, "keep_days": req.KeepDays,
			"enabled": enabled, "remote_id": remoteID,
			"safe": sanitizeBackupName(source), "cron_task_id": "",
			"created_at":     time.Now().Format("2006-01-02T15:04:05"),
			"last_backup_at": "", "last_status": "", "last_error": "",
		}
		if err := backupSyncCron(task); err != nil {
			return NewHTTPError(500, "创建计划任务失败："+err.Error())
		}
		data["tasks"] = append(backupItems(data, "tasks"), task)
		if err := WriteJSONFile(DataPath("backup.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, task)
	})
	a.Put("/api/backup/tasks/{task_id}", perm("backup"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "task_id")
		var req map[string]any
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		data := loadBackupConfig()
		tasks := backupItems(data, "tasks")
		var task map[string]any
		for _, t := range tasks {
			if str(t["id"]) == id {
				task = t
			}
		}
		if task == nil {
			return NewHTTPError(404, "备份任务不存在")
		}
		if v, ok := req["name"]; ok {
			task["name"] = strings.TrimSpace(str(v))
		}
		if v, ok := req["source"]; ok {
			source, err := validateBackupSource(str(v))
			if err != nil {
				return err
			}
			task["source"] = source
			task["safe"] = sanitizeBackupName(source)
		}
		if v, ok := req["target"]; ok {
			target := strings.TrimSpace(str(v))
			if target != "" {
				validated, err := validateBackupTarget(target)
				if err != nil {
					return err
				}
				task["target"] = validated
			} else {
				task["target"] = ""
			}
		}
		if v, ok := req["schedule"]; ok {
			task["schedule"] = strings.TrimSpace(str(v))
		}
		if v, ok := req["keep_count"]; ok {
			task["keep_count"] = int(int64Of(v))
		}
		if v, ok := req["keep_days"]; ok {
			task["keep_days"] = int(int64Of(v))
		}
		if v, ok := req["enabled"]; ok {
			task["enabled"] = boolOf(v)
		}
		if v, ok := req["remote_id"]; ok {
			remoteID := strings.TrimSpace(str(v))
			if remoteID != "" && findBackupRemote(data, remoteID) == nil {
				return NewHTTPError(400, "绑定的远程备份目标不存在")
			}
			task["remote_id"] = remoteID
		}
		if err := backupSyncCron(task); err != nil {
			return NewHTTPError(500, "更新计划任务失败："+err.Error())
		}
		data["tasks"] = tasks
		if err := WriteJSONFile(DataPath("backup.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, task)
	})
	a.Delete("/api/backup/tasks/{task_id}", perm("backup"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "task_id")
		data := loadBackupConfig()
		kept := []map[string]any{}
		found := false
		for _, t := range backupItems(data, "tasks") {
			if str(t["id"]) == id {
				found = true
				if cid := str(t["cron_task_id"]); cid != "" {
					backupDeleteCron(cid)
				}
			} else {
				kept = append(kept, t)
			}
		}
		if !found {
			return NewHTTPError(404, "备份任务不存在")
		}
		data["tasks"] = kept
		if err := WriteJSONFile(DataPath("backup.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/backup/tasks/{task_id}/run", perm("backup"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "task_id")
		data := loadBackupConfig()
		var task map[string]any
		for _, t := range backupItems(data, "tasks") {
			if str(t["id"]) == id {
				task = t
			}
		}
		if task == nil {
			return NewHTTPError(404, "备份任务不存在")
		}
		result, err := executeBackupTask(data, task)
		if err != nil {
			latest := loadBackupConfig()
			for _, t := range backupItems(latest, "tasks") {
				if str(t["id"]) == id {
					t["last_backup_at"] = nowISO()
					t["last_status"] = "error"
					t["last_error"] = err.Error()
				}
			}
			_ = WriteJSONFile(DataPath("backup.json"), latest)
			return err
		}
		latest := loadBackupConfig()
		for _, t := range backupItems(latest, "tasks") {
			if str(t["id"]) == id {
				t["last_backup_at"] = nowISO()
				t["last_status"] = "ok"
				t["last_error"] = ""
			}
		}
		_ = WriteJSONFile(DataPath("backup.json"), latest)
		return WriteJSON(w, 200, result)
	})
	a.Post("/api/backup/tasks/{task_id}/restore", perm("backup"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "task_id")
		var req struct {
			File   string `json:"file"`
			Target string `json:"target"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		data := loadBackupConfig()
		var task map[string]any
		for _, t := range backupItems(data, "tasks") {
			if str(t["id"]) == id {
				task = t
			}
		}
		if task == nil {
			return NewHTTPError(404, "备份任务不存在")
		}
		file := strings.TrimSpace(req.File)
		safe := str(task["safe"])
		if !backupFileRe.MatchString(file) || !strings.HasPrefix(file, safe+"_") {
			return NewHTTPError(400, "备份文件名非法")
		}
		target, err := validateBackupTarget(req.Target)
		if err != nil {
			return err
		}
		start := time.Now()
		archive := ""
		for _, cand := range []string{taskTarget(data, task), backupDirOf(data)} {
			p := HostPath(filepath.Join(cand, file))
			if Exists(p) {
				archive = p
				break
			}
		}
		if archive == "" {
			return NewHTTPError(404, "备份文件不存在")
		}
		if err := extractTar(archive, HostPath(target)); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{
			"ok": true, "file": file, "target": target,
			"elapsed_seconds": math.Round(time.Since(start).Seconds()*100) / 100,
		})
	})
	a.Get("/api/backup/records", perm("backup"), func(w http.ResponseWriter, r *http.Request) error {
		data := loadBackupConfig()
		return WriteJSON(w, 200, map[string]any{"records": scanBackupRecords(data, backupItems(data, "tasks"))})
	})
	a.Delete("/api/backup/records", perm("backup"), func(w http.ResponseWriter, r *http.Request) error {
		file := strings.TrimSpace(r.URL.Query().Get("file"))
		if !backupFileRe.MatchString(file) {
			return NewHTTPError(400, "备份文件名非法")
		}
		data := loadBackupConfig()
		removed := false
		seen := map[string]bool{}
		dirs := []string{backupDirOf(data)}
		for _, t := range backupItems(data, "tasks") {
			dirs = append(dirs, taskTarget(data, t))
		}
		for _, cand := range dirs {
			if seen[cand] {
				continue
			}
			seen[cand] = true
			p := HostPath(filepath.Join(cand, file))
			if Exists(p) {
				if err := os.Remove(p); err != nil {
					return NewHTTPError(500, "删除备份文件失败："+err.Error())
				}
				removed = true
			}
		}
		if !removed {
			return NewHTTPError(404, "备份文件不存在")
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Get("/api/backup/remotes", perm("backup"), func(w http.ResponseWriter, r *http.Request) error {
		remotes := []map[string]any{}
		for _, rm := range backupItems(loadBackupConfig(), "remotes") {
			remotes = append(remotes, maskBackupRemote(rm))
		}
		return WriteJSON(w, 200, map[string]any{"remotes": remotes})
	})
	a.Post("/api/backup/remotes", perm("backup"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Name     string `json:"name"`
			Type     string `json:"type"`
			Base     string `json:"base"`
			Username string `json:"username"`
			Password string `json:"password"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		name := strings.TrimSpace(req.Name)
		base := strings.TrimRight(strings.TrimSpace(req.Base), "/")
		if name == "" || len(name) > 64 {
			return NewHTTPError(400, "名称必填且不超过 64 字符")
		}
		if !strings.HasPrefix(base, "http://") && !strings.HasPrefix(base, "https://") {
			return NewHTTPError(400, "WebDAV 地址必须为 http/https URL")
		}
		remote := map[string]any{
			"id": "rmt_" + randomHex(10), "name": name,
			"type": defaultString(req.Type, "webdav"), "base": base,
			"username": strings.TrimSpace(req.Username), "password": req.Password,
			"created_at": nowISO(),
		}
		data := loadBackupConfig()
		data["remotes"] = append(backupItems(data, "remotes"), remote)
		if err := WriteJSONFile(DataPath("backup.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, maskBackupRemote(remote))
	})
	a.Put("/api/backup/remotes/{remote_id}", perm("backup"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "remote_id")
		var req struct {
			Name     string `json:"name"`
			Type     string `json:"type"`
			Base     string `json:"base"`
			Username string `json:"username"`
			Password string `json:"password"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		data := loadBackupConfig()
		remotes := backupItems(data, "remotes")
		var remote map[string]any
		for _, rm := range remotes {
			if str(rm["id"]) == id {
				remote = rm
			}
		}
		if remote == nil {
			return NewHTTPError(404, "远程备份目标不存在")
		}
		base := strings.TrimRight(strings.TrimSpace(req.Base), "/")
		if !strings.HasPrefix(base, "http://") && !strings.HasPrefix(base, "https://") {
			return NewHTTPError(400, "WebDAV 地址必须为 http/https URL")
		}
		if backupURLAuthenticity(base) != backupURLAuthenticity(str(remote["base"])) && req.Password == "" {
			return NewHTTPError(400, "服务器地址已变更，请显式提供新密码（密码留空不再沿用旧密码，避免凭据泄露到新服务器）")
		}
		remote["name"] = strings.TrimSpace(req.Name)
		remote["base"] = base
		remote["username"] = strings.TrimSpace(req.Username)
		if req.Type != "" {
			remote["type"] = req.Type
		}
		if req.Password != "" {
			remote["password"] = req.Password
		}
		if err := WriteJSONFile(DataPath("backup.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, maskBackupRemote(remote))
	})
	a.Delete("/api/backup/remotes/{remote_id}", perm("backup"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "remote_id")
		data := loadBackupConfig()
		kept := []map[string]any{}
		found := false
		for _, rm := range backupItems(data, "remotes") {
			if str(rm["id"]) == id {
				found = true
			} else {
				kept = append(kept, rm)
			}
		}
		if !found {
			return NewHTTPError(404, "远程备份目标不存在")
		}
		data["remotes"] = kept
		for _, t := range backupItems(data, "tasks") {
			if str(t["remote_id"]) == id {
				t["remote_id"] = ""
			}
		}
		if err := WriteJSONFile(DataPath("backup.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/backup/remotes/{remote_id}/test", perm("backup"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "remote_id")
		remote := findBackupRemote(loadBackupConfig(), id)
		if remote == nil {
			return NewHTTPError(404, "远程备份目标不存在")
		}
		base := str(remote["base"])
		if base == "" {
			return NewHTTPError(400, "远端 URL 为空")
		}
		req, _ := http.NewRequest("PROPFIND", base, strings.NewReader(
			`<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/></d:prop></d:propfind>`))
		req.Header.Set("Depth", "0")
		req.Header.Set("Content-Type", "application/xml")
		if user := str(remote["username"]); user != "" {
			req.SetBasicAuth(user, str(remote["password"]))
		}
		client := &http.Client{Timeout: 30 * time.Second}
		resp, err := client.Do(req)
		if err != nil {
			return NewHTTPError(502, "WebDAV 连接失败："+err.Error())
		}
		resp.Body.Close()
		if resp.StatusCode >= 400 {
			return NewHTTPError(502, "WebDAV 连接失败：HTTP "+itoa(resp.StatusCode))
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "id": id, "name": str(remote["name"])})
	})

	registerPanelBackupRoutes(a)
	registerRollbackRoutes(a)
	registerReportRoutes(a)
}

type backupTaskRequest struct {
	Name      string `json:"name"`
	Type      string `json:"type"`
	Source    string `json:"source"`
	Target    string `json:"target"`
	Schedule  string `json:"schedule"`
	KeepCount int    `json:"keep_count"`
	KeepDays  int    `json:"keep_days"`
	Enabled   *bool  `json:"enabled"`
	RemoteID  string `json:"remote_id"`
}

func taskTarget(data, task map[string]any) string {
	if t := strings.TrimSpace(str(task["target"])); t != "" {
		return t
	}
	return backupDirOf(data)
}

func backupPathIsData(p string) bool {
	npLower := strings.ToLower(filepath.ToSlash(filepath.Clean(p)))
	ndLower := strings.ToLower(filepath.ToSlash(filepath.Clean(GetConfig().DataDir)))
	return npLower == ndLower || strings.HasPrefix(npLower, ndLower+"/")
}

func validateBackupSource(p string) (string, error) {
	p = strings.TrimSpace(p)
	if p == "" {
		return "", NewHTTPError(400, "备份源路径不能为空")
	}
	if strings.HasPrefix(p, `\\?\`) || strings.HasPrefix(p, `\\.\`) {
		return "", NewHTTPError(400, "非法路径（不支持设备命名空间路径）")
	}
	if strings.ContainsRune(p, 0) {
		return "", NewHTTPError(400, "备份源路径不能包含非法字符")
	}
	if windowsTarget() && strings.Contains(p, `"`) {
		return "", NewHTTPError(400, `路径包含非法字符 "（Windows 文件名不允许双引号）`)
	}
	if !validDeployDir(p) {
		return "", NewHTTPError(400, "备份源路径必须为绝对路径")
	}
	base := filepath.Base(strings.TrimRight(p, `/\`))
	if strings.HasPrefix(base, "-") {
		return "", NewHTTPError(400, "备份源路径的文件/目录名不能以 - 开头")
	}
	if backupPathIsData(p) {
		return "", NewHTTPError(403, "无权备份面板数据目录")
	}
	return filepath.Clean(p), nil
}

func validateBackupTarget(p string) (string, error) {
	p = strings.TrimSpace(p)
	if p == "" {
		return "", NewHTTPError(400, "目标目录不能为空")
	}
	if strings.HasPrefix(p, `\\?\`) || strings.HasPrefix(p, `\\.\`) {
		return "", NewHTTPError(400, "非法路径（不支持设备命名空间路径）")
	}
	if strings.ContainsRune(p, 0) {
		return "", NewHTTPError(400, "目标目录不能包含非法字符")
	}
	if windowsTarget() && strings.Contains(p, `"`) {
		return "", NewHTTPError(400, `路径包含非法字符 "（Windows 文件名不允许双引号）`)
	}
	if !validDeployDir(p) {
		return "", NewHTTPError(400, "目标目录必须为绝对路径")
	}
	norm := filepath.Clean(p)
	if norm == "/" || norm == `\` || norm == "." {
		return "", NewHTTPError(400, "目标目录不能是根目录")
	}
	if backupPathIsData(norm) {
		return "", NewHTTPError(403, "无权将面板数据目录作为备份目标")
	}
	return norm, nil
}

func backupURLAuthenticity(raw string) string {
	u, err := url.Parse(raw)
	if err != nil || u.Hostname() == "" {
		return ""
	}
	port := u.Port()
	if port == "" {
		if u.Scheme == "https" {
			port = "443"
		} else if u.Scheme == "http" {
			port = "80"
		}
	}
	return strings.ToLower(u.Scheme) + "://" + strings.ToLower(u.Hostname()) + ":" + port
}

func findBackupRemote(data map[string]any, id string) map[string]any {
	for _, rm := range backupItems(data, "remotes") {
		if str(rm["id"]) == id {
			return rm
		}
	}
	return nil
}

func maskBackupRemote(remote map[string]any) map[string]any {
	return map[string]any{
		"id": str(remote["id"]), "name": str(remote["name"]),
		"type": defaultString(str(remote["type"]), "webdav"),
		"base": str(remote["base"]), "username": str(remote["username"]),
		"has_password": str(remote["password"]) != "",
	}
}

func scanBackupRecords(data map[string]any, tasks []map[string]any) []map[string]any {
	safeToTask := map[string]map[string]any{}
	for _, t := range tasks {
		if safe := str(t["safe"]); safe != "" {
			safeToTask[safe] = t
		}
	}
	records := []map[string]any{}
	seen := map[string]bool{}
	scanDir := func(target string) {
		if target == "" || seen[target] {
			return
		}
		seen[target] = true
		real := HostPath(target)
		entries, err := os.ReadDir(real)
		if err != nil {
			return
		}
		for _, e := range entries {
			fn := e.Name()
			if !backupFileRe.MatchString(fn) {
				continue
			}
			parts := backupFilePartsRe.FindStringSubmatch(fn)
			if len(parts) != 3 {
				continue
			}
			info, ierr := e.Info()
			if ierr != nil {
				continue
			}
			safe, ts := parts[1], parts[2]
			task := safeToTask[safe]
			created := ""
			if tv, err := time.ParseInLocation("20060102_150405", ts, time.Local); err == nil {
				created = tv.Format("2006-01-02T15:04:05")
			} else {
				created = info.ModTime().Format("2006-01-02T15:04:05")
			}
			taskID, taskName := "", safe
			if task != nil {
				taskID, taskName = str(task["id"]), str(task["name"])
			}
			records = append(records, map[string]any{
				"id": safe + "_" + ts, "task_id": taskID, "task_name": taskName,
				"file": filepath.Join(target, fn), "name": fn, "size": info.Size(),
				"created_at": created, "mtime": float64(info.ModTime().UnixNano()) / 1e9,
			})
		}
	}
	for _, t := range tasks {
		scanDir(taskTarget(data, t))
	}
	scanDir(backupDirOf(data))
	sort.Slice(records, func(i, j int) bool {
		return records[i]["mtime"].(float64) > records[j]["mtime"].(float64)
	})
	return records
}

func rotateBackupFiles(target, safe string, keepCount, keepDays int) []string {
	removed := []string{}
	if keepCount <= 0 && keepDays <= 0 {
		return removed
	}
	real := HostPath(target)
	entries, err := os.ReadDir(real)
	if err != nil {
		return removed
	}
	type item struct {
		path  string
		name  string
		mtime time.Time
	}
	files := []item{}
	for _, e := range entries {
		if !strings.HasPrefix(e.Name(), safe+"_") || !strings.HasSuffix(e.Name(), ".tar.gz") {
			continue
		}
		info, ierr := e.Info()
		if ierr != nil {
			continue
		}
		files = append(files, item{filepath.Join(real, e.Name()), e.Name(), info.ModTime()})
	}
	sort.Slice(files, func(i, j int) bool { return files[i].mtime.After(files[j].mtime) })
	now := time.Now()
	for idx, f := range files {
		del := false
		if keepCount > 0 && idx >= keepCount {
			del = true
		}
		if keepDays > 0 && idx > 0 && now.Sub(f.mtime) > time.Duration(keepDays)*24*time.Hour {
			del = true
		}
		if del {
			if os.Remove(f.path) == nil {
				removed = append(removed, f.name)
			}

		}
	}
	return removed
}

// backupCronCommand 生成计划备份命令（与 Python backup._build_cron_command 对齐）。
func backupCronCommand(source, target, safe string, keepDays int) (string, error) {
	source = strings.TrimRight(source, `/\`)
	target = strings.TrimRight(target, `/\`)
	if source == "" || target == "" {
		return "", NewHTTPError(400, "备份源/目标不能为空")
	}
	parent, base := filepath.Split(source)
	base = strings.TrimRight(base, `/\`)
	parent = strings.TrimRight(parent, `/\`)
	if base == "" {
		base = safe
	}
	if parent == "" {
		parent = "/"
	}
	if strings.HasPrefix(base, "-") {
		return "", NewHTTPError(400, "备份路径的文件/目录名不能以 - 开头")
	}
	if windowsTarget() {
		for _, val := range []string{source, target, safe} {
			if strings.Contains(val, `"`) {
				return "", NewHTTPError(400, "计划备份命令构造中止：路径含非法字符")
			}
		}
		cleanup := ""
		if keepDays > 0 {
			cleanup = `; Get-ChildItem ` + protectionPowerShellQuote(target) + ` -Filter ` +
				protectionPowerShellQuote(safe+"_*") + ` | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-` +
				itoa(keepDays) + `) } | Remove-Item -Force`
		}
		return "powershell -NoProfile -Command " +
			`"New-Item -ItemType Directory -Force -Path ` + protectionPowerShellQuote(target) + ` | Out-Null; ` +
			`$d=Get-Date -Format yyyyMMdd_HHmmss; ` +
			`tar -czf \"` + target + `\` + safe + `_$d.tar.gz\" -C ` +
			protectionPowerShellQuote(parent) + ` ` + protectionPowerShellQuote(base) + cleanup + `"`, nil
	}
	quotedTarget := shellQuote(target)
	cmd := "mkdir -p " + quotedTarget + " && tar -czf " +
		shellQuote(target+"/"+safe+"_$(date +\\%Y\\%m\\%d_\\%H\\%M\\%S).tar.gz") +
		" -C " + shellQuote(parent) + " -- " + shellQuote(base)
	if keepDays > 0 {
		cmd += " && find " + quotedTarget + " -name " + shellQuote(safe+"_*") +
			" -mtime +" + itoa(keepDays) + " -delete"
	}
	return cmd, nil
}

func backupSyncCron(task map[string]any) error {
	cronID := str(task["cron_task_id"])
	schedule := strings.TrimSpace(str(task["schedule"]))
	enabled := boolOfDefault(task["enabled"], true)
	if schedule == "" || !enabled {
		if cronID != "" {
			backupDeleteCron(cronID)
			task["cron_task_id"] = ""
		}
		return nil
	}
	command, err := backupCronCommand(str(task["source"]), taskTarget(loadBackupConfig(), task),
		str(task["safe"]), int(int64Of(task["keep_days"])))
	if err != nil {
		return err
	}
	name := "Graw备份-" + str(task["safe"])
	if cronID != "" {
		crons := loadCronTasks()
		for _, c := range crons {
			if str(c["id"]) != cronID {
				continue
			}
			oldCommand := str(c["command"])
			c["schedule"], c["command"], c["name"], c["enabled"] = schedule, command, name, true
			if windowsTarget() {
				if err := syncWindowsTask(c, true); err != nil {
					return err
				}
			} else {
				current, preserved := listLinuxCron()
				filtered := []map[string]any{}
				for _, item := range current {
					if str(item["command"]) != oldCommand && str(item["command"]) != command {
						filtered = append(filtered, item)
					}
				}
				filtered = append(filtered, map[string]any{"schedule": schedule, "command": command, "enabled": true})
				if err := rewriteLinuxCron(filtered, preserved); err != nil {
					return err
				}
			}
			return saveCronTasks(crons)
		}
	}
	cronTask, err := protectionCreateBackupTask(name, schedule, command)
	if err != nil {
		return err
	}
	task["cron_task_id"] = str(cronTask["id"])
	return nil
}

func backupDeleteCron(id string) {
	tasks := loadCronTasks()
	var target map[string]any
	kept := []map[string]any{}
	for _, t := range tasks {
		if str(t["id"]) == id {
			target = t
		} else {
			kept = append(kept, t)
		}
	}
	if target == nil {
		return
	}
	_ = saveCronTasks(kept)
	if windowsTarget() {
		runCommand(15*time.Second, "schtasks", "/delete", "/tn", str(target["name"]), "/f")
		return
	}
	command := str(target["command"])
	res := HostCmd(10*time.Second, "crontab", "-l")
	if res.Code != 0 {
		return
	}
	lines := []string{}
	for _, line := range strings.Split(res.Stdout, "\n") {
		if strings.TrimSpace(line) == "" || strings.Contains(line, command) {
			continue
		}
		lines = append(lines, line)
	}
	_ = HostCmdInput(15*time.Second, strings.Join(lines, "\n")+"\n", "crontab", "-")
}

func int64of(v any) int64 { return int64Of(v) }

func executeBackupTask(data, task map[string]any) (map[string]any, error) {
	source := str(task["source"])
	target := taskTarget(data, task)
	safe := defaultString(str(task["safe"]), sanitizeBackupName(source))
	start := time.Now()
	realSource := HostPath(source)
	if !Exists(realSource) {
		return nil, NewHTTPError(404, "备份源路径不存在："+source)
	}
	if err := os.MkdirAll(HostPath(target), 0o700); err != nil {
		return nil, NewHTTPError(500, "无法创建备份目录："+err.Error())
	}
	fname := safe + "_" + time.Now().Format("20060102_150405") + ".tar.gz"
	dest := HostPath(filepath.Join(target, fname))
	if err := createArchive(dest, []string{realSource}, "tar.gz"); err != nil {
		return nil, NewHTTPError(500, "备份失败："+err.Error())
	}
	size := int64(0)
	if info, err := os.Stat(dest); err == nil {
		size = info.Size()
	}
	removed := rotateBackupFiles(target, safe, int(int64Of(task["keep_count"])), int(int64Of(task["keep_days"])))
	remoteResult := map[string]any{"uploaded": false, "remote_id": "", "remote_name": "", "error": ""}
	if rid := str(task["remote_id"]); rid != "" {
		if remote := findBackupRemote(data, rid); remote != nil {
			results := uploadBackupRemotes(target, fname, []string{rid}, data)
			if len(results) > 0 && boolOf(results[0]["ok"]) {
				remoteResult = map[string]any{"uploaded": true, "remote_id": rid, "remote_name": str(remote["name"]), "error": ""}
			} else {
				errText := "远程上传失败"
				if len(results) > 0 {
					errText = str(results[0]["error"])
				}
				remoteResult = map[string]any{"uploaded": false, "remote_id": rid, "remote_name": str(remote["name"]), "error": errText}
			}
		}
	}
	removedNames := []any{}
	for _, name := range removed {
		removedNames = append(removedNames, name)
	}
	return map[string]any{
		"ok": true, "task_id": str(task["id"]), "name": str(task["name"]),
		"file": filepath.Join(target, fname), "size": size,
		"elapsed_seconds": math.Round(time.Since(start).Seconds()*100) / 100,
		"removed":         removedNames, "remote": remoteResult,
	}, nil
}

// uploadBackupRemotes 把备份文件推送到任务绑定的远端（WebDAV / S3）。
func uploadBackupRemotes(backupDir, filename string, remoteIDs []string, data map[string]any) []map[string]any {
	if len(remoteIDs) == 0 {
		return []map[string]any{}
	}
	localPath := HostPath(filepath.Join(backupDir, filename))
	results := []map[string]any{}
	for _, rid := range remoteIDs {
		var remote map[string]any
		for _, rm := range backupItems(data, "remotes") {
			if str(rm["id"]) == rid {
				remote = rm
				break
			}
		}
		if remote == nil {
			results = append(results, map[string]any{"remote_id": rid, "ok": false, "error": "远端不存在"})
			continue
		}
		typ := strings.ToLower(str(remote["type"]))
		if typ == "" {
			typ = "webdav"
		}
		var err error
		switch typ {
		case "webdav":
			base := strings.TrimRight(str(remote["base"]), "/")
			if base == "" {
				base = strings.TrimRight(str(remote["url"]), "/")
			}
			if base == "" {
				err = fmt.Errorf("WebDAV URL 为空")
				break
			}
			body, rerr := os.Open(localPath)
			if rerr != nil {
				err = rerr
				break
			}
			defer body.Close()
			req, rerr := http.NewRequest(http.MethodPut, base+"/"+filename, body)
			if rerr != nil {
				err = rerr
				break
			}
			if user := str(remote["username"]); user != "" {
				req.SetBasicAuth(user, str(remote["password"]))
			}
			client := &http.Client{Timeout: 10 * time.Minute}
			resp, derr := client.Do(req)
			if derr != nil {
				err = derr
				break
			}
			resp.Body.Close()
			if resp.StatusCode >= 300 {
				err = fmt.Errorf("WebDAV 返回 %d", resp.StatusCode)
			}
		case "s3":
			if HostWhich("mc") == "" {
				err = fmt.Errorf("宿主机未安装 mc（MinIO Client）")
				break
			}
			bucket := strings.Trim(str(remote["bucket"]), "/")
			endpoint := str(remote["endpoint"])
			if endpoint == "" {
				endpoint = str(remote["url"])
			}
			if bucket == "" || endpoint == "" {
				err = fmt.Errorf("S3 endpoint/bucket 未配置")
				break
			}
			alias := "graw_backup_" + rid
			scheme := "https"
			if boolOf(remote["insecure"]) {
				scheme = "http"
			}
			aliasURL := scheme + "://" + url.QueryEscape(str(remote["access_key"])) + ":" +
				url.QueryEscape(str(remote["secret_key"])) + "@" + strings.TrimPrefix(strings.TrimPrefix(endpoint, "https://"), "http://")
			out, merr := runCommandEnv(10*time.Minute, []string{"MC_HOST_" + alias + "=" + aliasURL},
				"mc", "cp", localPath, alias+"/"+bucket+"/"+filename)
			if merr != nil || out.Code != 0 {
				if merr != nil {
					err = merr
				} else {
					err = fmt.Errorf("%s", strings.TrimSpace(out.Stderr+out.Stdout))
				}
			}
		default:
			err = fmt.Errorf("不支持的远端类型: %s", typ)
		}
		if err != nil {
			results = append(results, map[string]any{"remote_id": rid, "name": str(remote["name"]), "ok": false, "error": err.Error()})
		} else {
			results = append(results, map[string]any{"remote_id": rid, "name": str(remote["name"]), "ok": true})
		}
	}
	return results
}

func sanitizeBackupName(name string) string {
	name = strings.TrimRight(strings.TrimSpace(name), `/\`)
	if idx := strings.LastIndexAny(name, `/\`); idx >= 0 {
		name = name[idx+1:]
	}
	out := []rune{}
	for _, r := range name {
		if r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || r == '_' || r == '-' || r == '.' {
			out = append(out, r)
		}
	}
	return string(out)
}

func listBackupRecords(backupDir string) []map[string]any {
	records := []map[string]any{}
	entries, _ := os.ReadDir(HostPath(backupDir))
	for _, e := range entries {
		if !backupFileRe.MatchString(e.Name()) {
			continue
		}
		info, _ := e.Info()
		size := int64(0)
		mod := time.Time{}
		if info != nil {
			size = info.Size()
			mod = info.ModTime()
		}
		records = append(records, map[string]any{
			"file": e.Name(), "path": filepath.Join(backupDir, e.Name()),
			"size": size, "created_at": mod.Format("2006-01-02 15:04:05"),
		})
	}
	sort.Slice(records, func(i, j int) bool {
		return str(records[i]["created_at"]) > str(records[j]["created_at"])
	})
	return records
}

// --- 面板配置备份 ---

func panelBackupDir() string { return DataPath("panelbackups") }

func collectDataFiles() []string {
	root := GetConfig().DataDir
	files := []string{}
	_ = filepath.Walk(root, func(p string, info os.FileInfo, err error) error {
		if err != nil || info == nil || info.IsDir() {
			return nil
		}
		if strings.HasPrefix(p, panelBackupDir()) {
			return nil
		}
		files = append(files, p)
		return nil
	})
	return files
}

func exportPanelArchive() (map[string]any, error) {
	_ = os.MkdirAll(panelBackupDir(), 0o700)
	name := "panel_" + time.Now().Format("20060102_150405") + ".tar.gz"
	path := filepath.Join(panelBackupDir(), name)
	out, err := os.Create(path)
	if err != nil {
		return nil, err
	}
	defer out.Close()
	gz := gzip.NewWriter(out)
	tw := tar.NewWriter(gz)
	root := GetConfig().DataDir
	files := collectDataFiles()
	for _, f := range files {
		info, err := os.Stat(f)
		if err != nil {
			continue
		}
		rel, err := filepath.Rel(root, f)
		if err != nil {
			continue
		}
		hdr, err := tar.FileInfoHeader(info, "")
		if err != nil {
			continue
		}
		hdr.Name = filepath.ToSlash(rel)
		if err := tw.WriteHeader(hdr); err != nil {
			continue
		}
		in, err := os.Open(f)
		if err != nil {
			continue
		}
		_, _ = io.Copy(tw, in)
		in.Close()
	}
	if err := tw.Close(); err != nil {
		return nil, err
	}
	if err := gz.Close(); err != nil {
		return nil, err
	}
	st, _ := os.Stat(path)
	var size int64
	if st != nil {
		size = st.Size()
	}
	return map[string]any{
		"name": name, "size": size, "file_count": len(files),
		"created_at": time.Now().Format("2006-01-02T15:04:05"),
	}, nil
}

func registerPanelBackupRoutes(a *App) {
	a.Get("/api/panelbackup/list", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		archives := []map[string]any{}
		entries, _ := os.ReadDir(panelBackupDir())
		for _, e := range entries {
			if e.IsDir() || !strings.HasSuffix(e.Name(), ".tar.gz") {
				continue
			}
			info, _ := e.Info()
			size := int64(0)
			mod := time.Time{}
			if info != nil {
				size = info.Size()
				mod = info.ModTime()
			}
			archives = append(archives, map[string]any{
				"name": e.Name(), "size": size, "created_at": mod.Format("2006-01-02T15:04:05"),
			})
		}
		return WriteJSON(w, 200, map[string]any{"archives": archives})
	})
	a.Post("/api/panelbackup/export", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		info, err := exportPanelArchive()
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, info)
	})
	a.Get("/api/panelbackup/download/{name}", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		name := PathParam(r, "name")
		if !strings.HasSuffix(name, ".tar.gz") || strings.Contains(name, "..") || strings.Contains(name, "/") {
			return NewHTTPError(400, "非法文件名")
		}
		path := filepath.Join(panelBackupDir(), name)
		if !Exists(path) {
			return NewHTTPError(404, "归档不存在")
		}
		http.ServeFile(w, r, path)
		return nil
	})
	a.Delete("/api/panelbackup/{name}", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		name := PathParam(r, "name")
		if !strings.HasSuffix(name, ".tar.gz") || strings.Contains(name, "..") || strings.Contains(name, "/") {
			return NewHTTPError(400, "非法文件名")
		}
		_ = os.Remove(filepath.Join(panelBackupDir(), name))
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/panelbackup/import", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		if err := r.ParseMultipartForm(64 << 20); err != nil {
			return NewHTTPError(400, "导入表单解析失败")
		}
		file, _, err := r.FormFile("file")
		if err != nil {
			return NewHTTPError(400, "缺少导入文件")
		}
		defer file.Close()
		tmp, err := os.CreateTemp(panelBackupDir(), ".import-*.tar.gz")
		if err != nil {
			return err
		}
		tmpName := tmp.Name()
		defer os.Remove(tmpName)
		if _, err := io.Copy(tmp, io.LimitReader(file, 64<<20)); err != nil {
			tmp.Close()
			return NewHTTPError(400, "读取导入文件失败")
		}
		tmp.Close()
		tempDir, err := os.MkdirTemp(panelBackupDir(), ".import-")
		if err != nil {
			return err
		}
		defer os.RemoveAll(tempDir)
		if err := extractTar(tmpName, tempDir); err != nil {
			return err
		}
		pre, _ := exportPanelArchive()
		restored := 0
		_ = filepath.Walk(tempDir, func(p string, info os.FileInfo, err error) error {
			if err != nil || info == nil || info.IsDir() {
				return nil
			}
			rel, err := filepath.Rel(tempDir, p)
			if err != nil {
				return nil
			}
			dst := filepath.Join(GetConfig().DataDir, rel)
			if strings.HasPrefix(dst, panelBackupDir()) {
				return nil
			}
			_ = os.MkdirAll(filepath.Dir(dst), 0o700)
			if CopyFile(p, dst) == nil {
				restored++
			}
			return nil
		})
		preName := ""
		if pre != nil {
			preName = str(pre["name"])
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "restored_files": restored, "pre_backup": preName})
	})
}

// --- 配置快照回滚 ---
// 存储与 Python config_snapshot 一致：
// data/config_snapshots/<kind>/<target_id>/<id>.json（含 content_b64）。

const (
	snapshotKeep     = 20
	snapshotMaxBytes = 256 * 1024
)

var (
	snapshotMu sync.Mutex
	snapIDRe   = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,64}$`)
)

func snapshotRoot() string { return DataPath("config_snapshots") }

func snapshotTargetDir(kind, targetID string) (string, bool) {
	if !snapIDRe.MatchString(kind) || !snapIDRe.MatchString(targetID) {
		return "", false
	}
	return DataPath("config_snapshots", kind, targetID), true
}

// CaptureSnapshot 在写配置前保存一份文件快照（供回滚），失败不阻塞业务写入。
func CaptureSnapshot(kind, targetID, filePath string) {
	if !snapIDRe.MatchString(kind) || !snapIDRe.MatchString(targetID) || strings.TrimSpace(filePath) == "" {
		return
	}
	content := ""
	if b, err := os.ReadFile(HostPath(filePath)); err == nil {
		if len(b) > snapshotMaxBytes {
			return
		}
		content = string(b)
	} else if !os.IsNotExist(err) {
		return
	}
	snap := map[string]any{
		"id":        time.Now().Format("20060102T150405") + "_" + randomHex(4),
		"kind":      kind,
		"target_id": targetID,
		"file_path": filePath,
		"when":      time.Now().Format("2006-01-02 15:04:05"),
		"user":      "", "ip": "", "route": "",
		"bytes":       len(content),
		"content_b64": base64.StdEncoding.EncodeToString([]byte(content)),
	}
	snapshotMu.Lock()
	defer snapshotMu.Unlock()
	dir, ok := snapshotTargetDir(kind, targetID)
	if !ok {
		return
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return
	}
	if err := WriteJSONFile(filepath.Join(dir, str(snap["id"])+".json"), snap); err != nil {
		return
	}
	entries, _ := os.ReadDir(dir)
	files := []string{}
	for _, e := range entries {
		if !e.IsDir() && strings.HasSuffix(e.Name(), ".json") {
			files = append(files, e.Name())
		}
	}
	if len(files) > snapshotKeep {
		sort.Strings(files)
		for _, name := range files[:len(files)-snapshotKeep] {
			_ = os.Remove(filepath.Join(dir, name))
		}
	}
}

func listSnapshots(kind string, limit int) []map[string]any {
	if limit < 1 {
		limit = 100
	}
	if limit > 500 {
		limit = 500
	}
	items := []map[string]any{}
	root := snapshotRoot()
	_ = filepath.Walk(root, func(p string, info os.FileInfo, err error) error {
		if err != nil || info == nil || info.IsDir() || !strings.HasSuffix(p, ".json") {
			return nil
		}
		var snap map[string]any
		if ReadJSON(p, &snap) != nil {
			return nil
		}
		if kind != "" && str(snap["kind"]) != kind {
			return nil
		}
		items = append(items, map[string]any{
			"id": str(snap["id"]), "kind": str(snap["kind"]),
			"target_id": str(snap["target_id"]), "file_path": str(snap["file_path"]),
			"when": str(snap["when"]), "user": str(snap["user"]),
			"route": str(snap["route"]), "bytes": int64Of(snap["bytes"]),
		})
		return nil
	})
	sort.SliceStable(items, func(i, j int) bool {
		return str(items[i]["when"]) > str(items[j]["when"])
	})
	if len(items) > limit {
		items = items[:limit]
	}
	return items
}

func findSnapshot(id string) (map[string]any, string) {
	if !snapIDRe.MatchString(id) {
		return nil, ""
	}
	var found map[string]any
	foundPath := ""
	_ = filepath.Walk(snapshotRoot(), func(p string, info os.FileInfo, err error) error {
		if err != nil || info == nil || info.IsDir() || filepath.Base(p) != id+".json" {
			return nil
		}
		var snap map[string]any
		if ReadJSON(p, &snap) == nil && str(snap["id"]) == id {
			found, foundPath = snap, p
		}
		return nil
	})
	return found, foundPath
}

func applyAllFirewallRules(data map[string]any) {
	for _, rule := range firewallRules(data, "port_rules") {
		addPortRule(rule)
	}
	for _, rule := range firewallRules(data, "ip_rules") {
		addIPRule(rule)
	}
}

func registerRollbackRoutes(a *App) {
	a.Get("/api/rollback", perm("backup"), func(w http.ResponseWriter, r *http.Request) error {
		kind := strings.TrimSpace(r.URL.Query().Get("kind"))
		limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
		items := listSnapshots(kind, limit)
		return WriteJSON(w, 200, map[string]any{"items": items, "total": len(items)})
	})
	a.Get("/api/rollback/{s_id}", perm("backup"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "s_id")
		snap, _ := findSnapshot(id)
		if snap == nil {
			return NewHTTPError(404, "快照不存在")
		}
		return WriteJSON(w, 200, map[string]any{
			"id": str(snap["id"]), "kind": str(snap["kind"]),
			"target_id": str(snap["target_id"]), "file_path": str(snap["file_path"]),
			"when": str(snap["when"]), "user": str(snap["user"]), "route": str(snap["route"]),
			"bytes": int64Of(snap["bytes"]), "content_b64": str(snap["content_b64"]),
		})
	})
	a.Post("/api/rollback/{s_id}/restore", perm("backup"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "s_id")
		snap, _ := findSnapshot(id)
		if snap == nil {
			return NewHTTPError(404, "快照不存在或内容非法")
		}
		raw, err := base64.StdEncoding.DecodeString(str(snap["content_b64"]))
		if err != nil {
			return NewHTTPError(404, "快照不存在或内容非法")
		}
		filePath := str(snap["file_path"])
		if filePath == "" {
			return NewHTTPError(404, "快照不存在或内容非法")
		}
		real := HostPath(filePath)
		if werr := os.MkdirAll(filepath.Dir(real), 0o755); werr != nil {
			return NewHTTPError(500, "写回配置失败: "+werr.Error())
		}
		if werr := os.WriteFile(real, raw, 0o644); werr != nil {
			return NewHTTPError(500, "写回配置失败: "+werr.Error())
		}
		kind := str(snap["kind"])
		switch kind {
		case "site", "sites", "waf":
			reloadIfNginx()
		case "firewall":
			var restored map[string]any
			if json.Unmarshal(raw, &restored) == nil {
				applyAllFirewallRules(restored)
			}
		}
		AuditRecord("回滚配置", CurrentUser(r).Username(), ClientIP(r),
			"kind="+kind+" target="+str(snap["target_id"])+" file="+filePath)
		return WriteJSON(w, 200, map[string]any{
			"ok": true, "kind": kind, "target_id": str(snap["target_id"]), "file_path": filePath,
		})
	})
	a.Delete("/api/rollback/{s_id}", perm("backup"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "s_id")
		snap, path := findSnapshot(id)
		if snap == nil || path == "" {
			return NewHTTPError(404, "快照不存在")
		}
		_ = os.Remove(path)
		AuditRecord("删除配置快照", CurrentUser(r).Username(), ClientIP(r), "id="+id)
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
}

// --- 巡检报告 ---

func reportDir() string { return DataPath("reports") }

func generateDailyReport() map[string]any {
	_ = os.MkdirAll(reportDir(), 0o700)
	now := time.Now()
	overview := metricsHub.overview()
	mem := mapOf(overview["memory"])
	storage := mapOf(overview["storage"])
	load := mapOf(overview["load"])
	sites := mergedSites()
	users, _ := LoadUsers()
	lines := []string{
		"# Graw 每日巡检报告",
		"",
		"生成时间：" + now.Format("2006-01-02 15:04:05"),
		"",
		"## 系统概览",
		"",
		fmt.Sprintf("- CPU 使用率：%.1f%%", num(overview["cpu"])),
		fmt.Sprintf("- 内存使用率：%.1f%%（已用 %.1f GB / 总共 %.1f GB）",
			num(mem["percent"]), num(mem["used"])/1024/1024/1024, num(mem["total"])/1024/1024/1024),
		fmt.Sprintf("- 磁盘使用率：%.1f%%（可用 %.1f GB）",
			num(storage["percent"]), num(storage["free"])/1024/1024/1024),
		fmt.Sprintf("- 负载：%.2f / %.2f / %.2f", num(load["load1"]), num(load["load5"]), num(load["load15"])),
		"",
		"## 站点",
		"",
		fmt.Sprintf("- 站点总数：%d", len(sites)),
	}
	enabled := 0
	for _, s := range sites {
		if boolOf(s["enabled"]) {
			enabled++
		}
	}
	lines = append(lines, fmt.Sprintf("- 已启用站点：%d", enabled))
	lines = append(lines, "", "## 账号", "")
	lines = append(lines, fmt.Sprintf("- 账号总数：%d", len(users)))
	admins := 0
	for _, u := range users {
		if u.Role() == "admin" {
			admins++
		}
	}
	lines = append(lines, fmt.Sprintf("- 管理员账号：%d", admins))
	text := strings.Join(lines, "\n") + "\n"
	fname := "report_" + now.Format("20060102_150405") + ".md"
	_ = os.WriteFile(filepath.Join(reportDir(), fname), []byte(text), 0o600)
	pushed := 0
	if PushAllFunc != nil {
		pushed, _ = PushAllFunc(text)
	}
	return map[string]any{"file": fname, "pushed": pushed, "text": text}
}

func registerReportRoutes(a *App) {
	a.Post("/api/report/generate", perm("report"), func(w http.ResponseWriter, r *http.Request) error {
		result := generateDailyReport()
		return WriteJSON(w, 200, map[string]any{
			"ok": true, "file": result["file"], "pushed": result["pushed"], "text": result["text"],
		})
	})
	a.Get("/api/report/list", perm("report"), func(w http.ResponseWriter, r *http.Request) error {
		files := []map[string]any{}
		entries, _ := os.ReadDir(reportDir())
		for _, e := range entries {
			if e.IsDir() || !strings.HasSuffix(e.Name(), ".md") {
				continue
			}
			info, _ := e.Info()
			size := int64(0)
			mod := time.Time{}
			if info != nil {
				size = info.Size()
				mod = info.ModTime()
			}
			files = append(files, map[string]any{"name": e.Name(), "size": size, "created_at": mod.Format("2006-01-02 15:04:05")})
		}
		sort.Slice(files, func(i, j int) bool { return str(files[i]["created_at"]) > str(files[j]["created_at"]) })
		return WriteJSON(w, 200, map[string]any{"files": files})
	})
	a.Get("/api/report/{fname}", perm("report"), func(w http.ResponseWriter, r *http.Request) error {
		name := PathParam(r, "fname")
		if strings.Contains(name, "..") || strings.Contains(name, "/") || !strings.HasSuffix(name, ".md") {
			return NewHTTPError(400, "非法文件名")
		}
		data, err := os.ReadFile(filepath.Join(reportDir(), name))
		if err != nil {
			return NewHTTPError(404, "报告不存在")
		}
		return WriteJSON(w, 200, map[string]any{"file": name, "text": string(data)})
	})
}

// StartDailyReport 启动每天 08:00 生成巡检报告的协程。
func StartDailyReport() {
	go func() {
		for {
			now := time.Now()
			next := time.Date(now.Year(), now.Month(), now.Day(), 8, 0, 0, 0, now.Location())
			if !next.After(now) {
				next = next.AddDate(0, 0, 1)
			}
			time.Sleep(time.Until(next))
			func() {
				defer func() { _ = recover() }()
				generateDailyReport()
			}()
		}
	}()
}
