package graw

import (
	"net/http"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

// 计划任务：Linux crontab / Windows schtasks 双平台；任务元数据存 data/cron.json。
var (
	cronScheduleRe = regexp.MustCompile(`^[0-9A-Za-z*,-/]+(\s+[0-9A-Za-z*,-/]+){4}$`)
	cronLineRe     = regexp.MustCompile(`^((?:\S+\s+){5})(.*)$`)
	cronTaskTypes  = map[string]bool{
		"shell_command": true, "backup_container": true, "visit_url": true,
		"clean_logs": true, "sync_time": true,
	}
)

type cronTask = map[string]any

func loadCronTasks() []map[string]any {
	var tasks []map[string]any
	if err := ReadJSON(DataPath("cron.json"), &tasks); err != nil {
		return []map[string]any{}
	}
	return tasks
}

func saveCronTasks(tasks []map[string]any) error {
	CaptureSnapshot("cron", "cron", DataPath("cron.json"))
	return WriteJSONFile(DataPath("cron.json"), tasks)
}

func validateCronSchedule(schedule string) *HTTPError {
	if !cronScheduleRe.MatchString(strings.TrimSpace(schedule)) {
		return NewHTTPError(400, "调度表达式非法：需为 5 字段 cron 格式（分 时 日 月 周）")
	}
	return nil
}

func validateCronCommand(command string) *HTTPError {
	if windowsTarget() {
		return nil
	}
	if strings.ContainsAny(command, "\r\n") {
		return NewHTTPError(400, "Linux 计划任务命令必须为单行（多命令请用 && 或 ; 连接），不支持换行")
	}
	return nil
}

func shellQuote(s string) string {
	if s == "" {
		return "''"
	}
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}

func resolveCronCommand(taskType, content, fallback string) string {
	content = strings.TrimSpace(content)
	if content != "" || taskType != "shell_command" {
		switch taskType {
		case "backup_container":
			return "docker export " + shellQuote(content) + " | gzip > 'backup_" + time.Now().Format("20060102150405") + ".tar.gz'"
		case "visit_url":
			return "curl -sS -o /dev/null " + shellQuote(content)
		case "clean_logs":
			return "find " + shellQuote(content) + " -type f -name '*.log' -mtime +7 -delete"
		case "sync_time":
			if windowsTarget() {
				return "w32tm /resync"
			}
			return "ntpdate pool.ntp.org"
		default:
			if content != "" {
				return content
			}
			return fallback
		}
	}
	return fallback
}

func listLinuxCron() ([]map[string]any, []string) {
	res := HostCmd(10*time.Second, "crontab", "-l")
	if res.Code != 0 {
		return nil, nil
	}
	tasks := []map[string]any{}
	preserved := []string{}
	for _, line := range strings.Split(res.Stdout, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		if m := cronLineRe.FindStringSubmatch(line); len(m) == 3 {
			tasks = append(tasks, map[string]any{
				"schedule": strings.TrimSpace(m[1]), "command": strings.TrimSpace(m[2]), "enabled": true,
			})
		} else {
			preserved = append(preserved, line)
		}
	}
	return tasks, preserved
}

func rewriteLinuxCron(tasks []map[string]any, preserved []string) error {
	lines := append([]string{}, preserved...)
	for _, t := range tasks {
		if !boolOf(t["enabled"]) && t["enabled"] != nil {
			if v, ok := t["enabled"].(bool); ok && !v {
				continue
			}
		}
		lines = append(lines, str(t["schedule"])+" "+str(t["command"]))
	}
	text := "# Managed by Graw Panel\n" + strings.Join(lines, "\n") + "\n"
	res := HostCmdInput(15*time.Second, text, "crontab", "-")
	if res.Code != 0 {
		return NewHTTPError(500, "crontab update failed")
	}
	return nil
}

func schtasksTime(schedule string) string {
	fields := strings.Fields(schedule)
	if len(fields) < 2 {
		return "09:00"
	}
	minute, hour := fields[0], fields[1]
	allDigits := func(s string) bool {
		if s == "" {
			return false
		}
		for _, c := range s {
			if c < '0' || c > '9' {
				return false
			}
		}
		return true
	}
	if !allDigits(minute) || !allDigits(hour) {
		return "09:00"
	}
	mm, hh := minute, hour
	if len(mm) == 1 {
		mm = "0" + mm
	}
	if len(hh) == 1 {
		hh = "0" + hh
	}
	return hh + ":" + mm
}

func syncWindowsTask(task map[string]any, existed bool) error {
	name := str(task["name"])
	if !boolOf(task["enabled"]) {
		if existed {
			runCommand(15*time.Second, "schtasks", "/delete", "/tn", name, "/f")
		}
		return nil
	}
	bat := DataPath(str(task["id"]) + "_task.bat")
	_ = os.MkdirAll(filepath.Dir(bat), 0o700)
	if err := os.WriteFile(bat, []byte(str(task["command"])), 0o600); err != nil {
		return err
	}
	res := runCommand(15*time.Second, "schtasks", "/create", "/tn", name, "/tr", bat,
		"/sc", "daily", "/st", schtasksTime(str(task["schedule"])), "/f")
	if res.Code != 0 {
		return NewHTTPError(500, strings.TrimSpace(res.Stderr+res.Stdout))
	}
	return nil
}

func registerCronRoutes(a *App) {
	a.Get("/api/cron/list", perm("cron"), func(w http.ResponseWriter, r *http.Request) error {
		platform := "linux"
		if windowsTarget() {
			platform = "windows"
		}
		return WriteJSON(w, 200, map[string]any{"tasks": loadCronTasks(), "platform": platform})
	})
	a.Post("/api/cron/create", perm("cron"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Name     string `json:"name"`
			Schedule string `json:"schedule"`
			Command  string `json:"command"`
			Enabled  *bool  `json:"enabled"`
			TaskType string `json:"task_type"`
			Content  string `json:"content"`
			Group    string `json:"group"`
			Alert    bool   `json:"alert"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if strings.TrimSpace(req.Name) == "" || len(req.Name) > 64 {
			return NewHTTPError(422, "任务名称必填且不超过 64 字符")
		}
		if err := validateCronSchedule(req.Schedule); err != nil {
			return err
		}
		taskType := req.TaskType
		if !cronTaskTypes[taskType] {
			taskType = "shell_command"
		}
		command := resolveCronCommand(taskType, req.Content, req.Command)
		if strings.TrimSpace(command) == "" {
			return NewHTTPError(400, "命令内容不能为空")
		}
		if err := validateCronCommand(command); err != nil {
			return err
		}
		enabled := true
		if req.Enabled != nil {
			enabled = *req.Enabled
		}
		group := strings.TrimSpace(req.Group)
		if group == "" {
			group = "默认"
		}
		task := map[string]any{
			"id": "task_" + randomHex(8), "name": req.Name, "schedule": req.Schedule,
			"command": command, "enabled": enabled, "task_type": taskType,
			"content": strings.TrimSpace(req.Content), "group": group, "alert": req.Alert,
			"created_at": time.Now().Format("2006-01-02T15:04:05"),
		}
		if windowsTarget() {
			if err := syncWindowsTask(task, false); err != nil {
				return err
			}
		} else {
			crons, preserved := listLinuxCron()
			crons = append(crons, map[string]any{
				"schedule": req.Schedule, "command": command, "enabled": enabled,
			})
			if err := rewriteLinuxCron(crons, preserved); err != nil {
				return err
			}
		}
		tasks := append(loadCronTasks(), task)
		if err := saveCronTasks(tasks); err != nil {
			return err
		}
		return WriteJSON(w, 200, task)
	})
	a.Post("/api/cron/{task_id}/update", perm("cron"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "task_id")
		var raw map[string]any
		if err := DecodeJSON(r, &raw); err != nil {
			return err
		}
		tasks := loadCronTasks()
		var task map[string]any
		for _, t := range tasks {
			if str(t["id"]) == id {
				task = t
				break
			}
		}
		if task == nil {
			return NewHTTPError(404, "Task not found")
		}
		oldCommand := str(task["command"])
		if v, ok := raw["schedule"]; ok && v != nil {
			s := str(v)
			if err := validateCronSchedule(s); err != nil {
				return err
			}
			task["schedule"] = s
		}
		if v, ok := raw["command"]; ok && v != nil {
			s := str(v)
			if err := validateCronCommand(s); err != nil {
				return err
			}
			task["command"] = s
		}
		if v, ok := raw["enabled"]; ok && v != nil {
			task["enabled"] = boolOf(v)
		}
		if v, ok := raw["task_type"]; ok && v != nil {
			tt := str(v)
			if !cronTaskTypes[tt] {
				tt = "shell_command"
			}
			task["task_type"] = tt
		}
		if v, ok := raw["content"]; ok && v != nil {
			task["content"] = str(v)
		}
		if v, ok := raw["group"]; ok && v != nil {
			g := strings.TrimSpace(str(v))
			if g == "" {
				g = "默认"
			}
			task["group"] = g
		}
		if v, ok := raw["alert"]; ok && v != nil {
			task["alert"] = boolOf(v)
		}
		if _, hasCommand := raw["command"]; !hasCommand {
			if _, hasType := raw["task_type"]; hasType {
				task["command"] = resolveCronCommand(str(task["task_type"]), str(task["content"]), oldCommand)
			} else if _, hasContent := raw["content"]; hasContent {
				task["command"] = resolveCronCommand(str(task["task_type"]), str(task["content"]), oldCommand)
			}
		}
		if err := saveCronTasks(tasks); err != nil {
			return err
		}
		if windowsTarget() {
			if err := syncWindowsTask(task, true); err != nil {
				return NewHTTPError(500, "面板配置已保存，但系统计划任务同步失败（"+err.Error()+"）。请检查 schtasks 权限后重试。")
			}
		} else {
			crons, preserved := listLinuxCron()
			kept := []map[string]any{}
			for _, c := range crons {
				if str(c["command"]) != oldCommand {
					kept = append(kept, c)
				}
			}
			if boolOf(task["enabled"]) {
				kept = append(kept, map[string]any{
					"schedule": str(task["schedule"]), "command": str(task["command"]), "enabled": true,
				})
			}
			if err := rewriteLinuxCron(kept, preserved); err != nil {
				return NewHTTPError(500, "面板配置已保存，但系统计划任务同步失败（"+err.Error()+"）。请检查 crontab 权限后重试。")
			}
		}
		return WriteJSON(w, 200, task)
	})
	a.Post("/api/cron/{task_id}/delete", perm("cron"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "task_id")
		tasks := loadCronTasks()
		var task map[string]any
		kept := []map[string]any{}
		for _, t := range tasks {
			if str(t["id"]) == id {
				task = t
			} else {
				kept = append(kept, t)
			}
		}
		if task == nil {
			return NewHTTPError(404, "Task not found")
		}
		if windowsTarget() {
			runCommand(15*time.Second, "schtasks", "/delete", "/tn", str(task["name"]), "/f")
		} else {
			crons, preserved := listLinuxCron()
			filtered := []map[string]any{}
			for _, c := range crons {
				if str(c["command"]) != str(task["command"]) {
					filtered = append(filtered, c)
				}
			}
			if err := rewriteLinuxCron(filtered, preserved); err != nil {
				return err
			}
		}
		if err := saveCronTasks(kept); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/cron/{task_id}/run", perm("cron"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "task_id")
		var task map[string]any
		for _, t := range loadCronTasks() {
			if str(t["id"]) == id {
				task = t
				break
			}
		}
		if task == nil {
			return NewHTTPError(404, "Task not found")
		}
		if windowsTarget() {
			runCommand(30*time.Second, "schtasks", "/run", "/tn", str(task["name"]))
		} else {
			HostShell(300*time.Second, str(task["command"]))
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
}

// --- 系统日志 ---

// currentLogRegistry 按当前管理主机生成日志路径表（远程 Linux 不沿用控制器的 Windows 路径）。
func currentLogRegistry() map[string]string {
	reg := map[string]string{"panel": DataPath("panel.log")}
	if windowsTarget() {
		reg["system"] = `C:\Windows\System32\winevt\Logs\System.evtx`
	} else {
		reg["syslog"] = "/var/log/syslog"
		reg["auth"] = "/var/log/auth.log"
		reg["nginx_access"] = "/var/log/nginx/access.log"
		reg["nginx_error"] = "/var/log/nginx/error.log"
		reg["dmesg"] = "/var/log/dmesg"
	}
	return reg
}

func safeLogPath(input string) (string, error) {
	if strings.HasPrefix(input, `\\?\`) || strings.HasPrefix(input, `\\.\`) ||
		strings.HasPrefix(input, `\\`) || strings.HasPrefix(input, "//") && remoteActive() {
		return "", NewHTTPError(400, "非法日志路径")
	}
	if remoteActive() {
		p := strings.ReplaceAll(input, "\\", "/")
		if !strings.HasPrefix(p, "/") {
			p = "/" + p
		}
		return path.Clean(p), nil
	}
	abs, err := filepath.Abs(input)
	if err != nil {
		return "", NewHTTPError(400, "非法日志路径")
	}
	abs = filepath.Clean(abs)
	panelLog := filepath.Clean(DataPath("panel.log"))
	if strings.EqualFold(abs, panelLog) {
		return abs, nil
	}
	if forbiddenDataPath(abs) {
		return "", NewHTTPError(403, "无权访问面板数据目录")
	}
	return abs, nil
}

func loadCustomLogs() []map[string]any {
	var items []map[string]any
	_ = ReadJSON(DataPath("logs.json"), &items)
	return items
}

func allLogRegistry() map[string]string {
	all := map[string]string{}
	for k, v := range currentLogRegistry() {
		all[k] = v
	}
	for _, item := range loadCustomLogs() {
		all[str(item["id"])] = str(item["path"])
	}
	return all
}

func registerLogsRoutes(a *App) {
	a.Get("/api/logs/list", perm("logs"), func(w http.ResponseWriter, r *http.Request) error {
		items := []map[string]any{}
		descs := map[string]string{
			"panel": "面板日志", "syslog": "系统日志", "auth": "认证日志",
			"nginx_access": "Nginx 访问日志", "nginx_error": "Nginx 错误日志",
			"dmesg": "内核日志", "system": "系统事件日志",
		}
		for id, path := range currentLogRegistry() {
			items = append(items, map[string]any{
				"id": id, "name": descs[id], "path": path,
				"exists": HostExists(path), "builtin": true,
			})
		}
		for _, c := range loadCustomLogs() {
			items = append(items, map[string]any{
				"id": str(c["id"]), "name": str(c["name"]), "path": str(c["path"]),
				"exists": HostExists(str(c["path"])), "builtin": false,
			})
		}
		return WriteJSON(w, 200, map[string]any{"logs": items})
	})
	a.Get("/api/logs/read", perm("logs"), func(w http.ResponseWriter, r *http.Request) error {
		path := r.URL.Query().Get("path")
		tail := QueryInt(r, "tail", 200)
		if tail < 1 {
			tail = 1
		}
		if tail > 5000 {
			tail = 5000
		}
		safe, err := safeLogPath(path)
		if err != nil {
			return err
		}
		if !HostExists(safe) {
			return NewHTTPError(404, "Log file not found")
		}
		if remoteActive() {
			if isWindows() && strings.HasSuffix(strings.ToLower(safe), ".evtx") {
				return WriteJSON(w, 200, map[string]any{
					"content": "Windows .evtx files are binary; use Event Viewer directly.",
				})
			}
			res := HostCmd(30*time.Second, "tail", "-n", itoa(tail), "--", safe)
			lines := strings.Split(strings.TrimRight(res.Stdout, "\n"), "\n")
			if len(lines) == 1 && lines[0] == "" {
				lines = []string{}
			}
			return WriteJSON(w, 200, map[string]any{"path": path, "lines": lines, "count": len(lines)})
		}
		real := HostPath(safe)
		st, statErr := os.Stat(real)
		if statErr != nil {
			return NewHTTPError(404, "Log file not found")
		}
		if st.Size() > 50*1024*1024 {
			return NewHTTPError(413, "Log file too large (>50MB)")
		}
		if isWindows() && strings.HasSuffix(strings.ToLower(real), ".evtx") {
			return WriteJSON(w, 200, map[string]any{
				"content": "Windows .evtx files are binary; use Event Viewer directly.",
			})
		}
		data, readErr := os.ReadFile(real)
		if readErr != nil {
			return NewHTTPError(500, "读取日志失败")
		}
		all := strings.Split(strings.TrimRight(strings.ReplaceAll(string(data), "\r\n", "\n"), "\n"), "\n")
		if len(all) > tail {
			all = all[len(all)-tail:]
		}
		if len(all) == 1 && all[0] == "" {
			all = []string{}
		}
		return WriteJSON(w, 200, map[string]any{"path": path, "lines": all, "count": len(all)})
	})
	a.Post("/api/logs/add", perm("logs"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Name string `json:"name"`
			Path string `json:"path"`
			Desc string `json:"desc"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		for _, v := range []string{req.Name, req.Path} {
			for _, c := range v {
				if c < 0x20 || c == 0x7f {
					return NewHTTPError(400, "日志路径/名称含非法控制字符")
				}
			}
		}
		if req.Name == "" || len(req.Name) > 64 || req.Path == "" || len(req.Path) > 1024 {
			return NewHTTPError(422, "名称或路径长度非法")
		}
		custom := loadCustomLogs()
		custom = append(custom, map[string]any{
			"id": "custom_" + itoa(int(time.Now().Unix())), "name": req.Name,
			"path": req.Path, "desc": req.Desc,
		})
		if err := WriteJSONFile(DataPath("logs.json"), custom); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/logs/clear", perm("logs"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Path string `json:"path"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		allowed := false
		for _, p := range allLogRegistry() {
			if p == req.Path {
				allowed = true
				break
			}
		}
		if !allowed {
			return NewHTTPError(400, "仅允许清空日志列表中登记的日志文件")
		}
		safe, err := safeLogPath(req.Path)
		if err != nil {
			return err
		}
		if !HostExists(safe) {
			return NewHTTPError(404, "Log file not found")
		}
		if err := HostWriteBytes(safe, []byte(""), 0o644); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
}
