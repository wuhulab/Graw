package graw

import (
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

// 计划任务增强：超时 / 失败重试 / 执行报告 / 保留份数 / 脚本库 / 分组 / 导入导出。
// 说明：系统 crontab 仍按原命令调度；超时与重试在「立即执行」以及导入后的
// 手动执行链路生效，避免改写用户已有 crontab 行的语义。

func normalizeCronTimeout(v int) int {
	if v <= 0 {
		return 300
	}
	if v > 86400 {
		return 86400
	}
	return v
}

func normalizeCronRetry(v int) int {
	if v < 0 {
		return 0
	}
	if v > 10 {
		return 10
	}
	return v
}

func normalizeCronKeep(v int) int {
	if v <= 0 {
		return 10
	}
	if v > 100 {
		return 100
	}
	return v
}

func cronGroups(tasks []map[string]any) []string {
	seen := map[string]bool{}
	out := []string{}
	for _, t := range tasks {
		g := strings.TrimSpace(str(t["group"]))
		if g == "" {
			g = "默认"
		}
		if !seen[g] {
			seen[g] = true
			out = append(out, g)
		}
	}
	sort.Strings(out)
	if len(out) == 0 {
		out = append(out, "默认")
	}
	return out
}

// --- 脚本库 ---

func loadCronScripts() []map[string]any {
	var items []map[string]any
	_ = ReadJSON(DataPath("cron_scripts.json"), &items)
	return items
}

func saveCronScripts(items []map[string]any) error {
	CaptureSnapshot("cron-scripts", "cron", DataPath("cron_scripts.json"))
	return WriteJSONFile(DataPath("cron_scripts.json"), items)
}

// --- 执行报告 ---

func cronReportDir() string {
	dir := DataPath("cron_reports")
	_ = os.MkdirAll(dir, 0o700)
	return dir
}

func saveCronReport(task map[string]any, output string, exitCode, attempts int) string {
	id := str(task["id"])
	if !strings.HasPrefix(id, "task_") {
		id = "task_unknown"
	}
	name := id + "_" + time.Now().Format("20060102_150405") + ".log"
	path := filepath.Join(cronReportDir(), name)
	header := fmt.Sprintf("任务: %s\n命令: %s\n退出码: %d\n尝试次数: %d\n时间: %s\n\n",
		str(task["name"]), str(task["command"]), exitCode, attempts, time.Now().Format("2006-01-02 15:04:05"))
	_ = os.WriteFile(path, []byte(header+output), 0o600)
	pruneCronReports(id, intOf(task["keep_count"]))
	return name
}

func pruneCronReports(taskID string, keep int) {
	if keep <= 0 {
		keep = 10
	}
	entries, err := os.ReadDir(cronReportDir())
	if err != nil {
		return
	}
	names := []string{}
	for _, e := range entries {
		if !e.IsDir() && strings.HasPrefix(e.Name(), taskID+"_") {
			names = append(names, e.Name())
		}
	}
	sort.Strings(names)
	for len(names) > keep {
		_ = os.Remove(filepath.Join(cronReportDir(), names[0]))
		names = names[1:]
	}
}

func latestCronReport(taskID string) (string, string) {
	entries, err := os.ReadDir(cronReportDir())
	if err != nil {
		return "", ""
	}
	latest := ""
	for _, e := range entries {
		if !e.IsDir() && strings.HasPrefix(e.Name(), taskID+"_") && e.Name() > latest {
			latest = e.Name()
		}
	}
	if latest == "" {
		return "", ""
	}
	data, err := os.ReadFile(filepath.Join(cronReportDir(), latest))
	if err != nil {
		return latest, ""
	}
	return latest, string(data)
}

// runCronTaskNow 执行一次任务：超时、重试、保存报告、失败告警。
func runCronTaskNow(task map[string]any) map[string]any {
	if windowsTarget() {
		runCommand(30*time.Second, "schtasks", "/run", "/tn", str(task["name"]))
		return map[string]any{"ok": true, "mode": "schtasks", "output": "已下发到 Windows 任务计划程序"}
	}
	timeout := normalizeCronTimeout(intOf(task["timeout"]))
	retry := normalizeCronRetry(intOf(task["retry"]))
	output := ""
	code := 0
	attempts := 0
	for i := 0; i <= retry; i++ {
		attempts = i + 1
		res := HostShell(time.Duration(timeout)*time.Second, str(task["command"]))
		output = strings.TrimSpace(res.Stdout + res.Stderr)
		code = res.Code
		if code == 0 {
			break
		}
		if i < retry {
			time.Sleep(3 * time.Second)
		}
	}
	report := saveCronReport(task, output, code, attempts)
	if code != 0 && boolOf(task["alert"]) {
		msg := "【Graw 计划任务失败】" + str(task["name"]) + " 退出码 " + strconv.Itoa(code) + "，已重试 " + strconv.Itoa(attempts) + " 次"
		sent, failed := pushAll(msg)
		appendNotifyLog(msg, sent, failed)
	}
	if len(output) > 8000 {
		output = output[:8000] + "…"
	}
	return map[string]any{
		"ok": code == 0, "exit_code": code, "attempts": attempts,
		"output": output, "report": report,
	}
}

// --- 导入 / 导出 ---

func cronExport() map[string]any {
	return map[string]any{
		"version":     1,
		"exported_at": time.Now().Format("2006-01-02T15:04:05"),
		"tasks":       loadCronTasks(),
		"scripts":     loadCronScripts(),
	}
}

func cronImport(tasks []map[string]any, scripts []map[string]any) (int, int, error) {
	existing := loadCronTasks()
	byName := map[string]map[string]any{}
	for _, t := range existing {
		byName[str(t["name"])] = t
	}
	taskCount := 0
	// touched 记录本次导入新增/更新的任务，落盘后需要同步到系统调度器。
	// Windows 的 schtasks 不会从 cron.json 自动生效，必须逐个重新注册。
	touched := []map[string]any{}
	touchedExisted := map[string]bool{}
	for _, t := range tasks {
		name := strings.TrimSpace(str(t["name"]))
		schedule := strings.TrimSpace(str(t["schedule"]))
		command := strings.TrimSpace(str(t["command"]))
		if name == "" || schedule == "" || command == "" {
			continue
		}
		if validateCronSchedule(schedule) != nil || validateCronCommand(command) != nil {
			continue
		}
		if cur := byName[name]; cur != nil {
			cur["schedule"] = schedule
			cur["command"] = command
			touched = append(touched, cur)
			touchedExisted[str(cur["id"])] = true
			taskCount++
			continue
		}
		nt := map[string]any{
			"id": "task_" + randomHex(8), "name": name, "schedule": schedule,
			"command": command, "enabled": boolOf(t["enabled"]),
			"task_type": str(t["task_type"]), "content": str(t["content"]),
			"group": defaultString(str(t["group"]), "默认"), "alert": boolOf(t["alert"]),
			"timeout":    normalizeCronTimeout(intOf(t["timeout"])),
			"retry":      normalizeCronRetry(intOf(t["retry"])),
			"keep_count": normalizeCronKeep(intOf(t["keep_count"])),
			"created_at": time.Now().Format("2006-01-02T15:04:05"),
		}
		existing = append(existing, nt)
		byName[name] = nt
		touched = append(touched, nt)
		taskCount++
	}
	if err := saveCronTasks(existing); err != nil {
		return taskCount, 0, err
	}

	oldScripts := loadCronScripts()
	scriptByName := map[string]bool{}
	for _, s := range oldScripts {
		scriptByName[str(s["name"])] = true
	}
	scriptCount := 0
	for _, s := range scripts {
		name := strings.TrimSpace(str(s["name"]))
		content := str(s["content"])
		if name == "" || content == "" || scriptByName[name] {
			continue
		}
		oldScripts = append(oldScripts, map[string]any{
			"id": "script_" + randomHex(8), "name": name, "content": content,
			"group":      defaultString(str(s["group"]), "默认"),
			"created_at": time.Now().Format("2006-01-02T15:04:05"),
		})
		scriptByName[name] = true
		scriptCount++
	}
	if err := saveCronScripts(oldScripts); err != nil {
		return taskCount, scriptCount, err
	}

	if windowsTarget() {
		for _, t := range touched {
			if err := syncWindowsTask(t, touchedExisted[str(t["id"])]); err != nil {
				return taskCount, scriptCount, NewHTTPError(500, "任务已保存，但同步 Windows 计划任务失败: "+err.Error())
			}
		}
	} else {
		crons := []map[string]any{}
		for _, t := range existing {
			if boolOf(t["enabled"]) {
				crons = append(crons, map[string]any{
					"schedule": str(t["schedule"]), "command": str(t["command"]), "enabled": true,
				})
			}
		}
		_, preserved := listLinuxCron()
		if err := rewriteLinuxCron(crons, preserved); err != nil {
			return taskCount, scriptCount, err
		}
	}
	return taskCount, scriptCount, nil
}

func registerCronEnhancedRoutes(a *App) {
	a.Get("/api/cron/scripts", perm("cron"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"scripts": loadCronScripts()})
	})
	a.Post("/api/cron/scripts", perm("cron"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			ID      string `json:"id"`
			Name    string `json:"name"`
			Content string `json:"content"`
			Group   string `json:"group"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if strings.TrimSpace(req.Name) == "" || len(req.Name) > 64 || strings.TrimSpace(req.Content) == "" {
			return NewHTTPError(400, "脚本名称与内容不能为空（名称不超过 64 字符）")
		}
		items := loadCronScripts()
		if req.ID != "" {
			for _, s := range items {
				if str(s["id"]) == req.ID {
					s["name"] = strings.TrimSpace(req.Name)
					s["content"] = req.Content
					s["group"] = defaultString(strings.TrimSpace(req.Group), "默认")
					// 修改现有脚本后必须落盘，否则接口返回成功但内容并未保存。
					if err := saveCronScripts(items); err != nil {
						return err
					}
					return WriteJSON(w, 200, s)
				}
			}
			return NewHTTPError(404, "脚本不存在")
		}
		item := map[string]any{
			"id": "script_" + randomHex(8), "name": strings.TrimSpace(req.Name),
			"content": req.Content, "group": defaultString(strings.TrimSpace(req.Group), "默认"),
			"created_at": time.Now().Format("2006-01-02T15:04:05"),
		}
		items = append(items, item)
		if err := saveCronScripts(items); err != nil {
			return err
		}
		return WriteJSON(w, 200, item)
	})
	a.Post("/api/cron/scripts/{script_id}/delete", perm("cron"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "script_id")
		items := loadCronScripts()
		kept := []map[string]any{}
		for _, s := range items {
			if str(s["id"]) != id {
				kept = append(kept, s)
			}
		}
		if len(kept) == len(items) {
			return NewHTTPError(404, "脚本不存在")
		}
		if err := saveCronScripts(kept); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Get("/api/cron/{task_id}/report", perm("cron"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "task_id")
		name, content := latestCronReport(id)
		return WriteJSON(w, 200, map[string]any{"name": name, "content": content})
	})
	a.Get("/api/cron/export", perm("cron"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, cronExport())
	})
	a.Post("/api/cron/import", perm("cron"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Tasks   []map[string]any `json:"tasks"`
			Scripts []map[string]any `json:"scripts"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		tasks, scripts, err := cronImport(req.Tasks, req.Scripts)
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "tasks": tasks, "scripts": scripts})
	})
}
