package graw

import (
	"database/sql"
	"net/http"
	"strings"
	"time"
)

// 数据库集群状态（P2，只读）：
//   - MySQL/MariaDB：主从复制（SHOW REPLICA/SLAVE STATUS）、只读标记、Galera、MGR 成员；
//   - PostgreSQL：主备角色、pg_stat_replication / pg_stat_wal_receiver、wal_level。
//
// 只读取状态，不修改复制配置；建集群/主从切换仍建议用专门的编排工具。

func queryStringMaps(db *sql.DB, query string) ([]map[string]any, error) {
	rows, err := db.Query(query)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	cols, err := rows.Columns()
	if err != nil {
		return nil, err
	}
	out := []map[string]any{}
	for rows.Next() {
		vals := make([]any, len(cols))
		ptrs := make([]any, len(cols))
		for i := range vals {
			ptrs[i] = &vals[i]
		}
		if err := rows.Scan(ptrs...); err != nil {
			continue
		}
		item := map[string]any{}
		for i, col := range cols {
			switch v := vals[i].(type) {
			case []byte:
				item[col] = string(v)
			case nil:
				item[col] = ""
			default:
				item[col] = v
			}
		}
		out = append(out, item)
	}
	return out, nil
}

func mysqlClusterStatus(conn map[string]any) (map[string]any, error) {
	db, err := openSQL(conn, false)
	if err != nil {
		return nil, NewHTTPError(502, "连接数据库失败: "+err.Error())
	}
	defer db.Close()
	res := map[string]any{"engine": "mysql", "checked_at": time.Now().Format("2006-01-02 15:04:05")}

	vars, _ := queryStringMaps(db, "SELECT @@server_id AS server_id, @@read_only AS read_only, @@super_read_only AS super_read_only, @@gtid_mode AS gtid_mode")
	if len(vars) > 0 {
		res["variables"] = vars[0]
	}
	replica, rerr := queryStringMaps(db, "SHOW REPLICA STATUS")
	if rerr != nil || len(replica) == 0 {
		replica, _ = queryStringMaps(db, "SHOW SLAVE STATUS")
	}
	role := "primary"
	healthy := true
	if len(replica) > 0 {
		row := replica[0]
		res["replication"] = row
		role = "replica"
		ioRunning := strings.EqualFold(str(row["Replica_IO_Running"]), "Yes") || strings.EqualFold(str(row["Slave_IO_Running"]), "Yes")
		sqlRunning := strings.EqualFold(str(row["Replica_SQL_Running"]), "Yes") || strings.EqualFold(str(row["Slave_SQL_Running"]), "Yes")
		healthy = ioRunning && sqlRunning
		res["io_running"] = ioRunning
		res["sql_running"] = sqlRunning
	}
	// Galera（MariaDB / Percona XtraDB Cluster）
	galera, _ := queryStringMaps(db, "SHOW STATUS LIKE 'wsrep_%'")
	if len(galera) > 0 {
		cluster := map[string]any{}
		for _, row := range galera {
			key := str(row["Variable_name"])
			if key == "" {
				continue
			}
			cluster[key] = row["Value"]
		}
		res["galera"] = cluster
		if size := int64Of(cluster["wsrep_cluster_size"]); size > 0 {
			if role == "primary" {
				role = "galera"
			}
			healthy = healthy && strings.EqualFold(str(cluster["wsrep_ready"]), "ON")
		}
	}
	// MySQL Group Replication
	if members, merr := queryStringMaps(db, "SELECT * FROM performance_schema.replication_group_members"); merr == nil && len(members) > 0 {
		res["group_replication"] = members
		if role == "primary" {
			role = "group-replication"
		}
	}
	res["role"] = role
	res["healthy"] = healthy
	return res, nil
}

func pgClusterStatus(conn map[string]any) (map[string]any, error) {
	db, err := openSQL(conn, true)
	if err != nil {
		return nil, NewHTTPError(502, "连接数据库失败: "+err.Error())
	}
	defer db.Close()
	res := map[string]any{"engine": "postgresql", "checked_at": time.Now().Format("2006-01-02 15:04:05")}

	recovery, _ := queryStringMaps(db, "SELECT pg_is_in_recovery() AS in_recovery")
	inRecovery := false
	if len(recovery) > 0 {
		v := str(recovery[0]["in_recovery"])
		inRecovery = strings.EqualFold(v, "t") || strings.EqualFold(v, "true")
		res["in_recovery"] = inRecovery
	}
	settings, _ := queryStringMaps(db, "SELECT current_setting('wal_level') AS wal_level, current_setting('max_wal_senders') AS max_wal_senders")
	if len(settings) > 0 {
		res["settings"] = settings[0]
	}
	if inRecovery {
		res["role"] = "standby"
		recv, _ := queryStringMaps(db, "SELECT status, sender_host, sender_port, slot_name FROM pg_stat_wal_receiver")
		if len(recv) > 0 {
			res["wal_receiver"] = recv[0]
			res["healthy"] = strings.EqualFold(str(recv[0]["status"]), "streaming")
		} else {
			res["healthy"] = false
		}
	} else {
		res["role"] = "primary"
		replicas, _ := queryStringMaps(db, "SELECT client_addr, state, sync_state, sent_lsn, replay_lsn FROM pg_stat_replication")
		res["replicas"] = replicas
		healthy := true
		for _, row := range replicas {
			if !strings.EqualFold(str(row["state"]), "streaming") {
				healthy = false
			}
		}
		res["healthy"] = healthy
	}
	return res, nil
}

func registerDBClusterRoutes(a *App) {
	a.Get("/api/databases/connections/{conn_id}/cluster", perm("database"), func(w http.ResponseWriter, r *http.Request) error {
		id := strings.TrimSpace(PathParam(r, "conn_id"))
		var conn map[string]any
		for _, item := range loadDBConnections() {
			if str(item["id"]) == id {
				conn = item
				break
			}
		}
		if conn == nil {
			return NewHTTPError(404, "数据库连接不存在")
		}
		switch strings.ToLower(str(conn["db_type"])) {
		case "mysql", "mariadb":
			res, err := mysqlClusterStatus(conn)
			if err != nil {
				return err
			}
			return WriteJSON(w, 200, res)
		case "postgresql", "postgres":
			res, err := pgClusterStatus(conn)
			if err != nil {
				return err
			}
			return WriteJSON(w, 200, res)
		default:
			return NewHTTPError(400, "该数据库类型暂不支持集群状态（仅 MySQL/MariaDB/PostgreSQL）")
		}
	})
}
