package graw

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"regexp"
	"strings"
	"sync"
	"time"

	"golang.org/x/crypto/ssh"
)

// SSH 本地端口转发 / 批量操作（SSH 密钥见 handlers_sshkeys.go）。

func runShellOnNode(nodeID, command string, timeout time.Duration) (string, string, int, error) {
	if !remoteCmdRe.MatchString(command) {
		return "", "非法的命令内容", 126, fmt.Errorf("非法的命令内容")
	}
	store := loadNodeStore()
	node := store.Nodes[nodeID]
	if node == nil {
		return "", "节点不存在", 1, fmt.Errorf("节点不存在")
	}
	if str(node["type"]) != "ssh" {
		res := HostShell(timeout, command)
		return res.Stdout, res.Stderr, res.Code, res.Err
	}
	client, err := pooledSSH(node, 10*time.Second)
	if err != nil {
		return "", err.Error(), 1, err
	}
	session, err := client.NewSession()
	if err != nil {
		return "", err.Error(), 1, err
	}
	defer session.Close()
	var stdout, stderr strings.Builder
	session.Stdout = &stdout
	session.Stderr = &stderr
	done := make(chan error, 1)
	go func() { done <- session.Run(command) }()
	select {
	case err := <-done:
		code := 0
		if err != nil {
			code = 1
		}
		return stdout.String(), stderr.String(), code, err
	case <-time.After(timeout):
		_ = session.Signal(ssh.SIGKILL)
		return stdout.String(), stderr.String(), 124, fmt.Errorf("超时")
	}
}

// --- SSH 端口转发 ---

type pfTunnel struct {
	Item      map[string]any
	Listener  net.Listener
	Client    *ssh.Client
	Conns     int64
	Bytes     int64
	StartedAt time.Time
	mu        sync.Mutex
}

var (
	pfMu      sync.Mutex
	pfRunning = map[string]*pfTunnel{}
)

func loadPortForwardItems() []map[string]any {
	var data map[string]any
	_ = ReadJSON(DataPath("portforwards.json"), &data)
	items := []map[string]any{}
	if arr, ok := data["items"].([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok {
				items = append(items, m)
			}
		}
	}
	return items
}

func savePortForwardItems(items []map[string]any) error {
	return WriteJSONFile(DataPath("portforwards.json"), map[string]any{"items": items})
}

var pfIDRe = regexp.MustCompile(`^[A-Za-z0-9_-]{1,32}$`)

func startPortForward(item map[string]any) error {
	id := str(item["id"])
	if !pfIDRe.MatchString(id) {
		return NewHTTPError(400, "非法的隧道 ID")
	}
	nodeID := str(item["node_id"])
	node := loadNodeStore().Nodes[nodeID]
	if node == nil || str(node["type"]) != "ssh" {
		return NewHTTPError(400, "端口转发的节点必须是 SSH 节点")
	}
	localPort := int(int64Of(item["local_port"]))
	if localPort < 1024 || localPort > 65535 {
		return NewHTTPError(400, "本地端口需在 1024-65535 之间")
	}
	remoteHost := str(item["remote_host"])
	remotePort := int(int64Of(item["remote_port"]))
	if remotePort < 1 || remotePort > 65535 || strings.ContainsAny(remoteHost, " \t\r\n;'\"") {
		return NewHTTPError(400, "远程目标非法")
	}
	client, err := pooledSSH(node, 10*time.Second)
	if err != nil {
		return NewHTTPError(400, "SSH 连接失败: "+err.Error())
	}
	ln, err := net.Listen("tcp", net.JoinHostPort("127.0.0.1", itoa(localPort)))
	if err != nil {
		return NewHTTPError(400, "本地端口监听失败: "+err.Error())
	}
	t := &pfTunnel{Item: item, Listener: ln, Client: client, StartedAt: time.Now()}
	pfMu.Lock()
	if old := pfRunning[id]; old != nil {
		_ = old.Listener.Close()
	}
	pfRunning[id] = t
	pfMu.Unlock()
	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return
			}
			go func() {
				remote, err := client.Dial("tcp", net.JoinHostPort(remoteHost, itoa(remotePort)))
				if err != nil {
					_ = conn.Close()
					return
				}
				t.mu.Lock()
				t.Conns++
				t.mu.Unlock()
				defer func() {
					_ = conn.Close()
					_ = remote.Close()
				}()
				done := make(chan struct{}, 2)
				go func() {
					n, _ := io.Copy(remote, conn)
					t.mu.Lock()
					t.Bytes += n
					t.mu.Unlock()
					done <- struct{}{}
				}()
				go func() {
					n, _ := io.Copy(conn, remote)
					t.mu.Lock()
					t.Bytes += n
					t.mu.Unlock()
					done <- struct{}{}
				}()
				<-done
			}()
		}
	}()
	return nil
}

func stopPortForward(id string) bool {
	pfMu.Lock()
	t := pfRunning[id]
	delete(pfRunning, id)
	pfMu.Unlock()
	if t == nil {
		return false
	}
	_ = t.Listener.Close()
	return true
}

func portForwardPublic(t *pfTunnel) map[string]any {
	t.mu.Lock()
	defer t.mu.Unlock()
	return map[string]any{
		"id": str(t.Item["id"]), "name": str(t.Item["name"]), "node_id": str(t.Item["node_id"]),
		"local_port": int(int64Of(t.Item["local_port"])), "remote_host": str(t.Item["remote_host"]),
		"remote_port": int(int64Of(t.Item["remote_port"])),
		"connections": t.Conns, "bytes": t.Bytes,
		"started_at": t.StartedAt.Format("2006-01-02 15:04:05"),
	}
}

func registerPortForwardRoutes(a *App) {
	a.Get("/api/portforward", perm("portforward"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"items": loadPortForwardItems()})
	})
	a.Get("/api/portforward/running", perm("portforward"), func(w http.ResponseWriter, r *http.Request) error {
		pfMu.Lock()
		tunnels := []map[string]any{}
		for _, t := range pfRunning {
			tunnels = append(tunnels, portForwardPublic(t))
		}
		pfMu.Unlock()
		return WriteJSON(w, 200, map[string]any{"tunnels": tunnels})
	})
	a.Post("/api/portforward", perm("portforward"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Name       string `json:"name"`
			NodeID     string `json:"node_id"`
			LocalPort  int    `json:"local_port"`
			RemoteHost string `json:"remote_host"`
			RemotePort int    `json:"remote_port"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		item := map[string]any{
			"id": randomHex(10), "name": strings.TrimSpace(req.Name), "node_id": req.NodeID,
			"local_port": req.LocalPort, "remote_host": strings.TrimSpace(req.RemoteHost),
			"remote_port": req.RemotePort, "enabled": true,
		}
		if err := startPortForward(item); err != nil {
			return err
		}
		items := append(loadPortForwardItems(), item)
		if err := savePortForwardItems(items); err != nil {
			return err
		}
		pfMu.Lock()
		pub := portForwardPublic(pfRunning[str(item["id"])])
		pfMu.Unlock()
		return WriteJSON(w, 200, map[string]any{"ok": true, "tunnel": pub})
	})
	a.Post("/api/portforward/{tid}/toggle", perm("portforward"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "tid")
		if !pfIDRe.MatchString(id) {
			return NewHTTPError(400, "非法的隧道 ID")
		}
		items := loadPortForwardItems()
		var item map[string]any
		for _, it := range items {
			if str(it["id"]) == id {
				item = it
			}
		}
		if item == nil {
			return NewHTTPError(404, "转发条目不存在")
		}
		pfMu.Lock()
		running := pfRunning[id] != nil
		pfMu.Unlock()
		if running {
			stopPortForward(id)
			item["enabled"] = false
		} else {
			if err := startPortForward(item); err != nil {
				return err
			}
			item["enabled"] = true
		}
		if err := savePortForwardItems(items); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "enabled": boolOf(item["enabled"])})
	})
	a.Delete("/api/portforward/{tid}", perm("portforward"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "tid")
		if !pfIDRe.MatchString(id) {
			return NewHTTPError(400, "非法的隧道 ID")
		}
		items := loadPortForwardItems()
		kept := []map[string]any{}
		for _, it := range items {
			if str(it["id"]) != id {
				kept = append(kept, it)
			}
		}
		if len(kept) == len(items) {
			return NewHTTPError(404, "转发条目不存在")
		}
		_ = savePortForwardItems(kept)
		stopPortForward(id)
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
}

// RestorePortForwards 启动所有 enabled 的端口转发（面板启动时调用）。
func RestorePortForwards() {
	for _, item := range loadPortForwardItems() {
		if boolOf(item["enabled"]) {
			_ = startPortForward(item)
		}
	}
}

// --- 批量操作 ---

func registerBatchRoutes(a *App) {
	a.Post("/api/batch/command", perm("batch"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			NodeIDs   []string `json:"node_ids"`
			Command   string   `json:"command"`
			Timeout   int      `json:"timeout"`
			MaxOutput int      `json:"max_output"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if len(req.NodeIDs) == 0 || len(req.NodeIDs) > 16 {
			return NewHTTPError(400, "节点数量需在 1-16 之间")
		}
		if req.Command == "" || len(req.Command) > 8000 {
			return NewHTTPError(400, "命令不能为空且不超过 8000 字符")
		}
		if req.Timeout <= 0 || req.Timeout > 300 {
			req.Timeout = 30
		}
		if req.MaxOutput < 1024 {
			req.MaxOutput = 20000
		}
		store := loadNodeStore()
		for _, nid := range req.NodeIDs {
			if store.Nodes[nid] == nil {
				return NewHTTPError(400, "节点不存在: "+nid)
			}
		}
		results := []map[string]any{}
		for _, nid := range req.NodeIDs {
			start := time.Now()
			node := store.Nodes[nid]
			stdout, stderr, code, err := runShellOnNode(nid, req.Command, time.Duration(req.Timeout)*time.Second)
			ok := err == nil && code == 0
			results = append(results, map[string]any{
				"node_id": nid, "node_name": defaultString(str(node["name"]), nid),
				"ok": ok, "returncode": code,
				"stdout": clipText(stdout, req.MaxOutput), "stderr": clipText(stderr, req.MaxOutput),
				"duration": roundN(time.Since(start).Seconds(), 2),
			})
		}
		u := CurrentUser(r)
		AuditRecord("批量命令", u.Username(), ClientIP(r), "nodes="+strings.Join(req.NodeIDs, ",")+" cmd="+truncateStr(req.Command, 200))
		return WriteJSON(w, 200, map[string]any{"results": results, "ok": allOK(results)})
	})
	a.Post("/api/batch/containers", perm("batch"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			NodeIDs []string `json:"node_ids"`
			Action  string   `json:"action"`
			Filter  struct {
				Keyword string `json:"keyword"`
			} `json:"filter"`
			Timeout int `json:"timeout"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if len(req.NodeIDs) == 0 || len(req.NodeIDs) > 16 {
			return NewHTTPError(400, "节点数量需在 1-16 之间")
		}
		if req.Action != "start" && req.Action != "stop" && req.Action != "restart" {
			return NewHTTPError(400, "action 仅允许 start/stop/restart")
		}
		store := loadNodeStore()
		results := []map[string]any{}
		kw := strings.ToLower(strings.TrimSpace(req.Filter.Keyword))
		for _, nid := range req.NodeIDs {
			node := store.Nodes[nid]
			if node == nil {
				results = append(results, map[string]any{"node_id": nid, "ok": false, "error": "节点不存在"})
				continue
			}
			stdout, stderr, code, err := runShellOnNode(nid, "docker ps -a --format '{{json .}}'", 30*time.Second)
			if err != nil || code != 0 {
				results = append(results, map[string]any{
					"node_id": nid, "node_name": defaultString(str(node["name"]), nid),
					"ok": false, "containers": []any{}, "error": strings.TrimSpace(stderr),
				})
				continue
			}
			containers := []map[string]any{}
			for _, line := range strings.Split(stdout, "\n") {
				var c map[string]any
				if json.Unmarshal([]byte(strings.TrimSpace(line)), &c) != nil {
					continue
				}
				id := str(c["ID"])
				name := str(c["Names"])
				if name == "" {
					name = id
				}
				if kw != "" && !strings.Contains(strings.ToLower(name), kw) {
					continue
				}
				if id == "" {
					continue
				}
				_, eout, _, eerr := runShellOnNode(nid, "docker "+req.Action+" "+shellQuote(id), 60*time.Second)
				containers = append(containers, map[string]any{
					"id": id, "name": name, "ok": eerr == nil, "detail": clipText(eout, 2000),
				})
			}
			results = append(results, map[string]any{
				"node_id": nid, "node_name": defaultString(str(node["name"]), nid),
				"ok": allOK(containers), "containers": containers,
			})
		}
		u := CurrentUser(r)
		AuditRecord("批量容器操作", u.Username(), ClientIP(r),
			"nodes="+strings.Join(req.NodeIDs, ",")+" action="+req.Action+" kw="+req.Filter.Keyword)
		return WriteJSON(w, 200, map[string]any{"results": results, "ok": allOK(results)})
	})
}

func clipText(s string, limit int) string {
	if len(s) <= limit {
		return s
	}
	return s[:limit] + "\n…(输出已截断，共 " + itoa(len(s)) + " 字符)"
}

func allOK(items []map[string]any) bool {
	for _, it := range items {
		if !boolOf(it["ok"]) {
			return false
		}
	}
	return true
}
