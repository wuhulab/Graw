package graw

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"time"
)

// 多机管理增强（P1）：
//   - 跨节点聚合：把各子节点的网站 / 已安装应用 / 证书汇总成一张表；
//   - 节点分组统计与批量设置分组/标签；
//   - 节点在线状态持久化与离线/恢复告警（后台巡检）。
//
// 安全约束：聚合只通过 Agent 隧道调用子节点只读 GET 接口，鉴权沿用 agentToken；
// 结果只保留展示字段，不回传任何凭据；单节点失败只标记该节点，不影响整体。

// agentJSONGet 通过 Agent 隧道读取子节点一个只读 JSON 接口。
func agentJSONGet(node map[string]any, path string) (map[string]any, error) {
	client, err := pooledSSH(node, 10*time.Second)
	if err != nil {
		return nil, err
	}
	token, err := agentToken(node, client)
	if err != nil {
		return nil, err
	}
	port := intOf(node["agent_port"])
	if port == 0 {
		port = defaultAgentPort
	}
	conn, reader, err := openAgentConn(client, port, "GET", path,
		map[string]string{"Authorization": "Bearer " + token, "Accept": "application/json"}, nil, 0)
	if err != nil {
		return nil, err
	}
	defer conn.Close()
	resp, err := http.ReadResponse(reader, &http.Request{Method: "GET"})
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if err != nil {
		return nil, err
	}
	var data map[string]any
	if err := json.Unmarshal(body, &data); err != nil {
		return nil, err
	}
	return data, nil
}

func nodeBaseFields(id string, node map[string]any) map[string]any {
	pub := publicNode(id, node)
	return map[string]any{
		"node_id": id, "node_name": pub["name"], "group": pub["group"],
		"node_type": pub["type"], "host": pub["host"],
	}
}

// aggregateResource 把一个资源类型在各节点上的列表合并。
func aggregateResource(resource string) map[string]any {
	store := loadNodeStore()
	items := []map[string]any{}
	errors := []map[string]any{}
	ids := []string{}
	for id := range store.Nodes {
		ids = append(ids, id)
	}
	sortStrings(ids)
	for _, id := range ids {
		node := store.Nodes[id]
		base := nodeBaseFields(id, node)
		isLocal := str(node["type"]) != "ssh"
		var list []map[string]any
		var err error
		if isLocal {
			switch resource {
			case "sites":
				list = loadSites()
			case "apps":
				list = listInstalledApps()
			case "certs":
				list = sslCertsList(loadSSLCerts())
			default:
				return map[string]any{"items": []any{}, "errors": []map[string]any{{"node_id": id, "error": "不支持的资源类型"}}}
			}
		} else {
			path := ""
			key := ""
			switch resource {
			case "sites":
				path, key = "/api/sites/list", "sites"
			case "apps":
				path, key = "/api/appstore/installed", "apps"
			case "certs":
				path, key = "/api/ssl/list", "certs"
			default:
				return map[string]any{"items": []any{}, "errors": []map[string]any{{"node_id": id, "error": "不支持的资源类型"}}}
			}
			data, aerr := agentJSONGet(node, path)
			err = aerr
			if err == nil {
				list = sliceOfMaps(data[key])
			}
		}
		if err != nil {
			e := map[string]any{"node_id": id, "node_name": base["node_name"], "error": err.Error()}
			errors = append(errors, e)
			continue
		}
		for _, item := range list {
			row := map[string]any{}
			for k, v := range base {
				row[k] = v
			}
			row["resource"] = resource
			for k, v := range item {
				// 只透传展示字段；站点/应用/证书条目本身不含私钥与密码。
				row[k] = v
			}
			items = append(items, row)
		}
	}
	return map[string]any{"items": items, "errors": errors, "generated_at": time.Now().UnixMilli()}
}

func sliceOfMaps(v any) []map[string]any {
	out := []map[string]any{}
	if arr, ok := v.([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok {
				out = append(out, m)
			}
		}
	}
	return out
}

// --- 节点在线状态与告警 ---

func nodeStatusPath() string {
	return DataPath("node_status.json")
}

func loadNodeStatus() map[string]map[string]any {
	data := map[string]map[string]any{}
	_ = ReadJSON(nodeStatusPath(), &data)
	return data
}

func saveNodeStatus(data map[string]map[string]any) {
	_ = WriteJSONFile(nodeStatusPath(), data)
}

// checkNodeStatusOnce 探测所有 SSH 节点并把状态变化推送到通知渠道。
func checkNodeStatusOnce() {
	store := loadNodeStore()
	prev := loadNodeStatus()
	next := map[string]map[string]any{}
	now := time.Now().Format("2006-01-02 15:04:05")
	for id, node := range store.Nodes {
		if id == localNodeID || str(node["type"]) != "ssh" {
			next[id] = map[string]any{"online": true, "checked_at": now}
			continue
		}
		client, err := sshConnect(node, 8*time.Second)
		online := err == nil
		errText := ""
		if err != nil {
			errText = err.Error()
		} else {
			client.Close()
		}
		next[id] = map[string]any{"online": online, "checked_at": now, "error": errText}
		old, had := prev[id]
		if !had {
			continue
		}
		wasOnline := boolOf(old["online"])
		name := str(node["name"])
		if wasOnline && !online {
			msg := "【Graw 节点离线】" + name + "（" + str(node["host"]) + "）"
			sent, failed := pushAll(msg)
			appendNotifyLog(msg, sent, failed)
		} else if !wasOnline && online {
			msg := "【Graw 节点恢复】" + name + "（" + str(node["host"]) + "）"
			sent, failed := pushAll(msg)
			appendNotifyLog(msg, sent, failed)
		}
	}
	saveNodeStatus(next)
}

// StartNodeMonitor 启动节点在线巡检（默认 120 秒一次）。
func StartNodeMonitor() {
	go func() {
		ticker := time.NewTicker(120 * time.Second)
		defer ticker.Stop()
		// 启动后稍等片刻再首检，避免与面板启动争抢 SSH。
		time.Sleep(20 * time.Second)
		checkNodeStatusOnce()
		for range ticker.C {
			checkNodeStatusOnce()
		}
	}()
}

func registerNodeEnhancedRoutes(a *App) {
	a.Get("/api/nodes/aggregate", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		resource := strings.TrimSpace(r.URL.Query().Get("resource"))
		if resource == "" {
			resource = "sites"
		}
		if resource != "sites" && resource != "apps" && resource != "certs" {
			return NewHTTPError(400, "resource 只能是 sites/apps/certs")
		}
		return WriteJSON(w, 200, aggregateResource(resource))
	})
	a.Get("/api/nodes/groups", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		store := loadNodeStore()
		counts := map[string]int{}
		for _, node := range store.Nodes {
			g := str(node["group"])
			if g == "" {
				g = "默认"
			}
			counts[g]++
		}
		groups := []map[string]any{}
		for name, count := range counts {
			groups = append(groups, map[string]any{"name": name, "count": count})
		}
		return WriteJSON(w, 200, map[string]any{"groups": groups})
	})
	a.Post("/api/nodes/batch-meta", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			IDs   []string `json:"ids"`
			Group *string  `json:"group"`
			Tags  []string `json:"tags"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		store := loadNodeStoreFresh()
		updated := 0
		for _, id := range req.IDs {
			node, ok := store.Nodes[id]
			if !ok {
				continue
			}
			if req.Group != nil {
				node["group"] = strings.TrimSpace(*req.Group)
			}
			if req.Tags != nil {
				tags := []string{}
				for _, t := range req.Tags {
					if s := strings.TrimSpace(t); s != "" && len(tags) < 12 {
						tags = append(tags, s)
					}
				}
				node["tags"] = tags
			}
			updated++
		}
		if err := saveNodeStore(store); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "updated": updated})
	})
	a.Get("/api/nodes/status", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"status": loadNodeStatus()})
	})
}
