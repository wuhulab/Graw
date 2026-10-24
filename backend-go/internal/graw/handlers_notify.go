package graw

import (
	"bytes"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/smtp"
	"net/url"
	"strings"
	"time"
)

// 通知中心 / 站点可用性 / 证书到期 / 服务与端口监控。

func loadNotify() map[string]any {
	data := map[string]any{"channels": []any{}, "rules": []any{}, "config": map[string]any{}}
	_ = ReadJSON(DataPath("notify.json"), &data)
	if data == nil {
		return map[string]any{"channels": []any{}, "rules": []any{}, "config": map[string]any{}}
	}
	return data
}

func notifyList(data map[string]any, key string) []map[string]any {
	out := []map[string]any{}
	if arr, ok := data[key].([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok {
				out = append(out, m)
			}
		}
	}
	return out
}

func maskChannel(ch map[string]any) map[string]any {
	out := map[string]any{
		"id": str(ch["id"]), "name": str(ch["name"]), "type": str(ch["type"]),
		"enabled": boolOf(ch["enabled"]), "config": map[string]any{},
	}
	cfg := mapOf(ch["config"])
	masked := map[string]any{}
	for k, v := range cfg {
		low := strings.ToLower(k)
		if strings.Contains(low, "secret") || strings.Contains(low, "token") || strings.Contains(low, "password") || strings.Contains(low, "key") {
			if str(v) != "" {
				masked[k] = "******"
			} else {
				masked[k] = ""
			}
		} else {
			masked[k] = v
		}
	}
	out["config"] = masked
	return out
}

func notifyChannels() []map[string]any {
	return notifyList(loadNotify(), "channels")
}

func sendChannel(ch map[string]any, message string) error {
	cfg := mapOf(ch["config"])
	typ := str(ch["type"])
	client := &http.Client{Timeout: 10 * time.Second}
	switch typ {
	case "webhook":
		target := str(cfg["url"])
		if target == "" {
			return fmt.Errorf("webhook url 为空")
		}
		body, _ := json.Marshal(map[string]any{"text": message, "message": message, "content": message})
		req, _ := http.NewRequest(http.MethodPost, target, bytes.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		resp, err := client.Do(req)
		if err != nil {
			return err
		}
		resp.Body.Close()
		if resp.StatusCode >= 300 {
			return fmt.Errorf("webhook 返回 %d", resp.StatusCode)
		}
	case "telegram":
		token := str(cfg["bot_token"])
		chatID := str(cfg["chat_id"])
		if token == "" || chatID == "" {
			return fmt.Errorf("telegram 配置不完整")
		}
		form := url.Values{}
		form.Set("chat_id", chatID)
		form.Set("text", message)
		resp, err := client.PostForm("https://api.telegram.org/bot"+token+"/sendMessage", form)
		if err != nil {
			return err
		}
		resp.Body.Close()
		if resp.StatusCode >= 300 {
			return fmt.Errorf("telegram 返回 %d", resp.StatusCode)
		}
	case "dingtalk":
		webhook := str(cfg["webhook"])
		if webhook == "" {
			return fmt.Errorf("钉钉 webhook 为空")
		}
		body, _ := json.Marshal(map[string]any{
			"msgtype": "text", "text": map[string]string{"content": message},
		})
		resp, err := client.Post(webhook, "application/json", bytes.NewReader(body))
		if err != nil {
			return err
		}
		resp.Body.Close()
	case "wecom":
		webhook := str(cfg["webhook"])
		if webhook == "" {
			return fmt.Errorf("企业微信 webhook 为空")
		}
		body, _ := json.Marshal(map[string]any{
			"msgtype": "text", "text": map[string]string{"content": message},
		})
		resp, err := client.Post(webhook, "application/json", bytes.NewReader(body))
		if err != nil {
			return err
		}
		resp.Body.Close()
	case "serverchan":
		sendKey := str(cfg["send_key"])
		if sendKey == "" {
			return fmt.Errorf("Server酱 SendKey 为空")
		}
		form := url.Values{}
		form.Set("title", "Graw 通知")
		form.Set("desp", message)
		resp, err := client.PostForm("https://sctapi.ftqq.com/"+sendKey+".send", form)
		if err != nil {
			return err
		}
		resp.Body.Close()
	case "smtp":
		host := str(cfg["host"])
		port := str(cfg["port"])
		if port == "" {
			port = "465"
		}
		user := str(cfg["username"])
		pass := str(cfg["password"])
		to := str(cfg["to"])
		from := defaultString(str(cfg["from"]), user)
		if host == "" || to == "" {
			return fmt.Errorf("SMTP 配置不完整")
		}
		headers := "From: " + from + "\r\nTo: " + to + "\r\nSubject: Graw 通知\r\nMIME-Version: 1.0\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n"
		msg := []byte(headers + message)
		if port == "465" {
			conn, err := tls.Dial("tcp", host+":"+port, &tls.Config{ServerName: host})
			if err != nil {
				return err
			}
			c, err := smtp.NewClient(conn, host)
			if err != nil {
				conn.Close()
				return err
			}
			defer c.Close()
			if user != "" {
				_ = c.Auth(smtp.PlainAuth("", user, pass, host))
			}
			_ = c.Mail(from)
			_ = c.Rcpt(to)
			w, err := c.Data()
			if err != nil {
				return err
			}
			_, _ = w.Write(msg)
			_ = w.Close()
			_ = c.Quit()
			return nil
		}
		auth := smtp.PlainAuth("", user, pass, host)
		return smtp.SendMail(host+":"+port, auth, from, []string{to}, msg)
	}
	return nil
}

func pushAll(message string) (int, int) {
	sent, failed := 0, 0
	for _, ch := range notifyChannels() {
		if !boolOf(ch["enabled"]) {
			continue
		}
		if err := sendChannel(ch, message); err != nil {
			failed++
		} else {
			sent++
		}
	}
	return sent, failed
}

func appendNotifyLog(message string, sent, failed int) {
	var logs []map[string]any
	_ = ReadJSON(DataPath("notify_logs.json"), &logs)
	logs = append([]map[string]any{{
		"time": time.Now().Format("2006-01-02 15:04:05"), "message": message,
		"sent": sent, "failed": failed,
	}}, logs...)
	if len(logs) > 500 {
		logs = logs[:500]
	}
	_ = WriteJSONFile(DataPath("notify_logs.json"), logs)
}

func init() { PushAllFunc = pushAll }

func registerNotifyModuleRoutes(a *App) {
	a.Get("/api/notify/status", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		data := loadNotify()
		return WriteJSON(w, 200, map[string]any{
			"enabled":       boolOf(mapOf(data["config"])["enabled"]),
			"channel_count": len(notifyList(data, "channels")),
			"rule_count":    len(notifyList(data, "rules")),
		})
	})
	a.Get("/api/notify/channels", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		channels := []map[string]any{}
		for _, ch := range notifyChannels() {
			channels = append(channels, maskChannel(ch))
		}
		return WriteJSON(w, 200, map[string]any{"channels": channels})
	})
	a.Post("/api/notify/channels", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Name    string         `json:"name"`
			Type    string         `json:"type"`
			Enabled *bool          `json:"enabled"`
			Config  map[string]any `json:"config"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if !validChannelType(req.Type) {
			return NewHTTPError(400, "不支持的通知渠道类型")
		}
		enabled := true
		if req.Enabled != nil {
			enabled = *req.Enabled
		}
		ch := map[string]any{
			"id": "chan_" + randomHex(8), "name": req.Name, "type": req.Type,
			"enabled": enabled, "config": req.Config,
		}
		data := loadNotify()
		data["channels"] = append(notifyList(data, "channels"), ch)
		if err := WriteJSONFile(DataPath("notify.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, maskChannel(ch))
	})
	a.Put("/api/notify/channels/{channel_id}", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "channel_id")
		var req struct {
			Name    string         `json:"name"`
			Type    string         `json:"type"`
			Enabled *bool          `json:"enabled"`
			Config  map[string]any `json:"config"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		data := loadNotify()
		channels := notifyList(data, "channels")
		var ch map[string]any
		for _, c := range channels {
			if str(c["id"]) != id {
				continue
			}
			ch = c
			if req.Name != "" {
				c["name"] = req.Name
			}
			if validChannelType(req.Type) {
				c["type"] = req.Type
			}
			if req.Enabled != nil {
				c["enabled"] = *req.Enabled
			}
			old := mapOf(c["config"])
			for k, v := range req.Config {
				if str(v) == "******" {
					continue
				}
				old[k] = v
			}
			c["config"] = old
		}
		if ch == nil {
			return NewHTTPError(404, "通知渠道不存在")
		}
		data["channels"] = channels
		if err := WriteJSONFile(DataPath("notify.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, maskChannel(ch))
	})
	a.Delete("/api/notify/channels/{channel_id}", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "channel_id")
		data := loadNotify()
		channels := notifyList(data, "channels")
		kept := []map[string]any{}
		for _, c := range channels {
			if str(c["id"]) != id {
				kept = append(kept, c)
			}
		}
		if len(kept) == len(channels) {
			return NewHTTPError(404, "通知渠道不存在")
		}
		data["channels"] = kept
		if err := WriteJSONFile(DataPath("notify.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/notify/channels/{channel_id}/test", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "channel_id")
		var ch map[string]any
		for _, c := range notifyChannels() {
			if str(c["id"]) == id {
				ch = c
			}
		}
		if ch == nil {
			return NewHTTPError(404, "通知渠道不存在")
		}
		if err := sendChannel(ch, "【Graw 通知测试】渠道 "+str(ch["name"])+" 配置正常"); err != nil {
			return NewHTTPError(502, err.Error())
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "name": str(ch["name"])})
	})
	a.Get("/api/notify/rules", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"rules": notifyList(loadNotify(), "rules")})
	})
	a.Post("/api/notify/rules", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		var req map[string]any
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		req["id"] = "rule_" + randomHex(8)
		data := loadNotify()
		data["rules"] = append(notifyList(data, "rules"), req)
		if err := WriteJSONFile(DataPath("notify.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, req)
	})
	a.Put("/api/notify/rules/{rule_id}", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "rule_id")
		var req map[string]any
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		data := loadNotify()
		rules := notifyList(data, "rules")
		var rule map[string]any
		for _, rr := range rules {
			if str(rr["id"]) == id {
				rule = rr
				for k, v := range req {
					rr[k] = v
				}
			}
		}
		if rule == nil {
			return NewHTTPError(404, "告警规则不存在")
		}
		data["rules"] = rules
		if err := WriteJSONFile(DataPath("notify.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, rule)
	})
	a.Delete("/api/notify/rules/{rule_id}", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "rule_id")
		data := loadNotify()
		kept := []map[string]any{}
		for _, rr := range notifyList(data, "rules") {
			if str(rr["id"]) != id {
				kept = append(kept, rr)
			}
		}
		data["rules"] = kept
		if err := WriteJSONFile(DataPath("notify.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Put("/api/notify/config", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		var req map[string]any
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		data := loadNotify()
		data["config"] = req
		if err := WriteJSONFile(DataPath("notify.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/notify/test-alert", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		msg := "【Graw 通知测试】这是一条测试告警，请忽略。"
		sent, failed := pushAll(msg)
		appendNotifyLog(msg, sent, failed)
		return WriteJSON(w, 200, map[string]any{"ok": true, "sent": sent, "failed": failed})
	})
	a.Get("/api/notify/logs", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		var logs []map[string]any
		_ = ReadJSON(DataPath("notify_logs.json"), &logs)
		return WriteJSON(w, 200, map[string]any{"logs": logs})
	})
	a.Post("/api/notify/logs/clear", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		_ = WriteJSONFile(DataPath("notify_logs.json"), []any{})
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})

	registerUptimeRoutes(a)
	registerCertcheckRoutes(a)
	registerSvcMonitorRoutes(a)
}

func validChannelType(t string) bool {
	switch t {
	case "webhook", "telegram", "dingtalk", "wecom", "serverchan", "smtp":
		return true
	}
	return false
}

// --- 站点可用性 ---

func loadUptime() map[string]any {
	data := map[string]any{"items": []any{}}
	_ = ReadJSON(DataPath("uptime.json"), &data)
	if _, ok := data["items"]; !ok {
		data["items"] = []any{}
	}
	return data
}

func uptimeItems(data map[string]any) []map[string]any {
	out := []map[string]any{}
	if arr, ok := data["items"].([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok {
				out = append(out, m)
			}
		}
	}
	return out
}

func probeURL(target string) map[string]any {
	start := time.Now()
	client := &http.Client{
		Timeout: 8 * time.Second,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) >= 5 {
				return fmt.Errorf("too many redirects")
			}
			return nil
		},
	}
	resp, err := client.Get(target)
	latency := time.Since(start).Milliseconds()
	if err != nil {
		return map[string]any{"status": "down", "code": nil, "latency_ms": nil, "error": err.Error()}
	}
	defer resp.Body.Close()
	status := "ok"
	if resp.StatusCode >= 400 {
		status = "down"
	}
	return map[string]any{"status": status, "code": resp.StatusCode, "latency_ms": latency, "error": nil}
}

func probeUptimeItem(item map[string]any) map[string]any {
	target := str(item["url"])
	if target == "" {
		return map[string]any{"status": "down", "code": nil, "latency_ms": nil, "error": "URL 为空"}
	}
	return probeURL(target)
}

func registerUptimeRoutes(a *App) {
	a.Get("/api/uptime/status", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		items := uptimeItems(loadUptime())
		up, down := 0, 0
		for _, it := range items {
			switch str(it["status"]) {
			case "ok":
				up++
			case "down":
				down++
			}
		}
		return WriteJSON(w, 200, map[string]any{"total": len(items), "up": up, "down": down})
	})
	a.Get("/api/uptime/items", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"items": uptimeItems(loadUptime())})
	})
	a.Post("/api/uptime/items", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		var req map[string]any
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		u := str(req["url"])
		if !strings.HasPrefix(u, "http://") && !strings.HasPrefix(u, "https://") {
			return NewHTTPError(400, "URL 必须以 http:// 或 https:// 开头")
		}
		req["id"] = "up_" + randomHex(8)
		req["status"] = "unknown"
		data := loadUptime()
		data["items"] = append(uptimeItems(data), req)
		if err := WriteJSONFile(DataPath("uptime.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, req)
	})
	a.Put("/api/uptime/items/{item_id}", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "item_id")
		var req map[string]any
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		data := loadUptime()
		items := uptimeItems(data)
		var item map[string]any
		for _, it := range items {
			if str(it["id"]) == id {
				item = it
				for k, v := range req {
					it[k] = v
				}
			}
		}
		if item == nil {
			return NewHTTPError(404, "监控项不存在")
		}
		data["items"] = items
		if err := WriteJSONFile(DataPath("uptime.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, item)
	})
	a.Delete("/api/uptime/items/{item_id}", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "item_id")
		data := loadUptime()
		kept := []map[string]any{}
		for _, it := range uptimeItems(data) {
			if str(it["id"]) != id {
				kept = append(kept, it)
			}
		}
		data["items"] = kept
		if err := WriteJSONFile(DataPath("uptime.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/uptime/items/{item_id}/test", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "item_id")
		var item map[string]any
		for _, it := range uptimeItems(loadUptime()) {
			if str(it["id"]) == id {
				item = it
			}
		}
		if item == nil {
			return NewHTTPError(404, "监控项不存在")
		}
		result := probeUptimeItem(item)
		return WriteJSON(w, 200, result)
	})
}

// --- 证书到期 ---

func loadCertCheck() map[string]any {
	data := map[string]any{"enabled": true, "days_before": 14}
	_ = ReadJSON(DataPath("certcheck.json"), &data)
	return data
}

func certCheckList() []map[string]any {
	certs := sslCertsList(loadSSLCerts())
	out := []map[string]any{}
	for _, c := range certs {
		path := str(c["cert_path"])
		expires := str(c["expires_at"])
		if expires == "" && path != "" {
			expires = readCertExpiry(path)
		}
		daysLeft := int64(0)
		status := "unknown"
		if expires != "" {
			if t, err := time.Parse("2006-01-02T15:04:05", expires); err == nil {
				daysLeft = int64(time.Until(t).Hours() / 24)
				status = "valid"
				if daysLeft < 0 {
					status = "expired"
				} else if daysLeft <= 14 {
					status = "expiring"
				}
			}
		}
		out = append(out, map[string]any{
			"id": c["id"], "name": c["name"], "domains": c["domains"],
			"cert_path": path, "expires_at": expires, "days_left": daysLeft, "status": status,
		})
	}
	return out
}

func registerCertcheckRoutes(a *App) {
	a.Get("/api/certcheck/status", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		cfg := loadCertCheck()
		certs := certCheckList()
		expiring := 0
		for _, c := range certs {
			if str(c["status"]) == "expiring" || str(c["status"]) == "expired" {
				expiring++
			}
		}
		return WriteJSON(w, 200, map[string]any{
			"enabled": boolOf(cfg["enabled"]), "days_before": int(int64Of(cfg["days_before"])),
			"cert_count": len(certs), "expiring": expiring,
		})
	})
	a.Get("/api/certcheck/certs", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"certs": certCheckList()})
	})
	a.Post("/api/certcheck/test", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		n := 0
		for _, c := range certCheckList() {
			if str(c["status"]) == "expiring" || str(c["status"]) == "expired" {
				n++
			}
		}
		sent, failed := 0, 0
		if n > 0 {
			sent, failed = pushAll("【Graw 证书到期提醒】有 " + itoa(n) + " 张证书即将到期或已过期，请及时续签。")
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "triggered": n, "sent": sent, "failed": failed})
	})
	a.Put("/api/certcheck/config", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Enabled    bool `json:"enabled"`
			DaysBefore int  `json:"days_before"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if req.DaysBefore <= 0 {
			req.DaysBefore = 14
		}
		if err := WriteJSONFile(DataPath("certcheck.json"), map[string]any{
			"enabled": req.Enabled, "days_before": req.DaysBefore,
		}); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
}

// --- 服务与端口监控 ---

func loadSvcMonitor() map[string]any {
	data := map[string]any{"items": []any{}}
	_ = ReadJSON(DataPath("svcmonitor.json"), &data)
	if _, ok := data["items"]; !ok {
		data["items"] = []any{}
	}
	return data
}

func svcItems(data map[string]any) []map[string]any {
	out := []map[string]any{}
	if arr, ok := data["items"].([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok {
				out = append(out, m)
			}
		}
	}
	return out
}

func probeSvcItem(item map[string]any) map[string]any {
	kind := str(item["kind"])
	if kind == "" {
		kind = str(item["type"])
	}
	switch kind {
	case "port":
		host := defaultString(str(item["host"]), "127.0.0.1")
		port := int(int64Of(item["port"]))
		if port < 1 || port > 65535 {
			return map[string]any{"status": "down", "detail": "端口号超出范围 1-65535"}
		}
		start := time.Now()
		if remoteActive() {
			script := "timeout 3 bash -c 'exec 3<>/dev/tcp/" + host + "/" + itoa(port) +
				"; exec 3>&-' >/dev/null 2>&1 && echo OPEN || echo CLOSED"
			res := HostShell(8*time.Second, script)
			latency := time.Since(start).Milliseconds()
			if strings.Contains(res.Stdout, "OPEN") {
				return map[string]any{"status": "ok", "detail": "连接成功（" + itoa(int(latency)) + "ms）"}
			}
			return map[string]any{"status": "down", "detail": "端口不可达"}
		}
		conn, err := net.DialTimeout("tcp", net.JoinHostPort(host, itoa(port)), 3*time.Second)
		latency := time.Since(start).Milliseconds()
		if err != nil {
			return map[string]any{"status": "down", "detail": err.Error()}
		}
		conn.Close()
		return map[string]any{"status": "ok", "detail": "连接成功（" + itoa(int(latency)) + "ms）"}
	case "process":
		name := strings.ToLower(str(item["target"]))
		if name == "" {
			name = strings.ToLower(str(item["process"]))
		}
		procs := listProcesses()
		if remoteActive() {
			procs = remoteProcessList()
		}
		for _, p := range procs {
			if strings.Contains(strings.ToLower(str(p["name"])), name) {
				return map[string]any{"status": "ok", "detail": "进程存在（PID " + str(p["pid"]) + "）"}
			}
		}
		return map[string]any{"status": "down", "detail": "未找到匹配进程"}
	case "service":
		if windowsTarget() {
			return map[string]any{"status": "unknown", "detail": "systemd 服务检测仅支持 Linux"}
		}
		name := str(item["target"])
		if HostWhich("systemctl") == "" {
			return map[string]any{"status": "unknown", "detail": "宿主缺少 systemctl"}
		}
		res := HostCmd(10*time.Second, "systemctl", "is-active", name)
		out := strings.TrimSpace(res.Stdout)
		if out == "active" {
			return map[string]any{"status": "ok", "detail": "服务状态：" + out}
		}
		return map[string]any{"status": "down", "detail": "服务未运行（" + out + "）"}
	}
	return map[string]any{"status": "unknown", "detail": "未知监控类型：" + kind}
}

func registerSvcMonitorRoutes(a *App) {
	a.Get("/api/svcmonitor/status", perm("svcmonitor"), func(w http.ResponseWriter, r *http.Request) error {
		items := svcItems(loadSvcMonitor())
		up, down := 0, 0
		for _, it := range items {
			switch str(it["status"]) {
			case "ok":
				up++
			case "down":
				down++
			}
		}
		return WriteJSON(w, 200, map[string]any{"total": len(items), "up": up, "down": down})
	})
	a.Get("/api/svcmonitor/items", perm("svcmonitor"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"items": svcItems(loadSvcMonitor())})
	})
	a.Post("/api/svcmonitor/items", perm("svcmonitor"), func(w http.ResponseWriter, r *http.Request) error {
		var req map[string]any
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		req["id"] = "svc_" + randomHex(8)
		req["status"] = "unknown"
		data := loadSvcMonitor()
		data["items"] = append(svcItems(data), req)
		if err := WriteJSONFile(DataPath("svcmonitor.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, req)
	})
	a.Put("/api/svcmonitor/items/{item_id}", perm("svcmonitor"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "item_id")
		var req map[string]any
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		data := loadSvcMonitor()
		items := svcItems(data)
		var item map[string]any
		for _, it := range items {
			if str(it["id"]) == id {
				item = it
				for k, v := range req {
					it[k] = v
				}
			}
		}
		if item == nil {
			return NewHTTPError(404, "监控项不存在")
		}
		data["items"] = items
		if err := WriteJSONFile(DataPath("svcmonitor.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, item)
	})
	a.Delete("/api/svcmonitor/items/{item_id}", perm("svcmonitor"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "item_id")
		data := loadSvcMonitor()
		kept := []map[string]any{}
		for _, it := range svcItems(data) {
			if str(it["id"]) != id {
				kept = append(kept, it)
			}
		}
		data["items"] = kept
		if err := WriteJSONFile(DataPath("svcmonitor.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/svcmonitor/items/{item_id}/test", perm("svcmonitor"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "item_id")
		var item map[string]any
		for _, it := range svcItems(loadSvcMonitor()) {
			if str(it["id"]) == id {
				item = it
			}
		}
		if item == nil {
			return NewHTTPError(404, "监控项不存在")
		}
		return WriteJSON(w, 200, probeSvcItem(item))
	})
	a.Post("/api/svcmonitor/items/{item_id}/action", perm("svcmonitor"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "item_id")
		var req struct {
			Action string `json:"action"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if windowsTarget() {
			return WriteJSON(w, 200, map[string]any{"ok": false, "status": "unknown", "detail": "systemd 服务操作仅支持 Linux"})
		}
		var item map[string]any
		for _, it := range svcItems(loadSvcMonitor()) {
			if str(it["id"]) == id {
				item = it
			}
		}
		if item == nil {
			return NewHTTPError(404, "监控项不存在")
		}
		if req.Action != "start" && req.Action != "stop" && req.Action != "restart" {
			return WriteJSON(w, 200, map[string]any{"ok": false, "status": "unknown", "detail": "不支持的服务动作"})
		}
		res := HostCmd(30*time.Second, "systemctl", req.Action, str(item["target"]))
		ok := res.Code == 0
		return WriteJSON(w, 200, map[string]any{
			"ok": ok, "status": map[bool]string{true: "ok", false: "down"}[ok],
			"detail": strings.TrimSpace(res.Stderr + res.Stdout),
		})
	})
}

// StartMonitors 启动可用性 / 服务监控 / 证书检查后台协程。
func StartMonitors() {
	go func() {
		for {
			func() {
				defer func() { _ = recover() }()
				data := loadUptime()
				items := uptimeItems(data)
				changed := false
				for _, it := range items {
					if !boolOf(it["enabled"]) && it["enabled"] != nil {
						continue
					}
					prev := str(it["status"])
					result := probeUptimeItem(it)
					it["status"] = result["status"]
					it["last_code"] = result["code"]
					it["last_latency_ms"] = result["latency_ms"]
					it["checked_at"] = time.Now().Format("2006-01-02 15:04:05")
					changed = true
					cur := str(result["status"])
					if prev == "ok" && cur == "down" {
						pushAll("【Graw 站点告警】" + str(it["name"]) + " 无法访问：" + str(it["url"]))
					} else if prev == "down" && cur == "ok" {
						pushAll("【Graw 站点恢复】" + str(it["name"]) + " 已恢复访问。")
					}
				}
				if changed {
					data["items"] = items
					_ = WriteJSONFile(DataPath("uptime.json"), data)
				}
			}()
			time.Sleep(60 * time.Second)
		}
	}()
	go func() {
		for {
			func() {
				defer func() { _ = recover() }()
				cfg := loadCertCheck()
				if !boolOf(cfg["enabled"]) {
					return
				}
				days := int(int64Of(cfg["days_before"]))
				if days <= 0 {
					days = 14
				}
				alerted := []string{}
				for _, cert := range certCheckList() {
					if int64Of(cert["days_left"]) <= int64(days) {
						alerted = append(alerted, str(cert["name"]))
					}
				}
				if len(alerted) > 0 {
					pushAll("【Graw 证书到期提醒】以下证书即将到期：" + strings.Join(alerted, "、"))
				}
			}()
			time.Sleep(6 * time.Hour)
		}
	}()
}
