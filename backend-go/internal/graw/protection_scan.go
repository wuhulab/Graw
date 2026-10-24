package graw

import (
	"os"
	"path"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

// 保护机制扫描 / 备份辅助（与 Python routers/protection.py 对齐）。

const protectionIgnoreDays = 7

var dbImageKeywords = []string{
	"mysql", "mariadb", "postgres", "postgis", "mongo", "redis",
	"clickhouse", "elasticsearch", "cassandra", "neo4j", "couchdb",
	"influxdb", "cockroachdb", "timescaledb", "mssql", "oracle", "dynamodb",
}

var embeddedDBImageKeywords = []string{
	"sqlite",
	"grafana", "homeassistant", "home-assistant", "jellyfin", "gitea",
	"vaultwarden", "bitwarden", "nextcloud", "matomo", "wallabag",
	"frigate", "photoprism", "paperless", "bookstack", "wikijs",
	"superset", "grocy", "mealie", "firefly", "freshrss", "duplicati",
	"calibre", "zigbee2mqtt", "z2m", "n8n", "ghost", "etherpad",
	"redmine", "mattermost", "miniflux", "hedgedoc",
}

var protectionDBDataDirs = [][2]string{
	{"mongo", "/data/db"},
	{"mysql", "/var/lib/mysql"},
	{"mariadb", "/var/lib/mysql"},
	{"postgres", "/var/lib/postgresql/data"},
	{"postgis", "/var/lib/postgresql/data"},
	{"timescaledb", "/var/lib/postgresql/data"},
	{"clickhouse", "/var/lib/clickhouse"},
	{"elasticsearch", "/usr/share/elasticsearch/data"},
	{"cassandra", "/var/lib/cassandra"},
	{"redis", "/data"},
	{"neo4j", "/data"},
	{"couchdb", "/opt/couchdb/data"},
	{"influxdb", "/var/lib/influxdb"},
	{"cockroachdb", "/cockroach/cockroach-data"},
	{"mssql", "/var/opt/mssql"},
	{"grafana", "/var/lib/grafana"},
	{"homeassistant", "/config"},
	{"home-assistant", "/config"},
	{"jellyfin", "/config"},
	{"gitea", "/data"},
	{"vaultwarden", "/data"},
	{"bitwarden", "/data"},
	{"nextcloud", "/var/www/html/data"},
	{"matomo", "/var/www/html"},
	{"wallabag", "/var/www/wallabag/data"},
	{"frigate", "/media/frigate"},
	{"photoprism", "/photoprism/originals"},
	{"paperless", "/usr/src/paperless/data"},
	{"bookstack", "/var/www/bookstack"},
	{"wikijs", "/wiki/data"},
	{"superset", "/app/superset_home"},
	{"grocy", "/var/www/html/data"},
	{"mealie", "/app/data"},
	{"firefly", "/var/www/html/storage"},
	{"freshrss", "/var/www/FreshRSS/data"},
	{"duplicati", "/config"},
	{"calibre", "/calibre-library"},
	{"zigbee2mqtt", "/app/data"},
	{"z2m", "/app/data"},
	{"n8n", "/home/node/.n8n"},
	{"ghost", "/var/lib/ghost/content"},
	{"etherpad", "/var/lib/etherpad-lite"},
	{"redmine", "/usr/src/redmine"},
	{"mattermost", "/mattermost/data"},
	{"miniflux", "/var/lib/miniflux"},
	{"hedgedoc", "/hedgedoc/data"},
}

var protectionKnownDBDirsList = []string{
	"/var/lib/mysql", "/var/lib/mariadb", "/var/lib/postgresql", "/var/lib/redis",
	"/var/lib/mongodb", "/var/lib/mongo", "/var/lib/clickhouse", "/usr/share/elasticsearch/data",
}

var protectionSQLiteRootsLinux = []string{"/root", "/home", "/srv", "/var/www", "/opt", "/data", "/srv/www"}

var protectionSQLiteExts = []string{".db", ".sqlite", ".sqlite3", ".db3", ".sqlitedb"}

var protectionSQLiteSkipDirs = map[string]bool{
	"node_modules": true, ".git": true, ".cache": true, "lost+found": true,
	"venv": true, ".venv": true, "__pycache__": true, "proc": true, "sys": true,
	"dev": true, ".gradle": true, ".m2": true, ".npm": true,
	"appdata": true, "application data": true, "windows": true,
	"program files": true, "programdata": true, "recovery": true,
	"system volume information": true, "$recycle.bin": true, "perflogs": true,
	".trae-cn": true, ".vscode": true, ".pycharm": true, ".idea": true,
}

const (
	protectionSQLiteMaxDepth  = 5
	protectionSQLiteMaxItems  = 300
	protectionDefaultSchedule = "30 2 * * *"
	protectionBackupKeepDays  = 30
)

func protectionIsIgnored(kind, key string) bool {
	data := loadProtection()
	now := time.Now()
	for _, item := range protectionList(data, "ignored") {
		if str(item["kind"]) != kind || str(item["key"]) != key {
			continue
		}
		if boolOf(item["permanent"]) {
			return true
		}
		ignoredAt, err := time.Parse("2006-01-02T15:04:05", str(item["ignored_at"]))
		if err != nil {
			return true
		}
		if now.Sub(ignoredAt) < protectionIgnoreDays*24*time.Hour {
			return true
		}
	}
	return false
}

func protectionCurrentIgnored() []map[string]any {
	data := loadProtection()
	now := time.Now()
	out := []map[string]any{}
	for _, item := range protectionList(data, "ignored") {
		copied := map[string]any{}
		for k, v := range item {
			copied[k] = v
		}
		if boolOf(item["permanent"]) {
			copied["expire_at"] = "永久"
			out = append(out, copied)
			continue
		}
		ignoredAt, err := time.Parse("2006-01-02T15:04:05", str(item["ignored_at"]))
		if err != nil {
			ignoredAt = now
		}
		expireAt := ignoredAt.Add(protectionIgnoreDays * 24 * time.Hour)
		if now.Before(expireAt) {
			copied["expire_at"] = expireAt.Format("2006-01-02T15:04:05")
			out = append(out, copied)
		}
	}
	return out
}

func protectionBackupDir() string {
	if windowsTarget() {
		return `C:\GrawBackups`
	}
	return "/data/graw-backups"
}

func protectionIsDBImage(image string) bool {
	low := strings.ToLower(image)
	for _, kw := range dbImageKeywords {
		if strings.Contains(low, kw) {
			return true
		}
	}
	return false
}

func protectionIsEmbeddedDBImage(image string) bool {
	low := strings.ToLower(image)
	for _, kw := range embeddedDBImageKeywords {
		if strings.Contains(low, kw) {
			return true
		}
	}
	return false
}

func protectionDBDataDir(image string) string {
	low := strings.ToLower(image)
	for _, pair := range protectionDBDataDirs {
		if strings.Contains(low, pair[0]) {
			return pair[1]
		}
	}
	return "/data"
}

func protectionLooksNamed(volumeName string) bool {
	if volumeName == "" {
		return false
	}
	if len(volumeName) >= 60 && regexp.MustCompile(`^[0-9a-f]+$`).MatchString(strings.ToLower(volumeName)) {
		return false
	}
	return true
}

func protectionSQLitePersisted(files []string, persistent []map[string]any) bool {
	dests := []string{}
	for _, m := range persistent {
		dest := strings.TrimRight(str(m["Destination"]), "/")
		if strings.HasPrefix(dest, "/") {
			dests = append(dests, dest)
		}
	}
	if len(dests) == 0 {
		return false
	}
	for _, p := range files {
		p = strings.TrimRight(p, "/")
		if p == "" || !strings.HasPrefix(p, "/") {
			continue
		}
		for _, d := range dests {
			if p == d || strings.HasPrefix(p, d+"/") {
				return true
			}
		}
	}
	return false
}

// protectionEvaluateMounts 评估容器数据持久化情况；安全时返回 nil。
func protectionEvaluateMounts(cid, name, image, status string, mounts []map[string]any, category string, sqliteFiles []string) map[string]any {
	embedded := category == "embedded"
	detected := category == "detected"
	persistent := []map[string]any{}
	anonymous := []map[string]any{}
	for _, m := range mounts {
		mtype := str(m["Type"])
		if mtype == "bind" && str(m["Source"]) != "" {
			persistent = append(persistent, m)
		} else if mtype == "volume" {
			if protectionLooksNamed(str(m["Name"])) {
				persistent = append(persistent, m)
			} else {
				anonymous = append(anonymous, m)
			}
		}
	}
	if len(persistent) > 0 && !detected {
		return nil
	}
	level := "danger"
	if detected {
		if protectionSQLitePersisted(sqliteFiles, persistent) {
			level = "safe"
		} else if len(anonymous) > 0 {
			level = "warning"
		}
	} else if len(anonymous) > 0 {
		level = "warning"
	}
	shown := []string{}
	for i, p := range sqliteFiles {
		if i >= 3 {
			break
		}
		shown = append(shown, p)
	}
	shownText := strings.Join(shown, ", ")
	var message string
	switch {
	case detected && level == "safe":
		message = "检测到容器内存在 SQLite 数据库文件（" + shownText + "），已设置永久数据卷映射，数据安全；建议在「数据库文件」中将其纳入自动备份以统一管理。"
	case detected && level == "danger":
		message = "检测到容器内存在 SQLite 数据库文件（" + shownText + "），但未设置永久数据卷映射，删除/重建容器将导致数据全部丢失！"
	case detected:
		message = "检测到容器内存在 SQLite 数据库文件（" + shownText + "），但仅有匿名数据卷（容器删除时可能被连带清除），建议改用命名数据卷以便持久化与管理"
	case embedded && level == "danger":
		message = "容器内置数据库（如 SQLite）未设置永久数据卷映射，数据仅存于容器可写层，删除/重建容器将导致数据全部丢失！"
	case embedded:
		message = "容器内置数据库（如 SQLite）仅有匿名数据卷（容器删除时可能被连带清除），建议改用命名数据卷以便持久化与管理"
	case level == "danger":
		message = "容器未设置永久数据卷映射，数据仅存于容器可写层，删除/重建容器将导致数据库数据全部丢失！"
	default:
		message = "容器仅有匿名数据卷（容器删除时可能被连带清除），建议改用命名数据卷以便持久化与管理"
	}
	dataDir := protectionDBDataDir(image)
	if detected && level == "safe" {
		dataDir = ""
	}
	mountOut := []map[string]any{}
	for _, m := range mounts {
		source := str(m["Source"])
		if source == "" {
			source = str(m["Name"])
		}
		mountOut = append(mountOut, map[string]any{
			"type": str(m["Type"]), "destination": str(m["Destination"]), "source": source,
		})
	}
	filesOut := []any{}
	if detected {
		for _, f := range sqliteFiles {
			filesOut = append(filesOut, f)
		}
	}
	return map[string]any{
		"id": cid, "name": name, "image": image, "status": status,
		"level": level, "message": message, "data_dir": dataDir,
		"sqlite_files": filesOut, "mounts": mountOut,
		"has_persistent": len(persistent) > 0,
	}
}

func protectionMounts(raw map[string]any) []map[string]any {
	out := []map[string]any{}
	for _, item := range sliceOf(raw["Mounts"]) {
		out = append(out, mapOf(item))
	}
	return out
}

func protectionHasSQLiteExt(name string) bool {
	low := strings.ToLower(name)
	for _, ext := range protectionSQLiteExts {
		if strings.HasSuffix(low, ext) {
			return true
		}
	}
	return false
}

func protectionScanBindSQLite(mounts []map[string]any) []string {
	found := []string{}
	for _, m := range mounts {
		if str(m["Type"]) != "bind" || str(m["Source"]) == "" || str(m["Destination"]) == "" {
			continue
		}
		real := HostPath(str(m["Source"]))
		baseDest := strings.TrimRight(str(m["Destination"]), "/")
		if !IsDir(real) {
			continue
		}
		type entry struct {
			real, cdest string
			depth       int
		}
		stack := []entry{{real, baseDest, 0}}
		for len(stack) > 0 {
			cur := stack[len(stack)-1]
			stack = stack[:len(stack)-1]
			if cur.depth > protectionSQLiteMaxDepth {
				continue
			}
			entries, err := os.ReadDir(cur.real)
			if err != nil {
				continue
			}
			for _, e := range entries {
				if e.IsDir() {
					name := strings.ToLower(e.Name())
					if protectionSQLiteSkipDirs[name] || strings.HasPrefix(e.Name(), ".") {
						continue
					}
					stack = append(stack, entry{filepath.Join(cur.real, e.Name()), cur.cdest + "/" + e.Name(), cur.depth + 1})
				} else if protectionHasSQLiteExt(e.Name()) {
					info, err := e.Info()
					if err == nil && info.Size() > 0 {
						found = append(found, cur.cdest+"/"+e.Name())
						if len(found) >= protectionSQLiteMaxItems {
							return found
						}
					}
				}
			}
		}
	}
	return found
}

func protectionFindSQLiteCmd(dest string) string {
	return "find " + shellQuote(dest) + " \\( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' " +
		"-o -name '*.db3' -o -name '*.sqlitedb' \\) -not -path '/proc/*' -not -path '/sys/*' " +
		"-not -path '/dev/*' 2>/dev/null | head -10"
}

const protectionSQLiteBaseCmd = "find / -xdev \\( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' " +
	"-o -name '*.db3' -o -name '*.sqlitedb' \\) -not -path '/proc/*' -not -path '/sys/*' " +
	"-not -path '/dev/*' -not -path '/usr/lib/*' -not -path '/usr/share/*' -not -path '/etc/*' " +
	"-not -path '/var/cache/*' 2>/dev/null | head -10"

func protectionExecProbe(cid, cmd string) []string {
	res := dockerRun(20*time.Second, "exec", cid, "sh", "-c", cmd)
	if res.Code != 0 {
		return nil
	}
	out := []string{}
	for _, line := range strings.Split(res.Stdout, "\n") {
		line = strings.TrimSpace(line)
		if line != "" {
			out = append(out, line)
		}
	}
	return out
}

func protectionDetectContainerSQLite(cid string, mounts []map[string]any) []string {
	found := []string{}
	found = append(found, protectionExecProbe(cid, protectionSQLiteBaseCmd)...)
	for _, m := range mounts {
		if str(m["Type"]) != "bind" && str(m["Type"]) != "volume" {
			continue
		}
		dest := str(m["Destination"])
		if !strings.HasPrefix(dest, "/") {
			continue
		}
		found = append(found, protectionExecProbe(cid, protectionFindSQLiteCmd(dest))...)
	}
	seen := map[string]bool{}
	uniq := []string{}
	for _, p := range found {
		if !seen[p] {
			seen[p] = true
			uniq = append(uniq, p)
		}
	}
	return uniq
}

func protectionScanDocker() map[string]any {
	containers, res := dockerJSONLines("ps", "-a", "--format", "json")
	if res.Code != 0 {
		return map[string]any{"available": false, "reason": "Docker/Podman 不可用"}
	}
	warnings := []map[string]any{}
	for _, c := range containers {
		cid := str(c["Id"])
		name := str(c["Names"])
		if name == "" {
			name = str(c["name"])
		}
		image := str(c["Image"])
		status := str(c["Status"])
		if cid == "" || protectionIsIgnored("docker", name) {
			continue
		}
		raw, err := dockerInspectRaw(cid)
		if err != nil {
			continue
		}
		mounts := protectionMounts(raw)
		var warning map[string]any
		if protectionIsDBImage(image) {
			warning = protectionEvaluateMounts(cid[:minInt(12, len(cid))], name, image, status, mounts, "db", nil)
		} else if protectionIsEmbeddedDBImage(image) {
			warning = protectionEvaluateMounts(cid[:minInt(12, len(cid))], name, image, status, mounts, "embedded", nil)
		} else {
			files := append(protectionScanBindSQLite(mounts), protectionDetectContainerSQLite(cid, mounts)...)
			seen := map[string]bool{}
			uniq := []string{}
			for _, p := range files {
				if !seen[p] {
					seen[p] = true
					uniq = append(uniq, p)
				}
			}
			if len(uniq) > 0 {
				warning = protectionEvaluateMounts(cid[:minInt(12, len(cid))], name, image, status, mounts, "detected", uniq)
			}
		}
		if warning != nil {
			warnings = append(warnings, warning)
		}
	}
	return map[string]any{"available": true, "warnings": warnings}
}

func protectionKnownDBDirs() []string {
	dirs := append([]string{}, protectionKnownDBDirsList...)
	if windowsTarget() {
		for _, p := range []string{`C:\data`, `D:\data`, `E:\data`, `C:\GrawData`} {
			if IsDir(p) {
				dirs = append(dirs, p)
			}
		}
	}
	return dirs
}

func protectionSQLiteRoots() []string {
	if !windowsTarget() {
		return append([]string{}, protectionSQLiteRootsLinux...)
	}
	home, _ := os.UserHomeDir()
	roots := []string{}
	if home != "" {
		roots = append(roots, home)
	}
	for _, p := range []string{`C:\data`, `D:\data`, `E:\data`, `C:\GrawData`} {
		if IsDir(p) {
			roots = append(roots, p)
		}
	}
	return roots
}

func protectionIsCovered(viewPath string) bool {
	p := strings.TrimRight(viewPath, "/")
	targets := []string{p}
	data := loadProtection()
	for _, b := range protectionList(data, "backup_items") {
		bp := strings.TrimRight(str(b["path"]), "/")
		if bp != "" && (p == bp || strings.HasPrefix(p, bp+"/")) {
			return true
		}
		if bp != "" {
			targets = append(targets, bp)
		}
	}
	commands := []string{}
	for _, t := range loadCronTasks() {
		commands = append(commands, str(t["command"]))
	}
	joined := strings.Join(commands, " ")
	for _, t := range targets {
		if t != "" && strings.Contains(joined, t) {
			return true
		}
	}
	return false
}

func protectionDirSize(realPath string) any {
	var total int64
	count := 0
	err := filepath.Walk(realPath, func(p string, info os.FileInfo, err error) error {
		if err != nil || info == nil {
			return nil
		}
		if info.IsDir() {
			if p != realPath && (protectionSQLiteSkipDirs[strings.ToLower(info.Name())] || strings.HasPrefix(info.Name(), ".")) {
				return filepath.SkipDir
			}
			return nil
		}
		total += info.Size()
		count++
		if count > 5000 {
			return errProtectionStop
		}
		return nil
	})
	if err != nil && err != errProtectionStop {
		return nil
	}
	if err == errProtectionStop {
		return nil
	}
	return total
}

var errProtectionStop = &protectionStopError{}

type protectionStopError struct{}

func (e *protectionStopError) Error() string { return "stop" }

func protectionWalkSQLite(realRoot, viewRoot string, depth int, items *[]map[string]any) {
	if depth > protectionSQLiteMaxDepth || len(*items) >= protectionSQLiteMaxItems {
		return
	}
	entries, err := os.ReadDir(realRoot)
	if err != nil {
		return
	}
	for _, e := range entries {
		if len(*items) >= protectionSQLiteMaxItems {
			return
		}
		name := e.Name()
		if e.IsDir() {
			if protectionSQLiteSkipDirs[strings.ToLower(name)] || strings.HasPrefix(name, ".") {
				continue
			}
			protectionWalkSQLite(filepath.Join(realRoot, name), path.Join(viewRoot, name), depth+1, items)
		} else if protectionHasSQLiteExt(name) {
			info, err := e.Info()
			if err != nil || info.Size() <= 0 {
				continue
			}
			vp := path.Join(viewRoot, name)
			*items = append(*items, map[string]any{
				"path": vp, "type": "file", "size": info.Size(), "covered": protectionIsCovered(vp),
			})
		}
	}
}

func protectionScanDBFiles() []map[string]any {
	items := []map[string]any{}
	for _, d := range protectionKnownDBDirs() {
		real := HostPath(d)
		if !IsDir(real) {
			continue
		}
		entries, err := os.ReadDir(real)
		if err != nil || len(entries) == 0 {
			continue
		}
		items = append(items, map[string]any{
			"path": d, "type": "dir", "size": protectionDirSize(real), "covered": protectionIsCovered(d),
		})
	}
	for _, root := range protectionSQLiteRoots() {
		realRoot := HostPath(root)
		if IsDir(realRoot) {
			protectionWalkSQLite(realRoot, root, 0, &items)
		}
	}
	filtered := []map[string]any{}
	for _, it := range items {
		if !protectionIsIgnored("db_file", str(it["path"])) {
			filtered = append(filtered, it)
		}
	}
	return filtered
}

func protectionSanitizeName(p string) string {
	base := filepath.Base(strings.TrimRight(p, "/"))
	if base == "." || base == "" || base == "/" {
		base = "db"
	}
	base = regexp.MustCompile(`[^A-Za-z0-9_.-]`).ReplaceAllString(base, "_")
	if base == "" {
		base = "db"
	}
	return base
}

func protectionPowerShellQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", "''") + "'"
}

func protectionBuildBackupCommand(viewPath string) (string, error) {
	safe := protectionSanitizeName(viewPath)
	trimmed := strings.TrimRight(viewPath, "/")
	if trimmed == "" || strings.ContainsRune(trimmed, 0) || !(strings.HasPrefix(trimmed, "/") || windowsTarget()) {
		return "", NewHTTPError(400, "备份路径必须为绝对路径且不包含非法字符")
	}
	if windowsTarget() {
		parent, base := filepath.Split(trimmed)
		base = strings.TrimRight(base, `/\`)
		parent = strings.TrimRight(parent, `/\`)
		if base == "" {
			base = safe
		}
		if parent == "" {
			parent = `\`
		}
		if strings.HasPrefix(base, "-") {
			return "", NewHTTPError(400, "备份路径的文件/目录名不能以 - 开头")
		}
		return "powershell -NoProfile -Command " +
			`"New-Item -ItemType Directory -Force -Path C:\GrawBackups | Out-Null; ` +
			`$d=Get-Date -Format yyyyMMdd_HHmmss; ` +
			`tar -czf \"C:\GrawBackups\` + safe + `_$d.tar.gz\" -C ` +
			protectionPowerShellQuote(parent) + ` ` + protectionPowerShellQuote(base) + `"`, nil
	}
	parent, base := path.Split(trimmed)
	base = strings.TrimRight(base, "/")
	parent = strings.TrimRight(parent, "/")
	if base == "" {
		base = safe
	}
	if parent == "" {
		parent = "/"
	}
	if strings.HasPrefix(base, "-") {
		return "", NewHTTPError(400, "备份路径的文件/目录名不能以 - 开头")
	}
	return "mkdir -p /data/graw-backups && " +
		"tar -czf /data/graw-backups/" + safe + "_$(date +\\%Y\\%m\\%d_\\%H\\%M\\%S).tar.gz " +
		"-C " + shellQuote(parent) + " -- " + shellQuote(base) + " && " +
		"find /data/graw-backups -name '" + safe + "_*' -mtime +" + itoa(protectionBackupKeepDays) + " -delete", nil
}

// protectionCreateBackupTask 复用计划任务模块创建自动备份任务。
func protectionCreateBackupTask(name, schedule, command string) (map[string]any, error) {
	if err := validateCronSchedule(schedule); err != nil {
		return nil, err
	}
	if err := validateCronCommand(command); err != nil {
		return nil, err
	}
	task := map[string]any{
		"id": "task_" + randomHex(8), "name": name, "schedule": schedule,
		"command": command, "enabled": true, "task_type": "shell_command",
		"content": command, "group": "默认", "alert": false,
		"created_at": time.Now().Format("2006-01-02T15:04:05"),
	}
	if windowsTarget() {
		if err := syncWindowsTask(task, false); err != nil {
			return nil, err
		}
	} else {
		crons, preserved := listLinuxCron()
		crons = append(crons, map[string]any{"schedule": schedule, "command": command, "enabled": true})
		if err := rewriteLinuxCron(crons, preserved); err != nil {
			return nil, err
		}
	}
	if err := saveCronTasks(append(loadCronTasks(), task)); err != nil {
		return nil, err
	}
	return task, nil
}

func protectionAddBackup(viewPath, schedule string) (map[string]any, bool, error) {
	viewPath = strings.TrimSpace(viewPath)
	if viewPath == "" {
		return nil, false, NewHTTPError(400, "路径不能为空")
	}
	data := loadProtection()
	for _, b := range protectionList(data, "backup_items") {
		if str(b["path"]) == viewPath {
			return map[string]any{"ok": true, "already": true, "path": viewPath}, true, nil
		}
	}
	if schedule == "" {
		schedule = protectionDefaultSchedule
	}
	safe := protectionSanitizeName(viewPath)
	name := "Graw备份-" + safe
	command, err := protectionBuildBackupCommand(viewPath)
	if err != nil {
		return nil, false, err
	}
	task, err := protectionCreateBackupTask(name, schedule, command)
	if err != nil {
		return nil, false, NewHTTPError(500, "创建自动备份计划失败："+err.Error())
	}
	items := append(protectionList(data, "backup_items"), map[string]any{
		"path": viewPath, "task_id": str(task["id"]), "task_name": name,
		"schedule": schedule, "created_at": time.Now().Format("2006-01-02T15:04:05"),
	})
	data["backup_items"] = items
	if err := WriteJSONFile(DataPath("protection.json"), data); err != nil {
		return nil, false, err
	}
	return map[string]any{"ok": true, "already": false, "path": viewPath, "task": task}, false, nil
}
