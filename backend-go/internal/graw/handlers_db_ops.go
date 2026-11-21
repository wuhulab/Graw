package graw

import (
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

// 数据库日常运维：用户/授权、备份/恢复、引擎状态与启停。
// 覆盖 MySQL/MariaDB 与 PostgreSQL；Redis/MongoDB 返回到明确的「暂不支持」提示，
// 避免用半成品命令误伤生产库。

var dbUserNameRe = regexp.MustCompile(`^[A-Za-z0-9._-]{1,32}$`)
var dbHostRe = regexp.MustCompile(`^[A-Za-z0-9.%:_-]{1,64}$`)

var mysqlPrivs = map[string]bool{
	"ALL PRIVILEGES": true, "SELECT": true, "INSERT": true, "UPDATE": true, "DELETE": true,
	"CREATE": true, "DROP": true, "ALTER": true, "INDEX": true, "REFERENCES": true,
	"EXECUTE": true, "CREATE VIEW": true, "SHOW VIEW": true, "TRIGGER": true,
	"CREATE ROUTINE": true, "ALTER ROUTINE": true, "EVENT": true, "LOCK TABLES": true,
	"PROCESS": true, "RELOAD": true, "REPLICATION CLIENT": true, "REPLICATION SLAVE": true,
}

func dbConnectionByID(id string) map[string]any {
	for _, c := range loadDBConnections() {
		if str(c["id"]) == id {
			return c
		}
	}
	return nil
}

func stringifyPrivs(v any) ([]string, *HTTPError) {
	out := []string{}
	for _, p := range toStringSlice(v) {
		u := strings.ToUpper(strings.TrimSpace(p))
		if u == "" {
			continue
		}
		if !mysqlPrivs[u] {
			return nil, NewHTTPError(400, "不支持的权限: "+p)
		}
		out = append(out, u)
	}
	if len(out) == 0 {
		out = []string{"SELECT"}
	}
	return out, nil
}

func dbBackupDir(connID string) string {
	dir := filepath.Join(DataPath("db_backups"), connID)
	_ = os.MkdirAll(dir, 0o700)
	return dir
}

func listDBBackups(connID string) []map[string]any {
	dir := dbBackupDir(connID)
	entries, err := os.ReadDir(dir)
	if err != nil {
		return []map[string]any{}
	}
	out := []map[string]any{}
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".sql") {
			continue
		}
		info, _ := e.Info()
		size := int64(0)
		mod := ""
		if info != nil {
			size, mod = info.Size(), info.ModTime().Format("2006-01-02 15:04:05")
		}
		out = append(out, map[string]any{"name": e.Name(), "size": size, "time": mod})
	}
	sort.Slice(out, func(i, j int) bool { return str(out[i]["name"]) > str(out[j]["name"]) })
	return out
}

func mysqlDefaultsFile(conn map[string]any) (string, func(), error) {
	dir := DataPath("tmp")
	_ = os.MkdirAll(dir, 0o700)
	path := filepath.Join(dir, "mysql_"+randomHex(6)+".cnf")
	content := fmt.Sprintf("[client]\nuser=%s\npassword=%s\nhost=%s\nport=%d\n",
		str(conn["username"]), str(conn["password"]), defaultString(str(conn["host"]), "127.0.0.1"), dbConnPort(conn, 3306))
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		return "", nil, err
	}
	return path, func() { _ = os.Remove(path) }, nil
}

func pgPassFile(conn map[string]any, database string) (string, func(), error) {
	dir := DataPath("tmp")
	_ = os.MkdirAll(dir, 0o700)
	path := filepath.Join(dir, "pgpass_"+randomHex(6))
	esc := func(s string) string {
		return strings.NewReplacer(`\`, `\\`, `:`, `\:`).Replace(s)
	}
	line := strings.Join([]string{
		esc(defaultString(str(conn["host"]), "127.0.0.1")), itoa(dbConnPort(conn, 5432)),
		esc(database), esc(defaultString(str(conn["username"]), "postgres")), esc(str(conn["password"])),
	}, ":")
	if err := os.WriteFile(path, []byte(line+"\n"), 0o600); err != nil {
		return "", nil, err
	}
	return path, func() { _ = os.Remove(path) }, nil
}

// --- 引擎状态 ---

func dbEngineInfo(engine string) map[string]any {
	names := map[string][]string{
		"mysql":      {"mysqld", "mariadbd"},
		"postgresql": {"postgres"},
		"redis":      {"redis-server"},
		"mongodb":    {"mongod"},
	}
	units := map[string][]string{
		"mysql": {"mysql", "mariadb"}, "postgresql": {"postgresql"},
		"redis": {"redis-server", "redis"}, "mongodb": {"mongod"},
	}
	running := false
	for _, n := range names[engine] {
		if detectProcess(n) {
			running = true
			break
		}
	}
	unit := ""
	for _, u := range units[engine] {
		if HostCmd(5*time.Second, "systemctl", "cat", u).Code == 0 {
			unit = u
			break
		}
	}
	return map[string]any{"engine": engine, "running": running, "unit": unit}
}

// --- 路由 ---

func registerDBOpsRoutes(a *App) {
	a.Get("/api/databases/engines", perm("database"), func(w http.ResponseWriter, r *http.Request) error {
		out := []map[string]any{}
		for _, e := range []string{"mysql", "postgresql", "redis", "mongodb"} {
			out = append(out, dbEngineInfo(e))
		}
		return WriteJSON(w, 200, map[string]any{"engines": out})
	})
	a.Post("/api/databases/engines/{engine}/action", perm("database"), func(w http.ResponseWriter, r *http.Request) error {
		engine := PathParam(r, "engine")
		var req struct {
			Action string `json:"action"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		act := strings.ToLower(strings.TrimSpace(req.Action))
		if act != "start" && act != "stop" && act != "restart" {
			return NewHTTPError(400, "仅支持 start/stop/restart")
		}
		info := dbEngineInfo(engine)
		unit := str(info["unit"])
		if unit == "" {
			return NewHTTPError(400, "未找到可管理的系统服务（可能运行在容器中）")
		}
		res := HostCmd(120*time.Second, "systemctl", act, unit)
		if res.Code != 0 {
			return NewHTTPError(500, strings.TrimSpace(res.Stderr+res.Stdout))
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "engine": engine, "action": act})
	})

	a.Get("/api/databases/connections/{conn_id}/users", perm("database"), func(w http.ResponseWriter, r *http.Request) error {
		conn := dbConnectionByID(PathParam(r, "conn_id"))
		if conn == nil {
			return NewHTTPError(404, "Connection not found")
		}
		db, err := openSQL(conn, false)
		if err != nil {
			return NewHTTPError(500, "连接失败: "+err.Error())
		}
		defer db.Close()
		users := []map[string]any{}
		switch str(conn["db_type"]) {
		case "mysql":
			rows, err := db.Query("SELECT user, host FROM mysql.user ORDER BY user, host")
			if err != nil {
				return NewHTTPError(500, "读取用户失败: "+err.Error())
			}
			defer rows.Close()
			for rows.Next() {
				var u, h string
				if rows.Scan(&u, &h) == nil {
					users = append(users, map[string]any{"username": u, "host": h})
				}
			}
		case "postgresql":
			rows, err := db.Query("SELECT rolname, rolsuper, rolcanlogin FROM pg_roles WHERE rolname NOT LIKE 'pg_%' ORDER BY rolname")
			if err != nil {
				return NewHTTPError(500, "读取角色失败: "+err.Error())
			}
			defer rows.Close()
			for rows.Next() {
				var n string
				var super, login bool
				if rows.Scan(&n, &super, &login) == nil {
					users = append(users, map[string]any{"username": n, "host": "", "super": super, "login": login})
				}
			}
		default:
			return NewHTTPError(400, "该数据库类型暂不支持用户管理（仅 MySQL/PostgreSQL）")
		}
		return WriteJSON(w, 200, map[string]any{"users": users})
	})

	a.Post("/api/databases/connections/{conn_id}/users", perm("database"), func(w http.ResponseWriter, r *http.Request) error {
		conn := dbConnectionByID(PathParam(r, "conn_id"))
		if conn == nil {
			return NewHTTPError(404, "Connection not found")
		}
		var req struct {
			Username   string   `json:"username"`
			Password   string   `json:"password"`
			Host       string   `json:"host"`
			Database   string   `json:"database"`
			Privileges []string `json:"privileges"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if !dbUserNameRe.MatchString(req.Username) {
			return NewHTTPError(400, "用户名仅允许字母、数字、点、下划线、中划线")
		}
		if len(req.Password) < 6 || len(req.Password) > 128 {
			return NewHTTPError(400, "密码长度需为 6-128 位")
		}
		host := req.Host
		if host == "" {
			host = "%"
		}
		if !dbHostRe.MatchString(host) {
			return NewHTTPError(400, "来源主机格式非法")
		}
		db, err := openSQL(conn, false)
		if err != nil {
			return NewHTTPError(500, "连接失败: "+err.Error())
		}
		defer db.Close()
		switch str(conn["db_type"]) {
		case "mysql":
			pwd := mysqlString(req.Password)
			if _, err := db.Exec("CREATE USER IF NOT EXISTS '" + req.Username + "'@'" + host + "' IDENTIFIED BY '" + pwd + "'"); err != nil {
				return NewHTTPError(500, "创建用户失败: "+err.Error())
			}
			if _, err := db.Exec("ALTER USER '" + req.Username + "'@'" + host + "' IDENTIFIED BY '" + pwd + "'"); err != nil {
				return NewHTTPError(500, "设置密码失败: "+err.Error())
			}
			if req.Database != "" {
				if !dbNameRe.MatchString(req.Database) {
					return NewHTTPError(400, "数据库名非法")
				}
				privs, herr := stringifyPrivs(req.Privileges)
				if herr != nil {
					return herr
				}
				stmt := "GRANT " + strings.Join(privs, ", ") + " ON `" + req.Database + "`.* TO '" + req.Username + "'@'" + host + "'"
				if _, err := db.Exec(stmt); err != nil {
					return NewHTTPError(500, "授权失败: "+err.Error())
				}
			}
		case "postgresql":
			name := quotePGIdent(req.Username)
			stmt := "CREATE ROLE " + name + " LOGIN PASSWORD '" + strings.ReplaceAll(req.Password, "'", "''") + "'"
			if _, err := db.Exec(stmt); err != nil {
				if !strings.Contains(strings.ToLower(err.Error()), "already exists") {
					return NewHTTPError(500, "创建角色失败: "+err.Error())
				}
				if _, err := db.Exec("ALTER ROLE " + name + " LOGIN PASSWORD '" + strings.ReplaceAll(req.Password, "'", "''") + "'"); err != nil {
					return NewHTTPError(500, "设置密码失败: "+err.Error())
				}
			}
			if req.Database != "" {
				if !dbNameRe.MatchString(req.Database) {
					return NewHTTPError(400, "数据库名非法")
				}
				if _, err := db.Exec("GRANT CONNECT ON DATABASE " + quotePGIdent(req.Database) + " TO " + name); err != nil {
					return NewHTTPError(500, "授权失败: "+err.Error())
				}
			}
		default:
			return NewHTTPError(400, "该数据库类型暂不支持用户管理（仅 MySQL/PostgreSQL）")
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})

	a.Delete("/api/databases/connections/{conn_id}/users/{username}", perm("database"), func(w http.ResponseWriter, r *http.Request) error {
		conn := dbConnectionByID(PathParam(r, "conn_id"))
		if conn == nil {
			return NewHTTPError(404, "Connection not found")
		}
		username := PathParam(r, "username")
		if !dbUserNameRe.MatchString(username) {
			return NewHTTPError(400, "用户名非法")
		}
		host := defaultString(r.URL.Query().Get("host"), "%")
		if !dbHostRe.MatchString(host) {
			return NewHTTPError(400, "来源主机格式非法")
		}
		db, err := openSQL(conn, false)
		if err != nil {
			return NewHTTPError(500, "连接失败: "+err.Error())
		}
		defer db.Close()
		switch str(conn["db_type"]) {
		case "mysql":
			if _, err := db.Exec("DROP USER IF EXISTS '" + username + "'@'" + host + "'"); err != nil {
				return NewHTTPError(500, "删除用户失败: "+err.Error())
			}
		case "postgresql":
			if _, err := db.Exec("DROP ROLE IF EXISTS " + quotePGIdent(username)); err != nil {
				return NewHTTPError(500, "删除角色失败: "+err.Error())
			}
		default:
			return NewHTTPError(400, "该数据库类型暂不支持用户管理")
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})

	a.Get("/api/databases/connections/{conn_id}/backups", perm("database"), func(w http.ResponseWriter, r *http.Request) error {
		conn := dbConnectionByID(PathParam(r, "conn_id"))
		if conn == nil {
			return NewHTTPError(404, "Connection not found")
		}
		return WriteJSON(w, 200, map[string]any{"backups": listDBBackups(str(conn["id"]))})
	})

	a.Post("/api/databases/connections/{conn_id}/backup", perm("database"), func(w http.ResponseWriter, r *http.Request) error {
		conn := dbConnectionByID(PathParam(r, "conn_id"))
		if conn == nil {
			return NewHTTPError(404, "Connection not found")
		}
		var req struct {
			Database string `json:"database"`
		}
		_ = DecodeJSON(r, &req)
		name := time.Now().Format("20060102_150405") + ".sql"
		out := filepath.Join(dbBackupDir(str(conn["id"])), name)
		hostOut := HostVisiblePath(out)
		switch str(conn["db_type"]) {
		case "mysql":
			cnf, cleanup, err := mysqlDefaultsFile(conn)
			if err != nil {
				return NewHTTPError(500, "写入临时凭据失败")
			}
			defer cleanup()
			args := []string{"--defaults-extra-file=" + cnf, "--single-transaction", "--routines", "--events",
				"--result-file=" + hostOut}
			if req.Database != "" {
				if !dbNameRe.MatchString(req.Database) {
					return NewHTTPError(400, "数据库名非法")
				}
				args = append(args, req.Database)
			} else {
				args = append(args, "--all-databases")
			}
			res := HostCmd(600*time.Second, "mysqldump", args...)
			if res.Code != 0 {
				_ = os.Remove(out)
				return NewHTTPError(500, strings.TrimSpace(res.Stderr+res.Stdout))
			}
		case "postgresql":
			if windowsTarget() {
				return NewHTTPError(400, "Windows 下暂不支持 PostgreSQL 备份")
			}
			dbName := defaultString(req.Database, defaultString(str(conn["database"]), "postgres"))
			if !dbNameRe.MatchString(dbName) {
				return NewHTTPError(400, "数据库名非法")
			}
			pgpass, cleanup, err := pgPassFile(conn, dbName)
			if err != nil {
				return NewHTTPError(500, "写入临时凭据失败")
			}
			defer cleanup()
			cmd := "PGPASSFILE=" + shellQuote(pgpass) + " pg_dump -h " + shellQuote(defaultString(str(conn["host"]), "127.0.0.1")) +
				" -p " + itoa(dbConnPort(conn, 5432)) + " -U " + shellQuote(defaultString(str(conn["username"]), "postgres")) +
				" -d " + shellQuote(dbName) + " -f " + shellQuote(hostOut)
			res := HostShell(600*time.Second, cmd)
			if res.Code != 0 {
				_ = os.Remove(out)
				return NewHTTPError(500, strings.TrimSpace(res.Stderr+res.Stdout))
			}
		default:
			return NewHTTPError(400, "该数据库类型暂不支持备份（仅 MySQL/PostgreSQL）")
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "name": name})
	})

	a.Post("/api/databases/connections/{conn_id}/backups/{name}/restore", perm("database"), func(w http.ResponseWriter, r *http.Request) error {
		conn := dbConnectionByID(PathParam(r, "conn_id"))
		if conn == nil {
			return NewHTTPError(404, "Connection not found")
		}
		name := PathParam(r, "name")
		if strings.Contains(name, "/") || strings.Contains(name, "\\") || !strings.HasSuffix(name, ".sql") {
			return NewHTTPError(400, "备份文件名非法")
		}
		path := filepath.Join(dbBackupDir(str(conn["id"])), name)
		if !Exists(path) {
			return NewHTTPError(404, "备份不存在")
		}
		var req struct {
			Database string `json:"database"`
		}
		_ = DecodeJSON(r, &req)
		hostPath := HostVisiblePath(path)
		switch str(conn["db_type"]) {
		case "mysql":
			cnf, cleanup, err := mysqlDefaultsFile(conn)
			if err != nil {
				return NewHTTPError(500, "写入临时凭据失败")
			}
			defer cleanup()
			dbName := req.Database
			if dbName != "" && !dbNameRe.MatchString(dbName) {
				return NewHTTPError(400, "数据库名非法")
			}
			cmd := "mysql --defaults-extra-file=" + shellQuote(cnf)
			if dbName != "" {
				cmd += " " + shellQuote(dbName)
			}
			cmd += " < " + shellQuote(hostPath)
			res := HostShell(600*time.Second, cmd)
			if res.Code != 0 {
				return NewHTTPError(500, strings.TrimSpace(res.Stderr+res.Stdout))
			}
		case "postgresql":
			if windowsTarget() {
				return NewHTTPError(400, "Windows 下暂不支持 PostgreSQL 恢复")
			}
			dbName := defaultString(req.Database, defaultString(str(conn["database"]), "postgres"))
			if !dbNameRe.MatchString(dbName) {
				return NewHTTPError(400, "数据库名非法")
			}
			pgpass, cleanup, err := pgPassFile(conn, dbName)
			if err != nil {
				return NewHTTPError(500, "写入临时凭据失败")
			}
			defer cleanup()
			cmd := "PGPASSFILE=" + shellQuote(pgpass) + " psql -h " + shellQuote(defaultString(str(conn["host"]), "127.0.0.1")) +
				" -p " + itoa(dbConnPort(conn, 5432)) + " -U " + shellQuote(defaultString(str(conn["username"]), "postgres")) +
				" -d " + shellQuote(dbName) + " -f " + shellQuote(hostPath)
			res := HostShell(600*time.Second, cmd)
			if res.Code != 0 {
				return NewHTTPError(500, strings.TrimSpace(res.Stderr+res.Stdout))
			}
		default:
			return NewHTTPError(400, "该数据库类型暂不支持恢复")
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})

	a.Delete("/api/databases/connections/{conn_id}/backups/{name}", perm("database"), func(w http.ResponseWriter, r *http.Request) error {
		conn := dbConnectionByID(PathParam(r, "conn_id"))
		if conn == nil {
			return NewHTTPError(404, "Connection not found")
		}
		name := PathParam(r, "name")
		if strings.Contains(name, "/") || strings.Contains(name, "\\") || !strings.HasSuffix(name, ".sql") {
			return NewHTTPError(400, "备份文件名非法")
		}
		if err := os.Remove(filepath.Join(dbBackupDir(str(conn["id"])), name)); err != nil {
			return NewHTTPError(404, "备份不存在")
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
}

func quotePGIdent(s string) string {
	return `"` + strings.ReplaceAll(s, `"`, `""`) + `"`
}

// mysqlString 转义 MySQL 单引号字符串（默认 sql_mode 下反斜杠也是转义符）。
func mysqlString(s string) string {
	s = strings.ReplaceAll(s, `\`, `\\`)
	return strings.ReplaceAll(s, "'", "''")
}
