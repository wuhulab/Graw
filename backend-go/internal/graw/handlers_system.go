package graw

import (
	"encoding/json"
	"net/http"
	"strings"
	"time"
)

func registerSystemRoutes(a *App) {
	a.Get("/api/system/overview", protected(), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, metricsHub.overview())
	})
	a.Get("/api/system/network", protected(), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, metricsHub.network())
	})
	a.Get("/api/system/diskio", protected(), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, metricsHub.diskio())
	})
	a.Get("/api/system/info", protected(), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, metricsHub.info())
	})
	a.Get("/api/system/install-check", protected(), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, InstallCheck())
	})
	a.Get("/api/system/metrics/status", protected(), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, MetricsStatus())
	})
	a.Get("/api/system/metrics/history", protected(), func(w http.ResponseWriter, r *http.Request) error {
		now := float64(time.Now().Unix())
		start := QueryFloat(r, "start", 0)
		end := QueryFloat(r, "end", 0)
		if start == 0 {
			start = now - 3600
		}
		if end == 0 {
			end = now
		}
		if start < 0 || end < 0 || end-start > 365*86400 {
			return NewHTTPError(400, "时间范围非法")
		}
		bucket := QueryInt(r, "bucket", 0)
		if bucket < 0 || bucket > 86400 {
			return NewHTTPError(422, "bucket 超出范围")
		}
		return WriteJSON(w, 200, MetricsHistory(start, end, bucket))
	})
	a.Delete("/api/system/metrics/clear", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		ClearMetrics()
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.wsRoute("/api/system/ws", systemWSHandler)
}

// serveNodeMetricsWS 为裸 SSH 跨节点订阅独立采集指标并推送。
func serveNodeMetricsWS(conn *wsConn, nodeID, token string) {
	defer conn.Close()
	done := make(chan struct{})
	go func() {
		for {
			if _, _, err := conn.ReadMessage(); err != nil {
				close(done)
				return
			}
		}
	}()
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-done:
			return
		case <-ticker.C:
			if token != "" && !WSStillValid(token) {
				return
			}
		}
		var snapshot map[string]any
		withNodeScope(nodeID, func() { snapshot = metricsHub.CollectAll() })
		data, _ := json.Marshal(map[string]any{"type": "metrics", "data": snapshot})
		if err := conn.WriteText(data); err != nil {
			return
		}
	}
}

func systemWSHandler(w http.ResponseWriter, r *http.Request) {
	u := AuthenticateWS(r)
	if u == nil {
		WriteJSON(w, 401, map[string]any{"detail": "未认证"})
		return
	}
	if IsDefaultPassword(u.PasswordHash()) {
		WriteJSON(w, 403, map[string]any{"detail": "必须修改默认密码后才能使用面板"})
		return
	}
	// 远端 Agent 子节点：桥接子节点自身的 /api/system/ws（原生指标）。
	targetID := strings.TrimSpace(r.URL.Query().Get("node"))
	if targetID != "" && u.Role() != "admin" {
		targetID = ""
	}
	store := loadNodeStore()
	node, ok := store.Nodes[targetID]
	if !ok {
		node = store.Nodes[store.Current]
	}
	if str(node["type"]) == "ssh" && agentReady(node) {
		bridgeAgentWS(w, r, node, "/api/system/ws")
		return
	}
	if str(node["type"]) == "ssh" && targetID != "" && targetID != store.Current {
		// 裸 SSH 跨节点订阅：在该节点上下文内独立采集并推送。
		conn, err := upgradeWS(w, r)
		if err != nil {
			return
		}
		serveNodeMetricsWS(conn, targetID, r.URL.Query().Get("token"))
		return
	}
	conn, err := upgradeWS(w, r)
	if err != nil {
		return
	}
	client := &wsClient{conn: conn}
	metricsHub.AddClient(client)
	defer func() {
		metricsHub.RemoveClient(client)
		_ = conn.Close()
	}()
	token := r.URL.Query().Get("token")
	lastCheck := time.Now()
	for {
		if _, _, err := conn.ReadMessage(); err != nil {
			return
		}
		// 周期性复检会话有效性：改密/踢出后主动断开长连接。
		if time.Since(lastCheck) > 30*time.Second {
			lastCheck = time.Now()
			if !WSStillValid(token) {
				return
			}
		}
	}
}
