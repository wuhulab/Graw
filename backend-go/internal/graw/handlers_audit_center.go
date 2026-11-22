package graw

import (
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// 统一日志审计：把面板内分散的日志源汇总成「分类 + 级别 + 关键词」可筛选的视图。
// 读取仍然复用各模块自己的文件，不复制数据；远程节点由 Agent 代理后在本机执行。

type auditEntry struct {
	Time     string `json:"time"`
	Category string `json:"category"`
	Level    string `json:"level"`
	Message  string `json:"message"`
}

func auditCategoryFiles(category string) []string {
	base := []string{}
	switch category {
	case "operation", "panel":
		base = append(base, DataPath("panel.log"))
	case "access":
		base = append(base, "/var/log/nginx/access.log")
	case "website":
		base = append(base, "/var/log/nginx/access.log", "/var/log/nginx/error.log")
	case "system":
		if windowsTarget() {
			base = append(base, DataPath("panel.log"))
		} else {
			base = append(base, "/var/log/syslog", "/var/log/dmesg")
		}
	case "host":
		if windowsTarget() {
			base = append(base, DataPath("panel.log"))
		} else {
			base = append(base, "/var/log/syslog", "/var/log/auth.log")
		}
	case "task":
		// 任务报告目录单独读取
	}
	return base
}

func auditLevelOf(line string) string {
	l := strings.ToLower(line)
	switch {
	case strings.Contains(l, "error"), strings.Contains(l, "fail"), strings.Contains(l, "failed"),
		strings.Contains(l, "panic"), strings.Contains(l, " 5"):
		return "error"
	case strings.Contains(l, "warn"), strings.Contains(l, "denied"), strings.Contains(l, " 4"):
		return "warn"
	default:
		return "info"
	}
}

func auditTimeOf(line string) string {
	// 面板日志格式：[2026-01-02 15:04:05] ...
	if strings.HasPrefix(line, "[") {
		if end := strings.Index(line, "]"); end > 1 {
			return line[1:end]
		}
	}
	// nginx 常见格式：2026/01/02 15:04:05
	if len(line) >= 20 && line[4] == '/' && line[7] == '/' {
		if t, err := time.Parse("2006/01/02 15:04:05", line[:19]); err == nil {
			return t.Format("2006-01-02 15:04:05")
		}
	}
	return ""
}

func readAuditFile(path string, limit int) []string {
	if !HostExists(path) {
		return nil
	}
	var text string
	if remoteActive() {
		res := HostCmd(20*time.Second, "tail", "-n", itoa(limit), "--", path)
		text = strings.TrimRight(res.Stdout, "\n")
	} else {
		// 本机直接读文件：Windows 开发环境没有 tail 命令，
		// 走 HostCmd 会让审计中心在本机永远返回空列表。
		text = strings.TrimRight(readTailText(HostPath(path), 4<<20), "\n")
	}
	if text == "" {
		return nil
	}
	lines := strings.Split(text, "\n")
	if len(lines) > limit {
		lines = lines[len(lines)-limit:]
	}
	return lines
}

func auditLoginEntries(limit int) []auditEntry {
	var logs []LoginLogEntry
	if err := ReadJSON(DataPath("login_logs.json"), &logs); err != nil {
		return nil
	}
	out := []auditEntry{}
	// login_logs.json 按「最新在前」保存，必须从 0 开始取，
	// 反向遍历会把最旧的记录返回给用户，最近的异常登录反而看不到。
	for i := 0; i < len(logs) && len(out) < limit; i++ {
		item := logs[i]
		level := "info"
		if item.Status != "success" {
			level = "warn"
		}
		if item.Abnormal {
			level = "error"
		}
		msg := item.Username + " " + item.IP + " " + item.Status
		if item.Detail != "" {
			msg += " " + item.Detail
		}
		out = append(out, auditEntry{Time: item.Time, Category: "login", Level: level, Message: msg})
	}
	return out
}

func auditTaskEntries(limit int) []auditEntry {
	entries, err := os.ReadDir(cronReportDir())
	if err != nil {
		return nil
	}
	files := []string{}
	for _, e := range entries {
		if !e.IsDir() && strings.HasSuffix(e.Name(), ".log") {
			files = append(files, e.Name())
		}
	}
	sort.Sort(sort.Reverse(sort.StringSlice(files)))
	out := []auditEntry{}
	for _, name := range files {
		if len(out) >= limit {
			break
		}
		data, err := os.ReadFile(filepath.Join(cronReportDir(), name))
		if err != nil {
			continue
		}
		lines := strings.Split(strings.TrimSpace(string(data)), "\n")
		msg := name
		if len(lines) > 0 {
			msg += " | " + lines[0]
		}
		level := "info"
		for _, l := range lines {
			if strings.Contains(strings.ToLower(l), "退出码: 0") {
				continue
			}
			if strings.Contains(strings.ToLower(l), "退出码:") {
				level = "error"
				break
			}
		}
		out = append(out, auditEntry{Time: "", Category: "task", Level: level, Message: msg})
	}
	return out
}

func registerAuditCenterRoutes(a *App) {
	a.Get("/api/audit/logs", perm("logs"), func(w http.ResponseWriter, r *http.Request) error {
		category := strings.TrimSpace(r.URL.Query().Get("category"))
		if category == "" {
			category = "operation"
		}
		level := strings.TrimSpace(r.URL.Query().Get("level"))
		query := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("q")))
		limit := QueryInt(r, "limit", 300)
		if limit < 1 {
			limit = 1
		}
		if limit > 2000 {
			limit = 2000
		}
		entries := []auditEntry{}
		switch category {
		case "login":
			entries = auditLoginEntries(limit)
		case "task":
			entries = auditTaskEntries(limit)
		default:
			for _, file := range auditCategoryFiles(category) {
				lines := readAuditFile(file, limit)
				for i := len(lines) - 1; i >= 0 && len(entries) < limit; i-- {
					line := lines[i]
					if strings.TrimSpace(line) == "" {
						continue
					}
					entries = append(entries, auditEntry{
						Time: auditTimeOf(line), Category: category, Level: auditLevelOf(line), Message: line,
					})
				}
			}
		}
		filtered := []auditEntry{}
		for _, e := range entries {
			if level != "" && e.Level != level {
				continue
			}
			if query != "" && !strings.Contains(strings.ToLower(e.Message), query) {
				continue
			}
			filtered = append(filtered, e)
			if len(filtered) >= limit {
				break
			}
		}
		return WriteJSON(w, 200, map[string]any{
			"category": category, "node": CurrentNodeID(), "entries": filtered,
		})
	})
	a.Get("/api/audit/categories", perm("logs"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"categories": []map[string]any{
			{"key": "operation", "name": "操作日志"},
			{"key": "login", "name": "登录日志"},
			{"key": "task", "name": "任务日志"},
			{"key": "access", "name": "访问日志"},
			{"key": "website", "name": "网站日志"},
			{"key": "system", "name": "系统日志"},
			{"key": "host", "name": "主机日志"},
			{"key": "panel", "name": "面板日志"},
		}})
	})
}
