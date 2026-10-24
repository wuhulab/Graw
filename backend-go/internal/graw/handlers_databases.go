package graw

import (
	"bufio"
	"database/sql"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/go-sql-driver/mysql"
	_ "github.com/lib/pq"
	"github.com/shirou/gopsutil/v4/process"
	_ "modernc.org/sqlite"
)

// 数据库管理：MySQL / PostgreSQL / SQLite 使用原生驱动，Redis 使用内置 RESP 客户端，
// MongoDB 通过 mongosh/mongo CLI 执行（避免引入庞大驱动依赖）。

var (
	sqlLeadRe   = regexp.MustCompile(`(?i)^\s*(select|with|show|desc|describe|explain|pragma|use|insert|update|delete|replace)\b`)
	sqlDangerRe = regexp.MustCompile(`(?i)\b(into\s+outfile|into\s+dumpfile|load_file|pg_read_file|pg_read_binary_file|pg_ls_dir|pg_write_file|prepare|execute|attach|import)\b`)
	sqlCopyRe   = regexp.MustCompile(`(?i)\bcopy\b.*\b(from|to)\b`)
	dbNameRe    = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)
)

var forbiddenRedisCommands = map[string]bool{
	"shutdown": true, "config": true, "save": true, "bgsave": true, "bgrewriteaof": true,
	"eval": true, "evalsha": true, "script": true, "replicaof": true, "slaveof": true,
	"migrate": true, "restore": true, "cluster": true, "swapdb": true,
	"flushall": true, "flushdb": true, "monitor": true, "psync": true, "sync": true,
	"module": true, "acl": true, "debug": true, "client": true, "pubsub": true,
	"memory": true, "lastsave": true, "failover": true,
}

func loadDBConnections() []map[string]any {
	var conns []map[string]any
	if err := ReadJSON(DataPath("databases.json"), &conns); err != nil {
		return []map[string]any{}
	}
	return conns
}

func saveDBConnections(conns []map[string]any) error {
	return WriteJSONFile(DataPath("databases.json"), conns)
}

func findDBConnection(id string) (map[string]any, error) {
	for _, c := range loadDBConnections() {
		if str(c["id"]) == id {
			return c, nil
		}
	}
	return nil, NewHTTPError(404, "Connection not found")
}

func dbConnPort(conn map[string]any, def int) int {
	p := int(int64Of(conn["port"]))
	if p == 0 {
		return def
	}
	return p
}

func dbConnStr(conn map[string]any, key, def string) string {
	v := str(conn[key])
	if v == "" {
		return def
	}
	return v
}

func detectProcess(nameFragment string) bool {
	procs, err := process.Processes()
	if err != nil {
		return false
	}
	for _, p := range procs {
		name, err := p.Name()
		if err != nil {
			continue
		}
		if strings.Contains(strings.ToLower(name), nameFragment) {
			return true
		}
	}
	return false
}

func mongoCLI() string {
	for _, name := range []string{"mongosh", "mongo"} {
		if p := HostWhich(name); p != "" {
			return p
		}
	}
	return ""
}

func sqliteAllowedRoots() []string {
	roots := []string{GetConfig().DataDir}
	env := strings.TrimSpace(os.Getenv("GRAW_SQLITE_ALLOWED_ROOTS"))
	if env == "" {
		env = "/srv,/var/www,/www,/opt"
	}
	for _, item := range strings.Split(env, ",") {
		item = strings.TrimSpace(item)
		if item == "" {
			continue
		}
		abs, err := filepath.Abs(item)
		if err == nil {
			roots = append(roots, abs)
		}
	}
	return roots
}

func sqliteFilePath(conn map[string]any) (string, error) {
	p := strings.TrimSpace(str(conn["database"]))
	if p == "" {
		return "", NewHTTPError(400, "SQLite 连接需填写数据库文件路径")
	}
	if !filepath.IsAbs(p) {
		p = filepath.Join(GetConfig().DataDir, p)
	}
	abs, err := filepath.Abs(p)
	if err != nil {
		return "", NewHTTPError(400, "SQLite 路径非法")
	}
	abs = filepath.Clean(abs)
	for _, root := range sqliteAllowedRoots() {
		if withinBase(filepath.Clean(root), abs) {
			return abs, nil
		}
	}
	return "", NewHTTPError(403, "SQLite 文件路径不在允许的根目录内")
}

func mysqlDSN(conn map[string]any, withDB bool) string {
	cfg := mysql.NewConfig()
	cfg.User = dbConnStr(conn, "username", "root")
	cfg.Passwd = str(conn["password"])
	cfg.Net = "tcp"
	cfg.Addr = net.JoinHostPort(dbConnStr(conn, "host", "127.0.0.1"), itoa(dbConnPort(conn, 3306)))
	cfg.Timeout = 5 * time.Second
	cfg.ParseTime = true
	if withDB {
		cfg.DBName = str(conn["database"])
	}
	return cfg.FormatDSN()
}

func pgDSN(conn map[string]any) string {
	return fmt.Sprintf("host=%s port=%d user=%s password=%s dbname=%s connect_timeout=5 sslmode=disable",
		dbConnStr(conn, "host", "127.0.0.1"), dbConnPort(conn, 5432),
		dbConnStr(conn, "username", "postgres"), str(conn["password"]),
		str(conn["database"]))
}

func openSQL(conn map[string]any, withDB bool) (*sql.DB, error) {
	switch str(conn["db_type"]) {
	case "mysql":
		return sql.Open("mysql", mysqlDSN(conn, withDB))
	case "postgresql":
		return sql.Open("postgres", pgDSN(conn))
	case "sqlite":
		path, err := sqliteFilePath(conn)
		if err != nil {
			return nil, err
		}
		return sql.Open("sqlite", path)
	}
	return nil, NewHTTPError(400, "Unsupported db_type")
}

func rejectDangerousSQL(raw string) bool {
	if strings.Contains(raw, "/*!") || strings.Contains(raw, "/*M!") {
		return true
	}
	cleaned := strings.TrimSpace(raw)
	if cleaned == "" {
		return true
	}
	parts := strings.Split(cleaned, ";")
	nonEmpty := 0
	for _, p := range parts {
		if strings.TrimSpace(p) != "" {
			nonEmpty++
		}
	}
	if nonEmpty > 1 {
		return true
	}
	m := sqlLeadRe.FindStringSubmatch(cleaned)
	if m == nil {
		return true
	}
	if strings.EqualFold(m[1], "select") && regexp.MustCompile(`(?i)\binto\b`).MatchString(cleaned) {
		return true
	}
	if sqlDangerRe.MatchString(cleaned) || sqlCopyRe.MatchString(cleaned) {
		return true
	}
	return false
}

func queryLeadVerb(sqlText string) string {
	m := sqlLeadRe.FindStringSubmatch(sqlText)
	if m == nil {
		return ""
	}
	return strings.ToLower(m[1])
}

func anyToJSON(v any) any {
	switch x := v.(type) {
	case []byte:
		return string(x)
	case time.Time:
		return x.Format("2006-01-02T15:04:05")
	case nil:
		return nil
	}
	return v
}

func runSQLQuery(db *sql.DB, sqlText string) (map[string]any, error) {
	verb := queryLeadVerb(sqlText)
	readVerb := verb == "select" || verb == "with" || verb == "show" || verb == "desc" ||
		verb == "describe" || verb == "explain" || verb == "pragma" || verb == "use"
	if !readVerb {
		res, err := db.Exec(sqlText)
		if err != nil {
			return nil, err
		}
		affected, _ := res.RowsAffected()
		return map[string]any{"affected": affected}, nil
	}
	rows, err := db.Query(sqlText)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	cols, err := rows.Columns()
	if err != nil {
		return nil, err
	}
	out := []any{}
	for rows.Next() {
		vals := make([]any, len(cols))
		ptrs := make([]any, len(cols))
		for i := range vals {
			ptrs[i] = &vals[i]
		}
		if err := rows.Scan(ptrs...); err != nil {
			return nil, err
		}
		for i := range vals {
			vals[i] = anyToJSON(vals[i])
		}
		out = append(out, vals)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return map[string]any{"columns": cols, "rows": out}, nil
}

// --- Redis RESP 客户端 ---

type redisConn struct {
	conn net.Conn
	r    *bufio.Reader
}

func redisDial(conn map[string]any, dbIndex int) (*redisConn, error) {
	addr := net.JoinHostPort(dbConnStr(conn, "host", "127.0.0.1"), itoa(dbConnPort(conn, 6379)))
	nc, err := net.DialTimeout("tcp", addr, 5*time.Second)
	if err != nil {
		return nil, err
	}
	_ = nc.SetDeadline(time.Now().Add(10 * time.Second))
	rc := &redisConn{conn: nc, r: bufio.NewReader(nc)}
	if pw := str(conn["password"]); pw != "" {
		if _, err := rc.command("AUTH", pw); err != nil {
			nc.Close()
			return nil, err
		}
	}
	if dbIndex > 0 {
		if _, err := rc.command("SELECT", itoa(dbIndex)); err != nil {
			nc.Close()
			return nil, err
		}
	}
	return rc, nil
}

func (c *redisConn) close() { _ = c.conn.Close() }

func (c *redisConn) command(args ...string) (any, error) {
	var b strings.Builder
	fmt.Fprintf(&b, "*%d\r\n", len(args))
	for _, a := range args {
		fmt.Fprintf(&b, "$%d\r\n%s\r\n", len(a), a)
	}
	if _, err := c.conn.Write([]byte(b.String())); err != nil {
		return nil, err
	}
	return c.readReply()
}

func (c *redisConn) readReply() (any, error) {
	line, err := c.r.ReadString('\n')
	if err != nil {
		return nil, err
	}
	line = strings.TrimRight(line, "\r\n")
	if line == "" {
		return nil, fmt.Errorf("empty redis reply")
	}
	switch line[0] {
	case '+':
		return line[1:], nil
	case '-':
		return nil, fmt.Errorf("%s", line[1:])
	case ':':
		n, _ := strconv.ParseInt(line[1:], 10, 64)
		return n, nil
	case '$':
		n, _ := strconv.Atoi(line[1:])
		if n < 0 {
			return nil, nil
		}
		buf := make([]byte, n+2)
		if _, err := ioReadFull(c.r, buf); err != nil {
			return nil, err
		}
		return string(buf[:n]), nil
	case '*':
		n, _ := strconv.Atoi(line[1:])
		if n < 0 {
			return nil, nil
		}
		out := make([]any, 0, n)
		for i := 0; i < n; i++ {
			v, err := c.readReply()
			if err != nil {
				return nil, err
			}
			out = append(out, v)
		}
		return out, nil
	}
	return line, nil
}

func ioReadFull(r *bufio.Reader, buf []byte) (int, error) {
	total := 0
	for total < len(buf) {
		n, err := r.Read(buf[total:])
		total += n
		if err != nil {
			return total, err
		}
	}
	return total, nil
}

func redisInfoMap(text string) map[string]any {
	info := map[string]any{}
	keyspace := map[string]any{}
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") || !strings.Contains(line, ":") {
			continue
		}
		kv := strings.SplitN(line, ":", 2)
		if strings.HasPrefix(kv[0], "db") {
			entry := map[string]any{}
			for _, pair := range strings.Split(kv[1], ",") {
				if p := strings.SplitN(pair, "=", 2); len(p) == 2 {
					if n, err := strconv.Atoi(p[1]); err == nil {
						entry[p[0]] = n
					} else {
						entry[p[0]] = p[1]
					}
				}
			}
			keyspace[kv[0]] = entry
		} else {
			info[kv[0]] = kv[1]
		}
	}
	if len(keyspace) > 0 {
		info["keyspace"] = keyspace
	}
	return info
}

// --- Mongo CLI ---

func mongoArgs(conn map[string]any) []string {
	args := []string{"--quiet", "--host", dbConnStr(conn, "host", "127.0.0.1"),
		"--port", itoa(dbConnPort(conn, 27017))}
	if user := strings.TrimSpace(str(conn["username"])); user != "" {
		args = append(args, "-u", user)
		if pw := str(conn["password"]); pw != "" {
			args = append(args, "-p", pw)
		}
		if authDB := strings.TrimSpace(str(conn["database"])); authDB != "" {
			args = append(args, "--authenticationDatabase", authDB)
		}
	}
	return args
}

func mongoEval(conn map[string]any, script string) (string, error) {
	bin := mongoCLI()
	if bin == "" {
		return "", NewHTTPError(503, "mongosh/mongo CLI 不可用")
	}
	args := append(mongoArgs(conn), "--eval", script)
	res := HostCmd(20*time.Second, bin, args...)
	if res.Code != 0 {
		return "", fmt.Errorf("%s", strings.TrimSpace(res.Stderr+res.Stdout))
	}
	return strings.TrimSpace(res.Stdout), nil
}

func jsQuote(s string) string {
	return "'" + strings.NewReplacer("\\", "\\\\", "'", "\\'").Replace(s) + "'"
}

func registerDBSRoutes(a *App) {
	a.Get("/api/databases/status", perm("database"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{
			"mysql_detected":      detectProcess("mysqld") || detectProcess("mariadbd"),
			"redis_detected":      detectProcess("redis-server"),
			"postgresql_detected": detectProcess("postgres"),
			"mongodb_detected":    detectProcess("mongod"),
			"mysql_libs":          true, "redis_libs": true, "postgresql_libs": true,
			"mongodb_libs": mongoCLI() != "", "sqlite_libs": true,
		})
	})
	a.Get("/api/databases/connections", perm("database"), func(w http.ResponseWriter, r *http.Request) error {
		conns := loadDBConnections()
		for _, c := range conns {
			c["has_password"] = str(c["password"]) != ""
			c["password"] = ""
		}
		return WriteJSON(w, 200, map[string]any{"connections": conns})
	})
	a.Post("/api/databases/connections", perm("database"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Name     string `json:"name"`
			DBType   string `json:"db_type"`
			Host     string `json:"host"`
			Port     int    `json:"port"`
			Username string `json:"username"`
			Password string `json:"password"`
			Database string `json:"database"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if !validDBType(req.DBType) || strings.TrimSpace(req.Name) == "" {
			return NewHTTPError(400, "连接名称或数据库类型非法")
		}
		if req.Host == "" {
			req.Host = "127.0.0.1"
		}
		if req.Port == 0 {
			req.Port = defaultDBPort(req.DBType)
		}
		conn := map[string]any{
			"id": randomHex(8), "name": req.Name, "db_type": req.DBType,
			"host": req.Host, "port": req.Port, "username": req.Username,
			"password": req.Password, "database": req.Database,
			"created_at": time.Now().Format("2006-01-02T15:04:05"),
		}
		if req.DBType == "sqlite" {
			if _, err := sqliteFilePath(conn); err != nil {
				return err
			}
		}
		conns := append(loadDBConnections(), conn)
		if err := saveDBConnections(conns); err != nil {
			return err
		}
		resp := map[string]any{}
		for k, v := range conn {
			resp[k] = v
		}
		resp["has_password"] = req.Password != ""
		resp["password"] = ""
		return WriteJSON(w, 200, resp)
	})
	a.Put("/api/databases/connections/{conn_id}", perm("database"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "conn_id")
		var req struct {
			Name     string `json:"name"`
			DBType   string `json:"db_type"`
			Host     string `json:"host"`
			Port     int    `json:"port"`
			Username string `json:"username"`
			Password string `json:"password"`
			Database string `json:"database"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		conns := loadDBConnections()
		var conn map[string]any
		for _, c := range conns {
			if str(c["id"]) == id {
				conn = c
				break
			}
		}
		if conn == nil {
			return NewHTTPError(404, "Connection not found")
		}
		if !validDBType(req.DBType) {
			return NewHTTPError(400, "数据库类型非法")
		}
		conn["name"] = req.Name
		conn["db_type"] = req.DBType
		conn["host"] = defaultString(req.Host, "127.0.0.1")
		if req.Port == 0 {
			req.Port = defaultDBPort(req.DBType)
		}
		conn["port"] = req.Port
		conn["username"] = req.Username
		if strings.TrimSpace(req.Password) != "" {
			conn["password"] = req.Password
		}
		conn["database"] = req.Database
		conn["updated_at"] = time.Now().Format("2006-01-02T15:04:05")
		if req.DBType == "sqlite" {
			if _, err := sqliteFilePath(conn); err != nil {
				return err
			}
		}
		if err := saveDBConnections(conns); err != nil {
			return err
		}
		resp := map[string]any{}
		for k, v := range conn {
			resp[k] = v
		}
		resp["has_password"] = str(conn["password"]) != ""
		resp["password"] = ""
		return WriteJSON(w, 200, resp)
	})
	a.Delete("/api/databases/connections/{conn_id}", perm("database"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "conn_id")
		conns := loadDBConnections()
		kept := []map[string]any{}
		for _, c := range conns {
			if str(c["id"]) != id {
				kept = append(kept, c)
			}
		}
		if err := saveDBConnections(kept); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/databases/connections/{conn_id}/test", perm("database"), func(w http.ResponseWriter, r *http.Request) error {
		conn, err := findDBConnection(PathParam(r, "conn_id"))
		if err != nil {
			return err
		}
		switch str(conn["db_type"]) {
		case "mysql", "postgresql", "sqlite":
			db, err := openSQL(conn, false)
			if err != nil {
				return NewHTTPError(502, err.Error())
			}
			defer db.Close()
			if err := db.Ping(); err != nil {
				return NewHTTPError(502, err.Error())
			}
			return WriteJSON(w, 200, map[string]any{"ok": true})
		case "redis":
			rc, err := redisDial(conn, 0)
			if err != nil {
				return NewHTTPError(502, err.Error())
			}
			defer rc.close()
			if _, err := rc.command("PING"); err != nil {
				return NewHTTPError(502, err.Error())
			}
			return WriteJSON(w, 200, map[string]any{"ok": true})
		case "mongodb":
			out, err := mongoEval(conn, "EJSON.stringify(db.adminCommand({ping:1}))")
			if err != nil {
				return NewHTTPError(502, err.Error())
			}
			_ = out
			return WriteJSON(w, 200, map[string]any{"ok": true})
		}
		return NewHTTPError(400, "Unsupported db_type")
	})
	a.Get("/api/databases/connections/{conn_id}/databases", perm("database"), func(w http.ResponseWriter, r *http.Request) error {
		conn, err := findDBConnection(PathParam(r, "conn_id"))
		if err != nil {
			return err
		}
		switch str(conn["db_type"]) {
		case "mysql":
			db, err := openSQL(conn, false)
			if err != nil {
				return NewHTTPError(502, err.Error())
			}
			defer db.Close()
			rows, err := db.Query("SHOW DATABASES")
			if err != nil {
				return NewHTTPError(502, err.Error())
			}
			names := []string{}
			for rows.Next() {
				var n string
				if rows.Scan(&n) == nil {
					names = append(names, n)
				}
			}
			rows.Close()
			proc := int64(0)
			if pr, err := db.Query("SELECT COUNT(*) FROM information_schema.processlist"); err == nil {
				if pr.Next() {
					_ = pr.Scan(&proc)
				}
				pr.Close()
			}
			return WriteJSON(w, 200, map[string]any{"databases": names, "process_count": proc})
		case "postgresql":
			db, err := openSQL(conn, false)
			if err != nil {
				return NewHTTPError(502, err.Error())
			}
			defer db.Close()
			rows, err := db.Query("SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname")
			if err != nil {
				return NewHTTPError(502, err.Error())
			}
			names := []string{}
			for rows.Next() {
				var n string
				if rows.Scan(&n) == nil {
					names = append(names, n)
				}
			}
			rows.Close()
			version := ""
			_ = db.QueryRow("SHOW server_version").Scan(&version)
			return WriteJSON(w, 200, map[string]any{"databases": names, "version": version})
		case "redis":
			rc, err := redisDial(conn, 0)
			if err != nil {
				return NewHTTPError(502, err.Error())
			}
			defer rc.close()
			raw, err := rc.command("INFO")
			if err != nil {
				return NewHTTPError(502, err.Error())
			}
			info := redisInfoMap(str(raw))
			keyspaces := []map[string]any{}
			if ks, ok := info["keyspace"].(map[string]any); ok {
				for db, v := range ks {
					entry := map[string]any{"db": db, "keys": 0}
					if m, ok := v.(map[string]any); ok {
						entry["keys"] = m["keys"]
					}
					keyspaces = append(keyspaces, entry)
				}
			}
			return WriteJSON(w, 200, map[string]any{"info": info, "keyspaces": keyspaces})
		case "sqlite":
			path, err := sqliteFilePath(conn)
			if err != nil {
				return err
			}
			if !Exists(path) {
				return WriteJSON(w, 200, map[string]any{"tables": []string{}, "file": path, "exists": false, "size": 0})
			}
			db, err := openSQL(conn, false)
			if err != nil {
				return NewHTTPError(502, err.Error())
			}
			defer db.Close()
			rows, err := db.Query("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
			if err != nil {
				return NewHTTPError(502, err.Error())
			}
			tables := []string{}
			for rows.Next() {
				var n string
				if rows.Scan(&n) == nil {
					tables = append(tables, n)
				}
			}
			rows.Close()
			st, _ := os.Stat(path)
			size := int64(0)
			if st != nil {
				size = st.Size()
			}
			return WriteJSON(w, 200, map[string]any{"tables": tables, "file": path, "exists": true, "size": size})
		case "mongodb":
			out, err := mongoEval(conn, "EJSON.stringify(db.adminCommand({listDatabases:1}).databases.map(d=>d.name))")
			if err != nil {
				return NewHTTPError(502, err.Error())
			}
			var names []string
			if idx := strings.Index(out, "["); idx >= 0 {
				_ = json.Unmarshal([]byte(out[idx:]), &names)
			}
			version := ""
			if vout, err := mongoEval(conn, "db.version()"); err == nil {
				version = strings.Trim(vout, "\"\n")
			}
			return WriteJSON(w, 200, map[string]any{"databases": names, "version": version})
		}
		return NewHTTPError(400, "Unsupported db_type")
	})
	a.Post("/api/databases/connections/{conn_id}/query", perm("database"), func(w http.ResponseWriter, r *http.Request) error {
		conn, err := findDBConnection(PathParam(r, "conn_id"))
		if err != nil {
			return err
		}
		var req struct {
			SQL        string `json:"sql"`
			Command    string `json:"command"`
			DBIndex    int    `json:"db_index"`
			Collection string `json:"collection"`
			Filter     string `json:"filter"`
			Limit      int    `json:"limit"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		switch str(conn["db_type"]) {
		case "mysql", "postgresql", "sqlite":
			sqlText := strings.TrimSpace(req.SQL)
			if sqlText == "" {
				return NewHTTPError(400, "Empty SQL")
			}
			if rejectDangerousSQL(sqlText) {
				return NewHTTPError(403, "该 SQL 包含危险语句，已被安全策略拒绝")
			}
			db, err := openSQL(conn, true)
			if err != nil {
				return NewHTTPError(502, err.Error())
			}
			defer db.Close()
			res, err := runSQLQuery(db, sqlText)
			if err != nil {
				return NewHTTPError(502, err.Error())
			}
			return WriteJSON(w, 200, res)
		case "redis":
			cmd := strings.TrimSpace(req.Command)
			if cmd == "" {
				return NewHTTPError(400, "Empty command")
			}
			parts := strings.Fields(cmd)
			method := strings.ToLower(parts[0])
			forbiddenPrefix := []string{"debug_", "config_", "function_", "module_", "acl_", "client_", "rdb_"}
			if forbiddenRedisCommands[method] {
				return NewHTTPError(403, "Forbidden redis command: "+method)
			}
			for _, p := range forbiddenPrefix {
				if strings.HasPrefix(method, p) {
					return NewHTTPError(403, "Forbidden redis command: "+method)
				}
			}
			rc, err := redisDial(conn, req.DBIndex)
			if err != nil {
				return NewHTTPError(502, err.Error())
			}
			defer rc.close()
			result, err := rc.command(parts...)
			if err != nil {
				return NewHTTPError(502, err.Error())
			}
			return WriteJSON(w, 200, map[string]any{"result": result})
		case "mongodb":
			collection := strings.TrimSpace(req.Collection)
			if collection == "" {
				return NewHTTPError(400, "Collection name required")
			}
			filter := strings.TrimSpace(req.Filter)
			if filter == "" {
				filter = "{}"
			}
			var probe map[string]any
			if err := json.Unmarshal([]byte(filter), &probe); err != nil {
				return NewHTTPError(400, "Invalid filter JSON: "+err.Error())
			}
			limit := req.Limit
			if limit <= 0 {
				limit = 100
			}
			if limit > 500 {
				limit = 500
			}
			dbName := dbConnStr(conn, "database", "test")
			script := fmt.Sprintf("EJSON.stringify(db.getSiblingDB(%s).getCollection(%s).find(%s).limit(%d).toArray())",
				jsQuote(dbName), jsQuote(collection), filter, limit)
			out, err := mongoEval(conn, script)
			if err != nil {
				return NewHTTPError(502, err.Error())
			}
			var docs []any
			if idx := strings.Index(out, "["); idx >= 0 {
				_ = json.Unmarshal([]byte(out[idx:]), &docs)
			}
			return WriteJSON(w, 200, map[string]any{"result": docs})
		}
		return NewHTTPError(400, "Unsupported db_type")
	})
	a.Post("/api/databases/connections/{conn_id}/create-db", perm("database"), func(w http.ResponseWriter, r *http.Request) error {
		conn, err := findDBConnection(PathParam(r, "conn_id"))
		if err != nil {
			return err
		}
		var body struct {
			Name string `json:"name"`
		}
		if err := DecodeJSON(r, &body); err != nil {
			return err
		}
		name := strings.TrimSpace(body.Name)
		if name == "" {
			return NewHTTPError(400, "Database name required")
		}
		if !dbNameRe.MatchString(name) {
			return NewHTTPError(400, "数据库名仅允许字母、数字、下划线和中划线（≤64 字符）")
		}
		switch str(conn["db_type"]) {
		case "mysql":
			db, err := openSQL(conn, false)
			if err != nil {
				return NewHTTPError(502, err.Error())
			}
			defer db.Close()
			_, err = db.Exec("CREATE DATABASE IF NOT EXISTS `" + name + "` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
			if err != nil {
				return NewHTTPError(502, err.Error())
			}
			return WriteJSON(w, 200, map[string]any{"ok": true})
		case "postgresql":
			db, err := openSQL(conn, false)
			if err != nil {
				return NewHTTPError(502, err.Error())
			}
			defer db.Close()
			if _, err := db.Exec(`CREATE DATABASE "` + name + `"`); err != nil {
				return NewHTTPError(502, err.Error())
			}
			return WriteJSON(w, 200, map[string]any{"ok": true})
		case "mongodb":
			return WriteJSON(w, 200, map[string]any{"ok": true, "note": "MongoDB 数据库将在首次写入时自动创建"})
		case "sqlite":
			return NewHTTPError(400, "SQLite 为单文件库，请在查询页执行 CREATE TABLE 建表")
		}
		return NewHTTPError(400, "Only MySQL/PostgreSQL support create-db")
	})
	a.Post("/api/databases/connections/{conn_id}/delete-db", perm("database"), func(w http.ResponseWriter, r *http.Request) error {
		conn, err := findDBConnection(PathParam(r, "conn_id"))
		if err != nil {
			return err
		}
		var body struct {
			Name string `json:"name"`
		}
		if err := DecodeJSON(r, &body); err != nil {
			return err
		}
		name := strings.TrimSpace(body.Name)
		if name == "" || !dbNameRe.MatchString(name) {
			return NewHTTPError(400, "数据库名非法")
		}
		switch str(conn["db_type"]) {
		case "mysql":
			db, err := openSQL(conn, false)
			if err != nil {
				return NewHTTPError(502, err.Error())
			}
			defer db.Close()
			if _, err := db.Exec("DROP DATABASE `" + name + "`"); err != nil {
				return NewHTTPError(502, err.Error())
			}
			return WriteJSON(w, 200, map[string]any{"ok": true})
		case "postgresql":
			db, err := openSQL(conn, false)
			if err != nil {
				return NewHTTPError(502, err.Error())
			}
			defer db.Close()
			if _, err := db.Exec(`DROP DATABASE "` + name + `"`); err != nil {
				return NewHTTPError(502, err.Error())
			}
			return WriteJSON(w, 200, map[string]any{"ok": true})
		case "mongodb":
			if _, err := mongoEval(conn, "db.getSiblingDB("+jsQuote(name)+").dropDatabase()"); err != nil {
				return NewHTTPError(502, err.Error())
			}
			return WriteJSON(w, 200, map[string]any{"ok": true})
		case "sqlite":
			return NewHTTPError(400, "SQLite 为单文件库，请直接管理数据库文件（本接口不删除文件）")
		}
		return NewHTTPError(400, "Unsupported db_type")
	})
}

func validDBType(t string) bool {
	switch t {
	case "mysql", "redis", "postgresql", "mongodb", "sqlite":
		return true
	}
	return false
}

func defaultDBPort(t string) int {
	switch t {
	case "mysql":
		return 3306
	case "redis":
		return 6379
	case "postgresql":
		return 5432
	case "mongodb":
		return 27017
	}
	return 0
}
