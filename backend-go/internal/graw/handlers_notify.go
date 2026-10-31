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
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

// 通知中心 / 站点可用性 / 证书到期 / 服务与端口监控。

func loadNotify() map[string]any {
	data := map[string]any{
		"enabled": false, "interval_seconds": 60, "cooldown_seconds": 300,
		"channels": []any{}, "rules": []any{},
	}
	_ = ReadJSON(DataPath("notify.json"), &data)
	if data == nil {
		data = map[string]any{
			"enabled": false, "interval_seconds": 60, "cooldown_seconds": 300,
			"channels": []any{}, "rules": []any{},
		}
	}
	// 兼容 Go 早期版本写入的嵌套 config 结构（Python 旧版与前端均使用顶层字段）。
	if cfg, ok := data["config"].(map[string]any); ok {
		if _, present := data["enabled"]; !present {
			data["enabled"] = boolOf(cfg["enabled"])
		}
		if _, present := data["interval_seconds"]; !present {
			data["interval_seconds"] = int(int64Of(cfg["interval_seconds"]))
		}
		if _, present := data["cooldown_seconds"]; !present {
			data["cooldown_seconds"] = int(int64Of(cfg["cooldown_seconds"]))
		}
		delete(data, "config")
	}
	if _, ok := data["channels"]; !ok {
		data["channels"] = []any{}
	}
	if _, ok := data["rules"]; !ok {
		data["rules"] = []any{}
	}
	return data
}

// notifyInterval 返回阈值检查间隔（秒），非法值回落到默认 60。
func notifyInterval(data map[string]any) int {
	n := int(int64Of(data["interval_seconds"]))
	if n < 10 || n > 86400 {
		return 60
	}
	return n
}

// notifyCooldown 返回同一规则告警冷却时间（秒），非法值回落到默认 300。
func notifyCooldown(data map[string]any) int {
	n := int(int64Of(data["cooldown_seconds"]))
	if n < 30 || n > 86400 {
		return 300
	}
	return n
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

// sensitiveChannelKeys 是通知渠道里不能回显给前端的敏感字段。
// 与管理前端约定一致：隐藏原值，只返回 has_<key> 布尔标记。
var sensitiveChannelKeys = []string{"password", "bot_token", "key"}

func maskChannel(ch map[string]any) map[string]any {
	out := map[string]any{
		"id": str(ch["id"]), "name": str(ch["name"]), "type": str(ch["type"]),
		"enabled": boolOf(ch["enabled"]), "config": map[string]any{},
	}
	cfg := mapOf(ch["config"])
	masked := map[string]any{}
	for k, v := range cfg {
		skip := false
		for _, secret := range sensitiveChannelKeys {
			if k == secret {
				skip = true
				break
			}
		}
		if !skip {
			masked[k] = v
		}
	}
	for _, secret := range sensitiveChannelKeys {
		masked["has_"+secret] = str(cfg[secret]) != ""
	}
	out["config"] = masked
	return out
}

func notifyChannels() []map[string]any {
	return notifyList(loadNotify(), "channels")
}

// stripCRLF 去除邮件头字段中的换行，避免头注入（CWE-640）。
func stripCRLF(s string) string {
	return strings.ReplaceAll(strings.ReplaceAll(s, "\r", ""), "\n", "")
}

// notifyHTTPClient 返回带 SSRF 防护且不跟随重定向的客户端。
// 用户提供的 Webhook 地址每次发送前都会由 DialContext 重新解析并校验，
// 3xx 原样返回（调用方按错误处理），避免重定向绕过初始 URL 校验。
func notifyHTTPClient() *http.Client {
	return NewSafeHTTPClient(15*time.Second, NotifyAllowPrivateNet(), false, false)
}

// postJSONNoRedirect 发送 JSON POST 并拒绝 3xx（与 Python _post_no_redirect 一致）。
func postJSONNoRedirect(target string, payload any) error {
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	req, err := http.NewRequest(http.MethodPost, target, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := notifyHTTPClient().Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 && resp.StatusCode < 400 {
		return fmt.Errorf("检测到重定向跳转（HTTP %d），已拒绝（SSRF 防护）", resp.StatusCode)
	}
	if resp.StatusCode >= 300 {
		return fmt.Errorf("Webhook 返回 %d", resp.StatusCode)
	}
	return nil
}

func sendChannel(ch map[string]any, message string) error {
	cfg := mapOf(ch["config"])
	typ := str(ch["type"])
	client := notifyHTTPClient()
	switch typ {
	case "webhook":
		target := str(cfg["url"])
		if target == "" {
			return fmt.Errorf("webhook url 为空")
		}
		if err := AssertSafeHost(ssrfNormalizeHost(target), NotifyAllowPrivateNet(), false); err != nil {
			return err
		}
		return postJSONNoRedirect(target, map[string]any{"text": message, "message": message, "content": message})
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
		if err := AssertSafeHost(ssrfNormalizeHost(webhook), NotifyAllowPrivateNet(), false); err != nil {
			return err
		}
		return postJSONNoRedirect(webhook, map[string]any{
			"msgtype": "text", "text": map[string]string{"content": message},
		})
	case "wecom":
		webhook := str(cfg["webhook"])
		if webhook == "" {
			return fmt.Errorf("企业微信 webhook 为空")
		}
		if err := AssertSafeHost(ssrfNormalizeHost(webhook), NotifyAllowPrivateNet(), false); err != nil {
			return err
		}
		return postJSONNoRedirect(webhook, map[string]any{
			"msgtype": "text", "text": map[string]string{"content": message},
		})
	case "serverchan":
		// 前端与 Python 旧版使用的字段名是 key；兼容早期 Go 版本写入的 send_key。
		sendKey := defaultString(str(cfg["key"]), str(cfg["send_key"]))
		if sendKey == "" {
			return fmt.Errorf("Server酱 SendKey 为空")
		}
		form := url.Values{}
		form.Set("title", "Graw 通知")
		form.Set("desp", message)
		resp, err := client.PostForm("https://sctapi.ftqq.com/"+url.PathEscape(sendKey)+".send", form)
		if err != nil {
			return err
		}
		resp.Body.Close()
		if resp.StatusCode >= 300 {
			return fmt.Errorf("Server酱 返回 %d", resp.StatusCode)
		}
	case "smtp":
		return sendSMTP(cfg, message)
	}
	return fmt.Errorf("不支持的渠道类型")
}

// sendSMTP 发送邮件（支持 465 隐式 TLS、其他端口 STARTTLS 与明文）。
// SMTP 主机属于「本机回环服务合法」场景：允许回环与私网，但始终拒绝
// 链路本地（含云元数据）/ 组播 / 保留 / 未指定地址。
func sendSMTP(cfg map[string]any, message string) error {
	host := strings.TrimSpace(str(cfg["host"]))
	if host == "" {
		return fmt.Errorf("SMTP 主机为空")
	}
	if err := AssertSafeHost(host, true, true); err != nil {
		return fmt.Errorf("SMTP 主机校验失败：%v", err)
	}
	port := int(int64Of(cfg["port"]))
	useSSL := boolOf(cfg["ssl"])
	useStartTLS := boolOf(cfg["starttls"])
	if port == 0 {
		if useSSL {
			port = 465
		} else {
			port = 25
		}
	}
	if port < 1 || port > 65535 {
		return fmt.Errorf("SMTP 端口必须在 1-65535")
	}
	user := str(cfg["username"])
	pass := str(cfg["password"])
	from := strings.TrimSpace(str(cfg["from"]))
	toRaw := str(cfg["to"])
	subject := stripCRLF(strings.TrimSpace(str(cfg["subject"])))
	if subject == "" {
		subject = "Graw 资源告警"
	}
	if from == "" || toRaw == "" {
		return fmt.Errorf("SMTP 发件人/收件人不能为空")
	}
	recipients := []string{}
	for _, item := range strings.Split(toRaw, ",") {
		if addr := strings.TrimSpace(item); addr != "" {
			recipients = append(recipients, stripCRLF(addr))
		}
	}
	if len(recipients) == 0 {
		return fmt.Errorf("SMTP 收件人不能为空")
	}
	addr := net.JoinHostPort(host, itoa(port))
	conn, err := net.DialTimeout("tcp", addr, 15*time.Second)
	if err != nil {
		return err
	}
	if useSSL {
		tlsConn := tls.Client(conn, &tls.Config{ServerName: host, MinVersion: tls.VersionTLS12})
		if err := tlsConn.Handshake(); err != nil {
			conn.Close()
			return err
		}
		conn = tlsConn
	}
	c, err := smtp.NewClient(conn, host)
	if err != nil {
		conn.Close()
		return err
	}
	defer c.Close()
	// 非 465 端口且勾选 STARTTLS：先升级为加密通道再认证。
	if !useSSL && useStartTLS {
		if err := c.StartTLS(&tls.Config{ServerName: host, MinVersion: tls.VersionTLS12}); err != nil {
			return err
		}
	}
	if user != "" {
		if err := c.Auth(smtp.PlainAuth("", user, pass, host)); err != nil {
			return err
		}
	}
	if err := c.Mail(stripCRLF(from)); err != nil {
		return err
	}
	for _, rcpt := range recipients {
		if err := c.Rcpt(rcpt); err != nil {
			return err
		}
	}
	w, err := c.Data()
	if err != nil {
		return err
	}
	headers := "From: " + stripCRLF(from) + "\r\nTo: " + strings.Join(recipients, ", ") +
		"\r\nSubject: " + subject + "\r\nMIME-Version: 1.0\r\n" +
		"Content-Type: text/plain; charset=UTF-8\r\n\r\n"
	if _, err := w.Write([]byte(headers + message)); err != nil {
		return err
	}
	if err := w.Close(); err != nil {
		return err
	}
	return c.Quit()
}

// validateNotifyChannel 按渠道类型校验必需字段与出站地址（创建/更新时调用）。
// 与 Python _validate_channel 对齐，避免缺字段的渠道在告警发送时才报错。
func validateNotifyChannel(ch map[string]any) *HTTPError {
	typ := str(ch["type"])
	if !validChannelType(typ) {
		return NewHTTPError(400, "不支持的通知渠道类型")
	}
	name := strings.TrimSpace(str(ch["name"]))
	if name == "" || len([]rune(name)) > 64 {
		return NewHTTPError(400, "名称为必填项且不超过 64 个字符")
	}
	ch["name"] = name
	cfg := mapOf(ch["config"])
	checkURL := func(raw, field string) *HTTPError {
		value := strings.TrimSpace(raw)
		if value == "" {
			return NewHTTPError(400, field+"不能为空")
		}
		if err := AssertSafeHTTPURL(value, NotifyAllowPrivateNet()); err != nil {
			// 创建阶段对无法解析的主机名放宽（离线环境），发送前仍会严格校验。
			if !strings.Contains(err.Error(), "无法解析主机名") {
				return NewHTTPError(400, field+"校验失败："+err.Error())
			}
		}
		return nil
	}
	switch typ {
	case "webhook":
		if e := checkURL(str(cfg["url"]), "Webhook 地址"); e != nil {
			return e
		}
	case "dingtalk":
		if e := checkURL(str(cfg["webhook"]), "钉钉机器人地址"); e != nil {
			return e
		}
	case "wecom":
		if e := checkURL(str(cfg["webhook"]), "企业微信机器人地址"); e != nil {
			return e
		}
	case "telegram":
		token, chatID := strings.TrimSpace(str(cfg["bot_token"])), strings.TrimSpace(str(cfg["chat_id"]))
		if token == "" {
			return NewHTTPError(400, "Telegram 机器人 Token 不能为空")
		}
		if chatID == "" {
			return NewHTTPError(400, "Telegram 接收 chat_id 不能为空")
		}
		if hasCtrlOrSpace(token) || hasCtrlOrSpace(chatID) {
			return NewHTTPError(400, "Telegram Token/chat_id 不能包含空白或控制字符")
		}
		cfg["bot_token"], cfg["chat_id"] = token, chatID
	case "serverchan":
		sendKey := strings.TrimSpace(str(cfg["key"]))
		if sendKey == "" {
			return NewHTTPError(400, "Server酱 SendKey 不能为空")
		}
		if hasCtrlOrSpace(sendKey) {
			return NewHTTPError(400, "Server酱 SendKey 不能包含空白或控制字符")
		}
		cfg["key"] = sendKey
	case "smtp":
		host := strings.TrimSpace(str(cfg["host"]))
		if host == "" {
			return NewHTTPError(400, "SMTP 主机不能为空")
		}
		port := int(int64Of(cfg["port"]))
		if port < 1 || port > 65535 {
			return NewHTTPError(400, "SMTP 端口必须在 1-65535")
		}
		if strings.TrimSpace(str(cfg["from"])) == "" || strings.TrimSpace(str(cfg["to"])) == "" {
			return NewHTTPError(400, "SMTP 发件人/收件人不能为空")
		}
		if hasCtrlOrSpace(host) {
			return NewHTTPError(400, "SMTP 主机不能包含空白或控制字符")
		}
		if err := AssertSafeHost(host, true, true); err != nil {
			return NewHTTPError(400, "SMTP 主机校验失败："+err.Error())
		}
		cfg["host"] = host
		cfg["port"] = port
	}
	ch["config"] = cfg
	return nil
}

// hasCtrlOrSpace 判断字符串是否包含空白或控制字符（URL/请求头字段防注入）。
func hasCtrlOrSpace(value string) bool {
	for _, r := range value {
		if r <= 0x20 || r == 0x7f || unicodeIsSpace(r) {
			return true
		}
	}
	return false
}

// unicodeIsSpace 只覆盖 HTTP 头/URL 里常见空白（不引入 unicode 包的大表）。
func unicodeIsSpace(r rune) bool {
	switch r {
	case '\u00a0', '\u1680', '\u2028', '\u2029', '\u202f', '\u205f', '\u3000':
		return true
	}
	return r >= '\u2000' && r <= '\u200a'
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

// appendNotifyLog 追加一条告警记录（环形截断到 200 条，与 Python notify_logs 一致）。
// 字段名对齐前端：id/time/metric/value/threshold/message/sent_channels/failed_channels。
func appendNotifyLog(message string, sent, failed int) {
	appendNotifyLogEntry(map[string]any{
		"metric": "", "value": nil, "threshold": nil,
		"message": message, "sent_channels": sent, "failed_channels": failed,
	})
}

func appendNotifyLogEntry(entry map[string]any) {
	var logs []map[string]any
	_ = ReadJSON(DataPath("notify_logs.json"), &logs)
	entry["id"] = randomHex(12)
	entry["time"] = time.Now().Format(time.RFC3339)
	if _, ok := entry["message"]; !ok {
		entry["message"] = ""
	}
	// 新记录置顶，只保留最近 200 条。
	logs = append([]map[string]any{entry}, logs...)
	if len(logs) > 200 {
		logs = logs[:200]
	}
	_ = WriteJSONFile(DataPath("notify_logs.json"), logs)
}

func init() { PushAllFunc = pushAll }

func registerNotifyModuleRoutes(a *App) {
	a.Get("/api/notify/status", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		data := loadNotify()
		logs := []map[string]any{}
		_ = ReadJSON(DataPath("notify_logs.json"), &logs)
		return WriteJSON(w, 200, map[string]any{
			"enabled":          boolOf(data["enabled"]),
			"interval_seconds": notifyInterval(data),
			"cooldown_seconds": notifyCooldown(data),
			"channel_count":    len(notifyList(data, "channels")),
			"rule_count":       len(notifyList(data, "rules")),
			"log_count":        len(logs),
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
		enabled := true
		if req.Enabled != nil {
			enabled = *req.Enabled
		}
		if req.Config == nil {
			req.Config = map[string]any{}
		}
		ch := map[string]any{
			"id": "ch_" + randomHex(10), "name": req.Name, "type": req.Type,
			"enabled": enabled, "config": req.Config, "created_at": time.Now().Format(time.RFC3339),
		}
		if err := validateNotifyChannel(ch); err != nil {
			return err
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
				// 敏感字段留空表示保持原值（前端编辑时不回显原值）；
				// 早期 Go 版本曾用 "******" 占位，同样视为不修改。
				if str(v) == "******" {
					continue
				}
				secret := false
				for _, sk := range sensitiveChannelKeys {
					if k == sk {
						secret = true
						break
					}
				}
				if secret && strings.TrimSpace(str(v)) == "" {
					continue
				}
				old[k] = v
			}
			c["config"] = old
		}
		if ch == nil {
			return NewHTTPError(404, "通知渠道不存在")
		}
		if err := validateNotifyChannel(ch); err != nil {
			return err
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
		if err := validateNotifyRule(req); err != nil {
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
		if err := validateNotifyRule(rule); err != nil {
			return err
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
		var req struct {
			Enabled         *bool `json:"enabled"`
			IntervalSeconds *int  `json:"interval_seconds"`
			CooldownSeconds *int  `json:"cooldown_seconds"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		data := loadNotify()
		if req.Enabled != nil {
			data["enabled"] = *req.Enabled
		}
		if req.IntervalSeconds != nil {
			if *req.IntervalSeconds < 10 || *req.IntervalSeconds > 86400 {
				return NewHTTPError(400, "检查间隔需在 10-86400 秒之间")
			}
			data["interval_seconds"] = *req.IntervalSeconds
		}
		if req.CooldownSeconds != nil {
			if *req.CooldownSeconds < 30 || *req.CooldownSeconds > 86400 {
				return NewHTTPError(400, "冷却时间需在 30-86400 秒之间")
			}
			data["cooldown_seconds"] = *req.CooldownSeconds
		}
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
		limit := QueryInt(r, "limit", 200)
		if limit <= 0 {
			limit = 200
		}
		if limit > 200 {
			limit = 200
		}
		if len(logs) > limit {
			logs = logs[:limit]
		}
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

// atoiSafe 解析十进制整数；非法返回 0（监控目标里的端口字段来自用户输入）。
func atoiSafe(s string) int {
	n, err := strconv.Atoi(strings.TrimSpace(s))
	if err != nil {
		return 0
	}
	return n
}

// notifyMetrics 是资源告警支持的指标集合（与前端下拉/巡检报告口径一致）。
var notifyMetrics = map[string]bool{"cpu": true, "mem": true, "disk": true, "load": true, "temp": true}

// validateNotifyRule 校验告警规则：指标必须受支持、阈值在 0-10000。
func validateNotifyRule(rule map[string]any) *HTTPError {
	metric := strings.TrimSpace(str(rule["metric"]))
	if !notifyMetrics[metric] {
		return NewHTTPError(400, "不支持的告警指标："+metric)
	}
	rule["metric"] = metric
	threshold := num(rule["threshold"])
	if threshold < 0 || threshold > 10000 {
		return NewHTTPError(400, "阈值需在 0-10000 之间")
	}
	rule["threshold"] = threshold
	if _, ok := rule["enabled"]; !ok {
		rule["enabled"] = true
	}
	return nil
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

// uptimeProbeURL 对 URL 执行一次探测（HEAD 优先，失败回退 GET）。
// 不跟随重定向（避免 30x 绕过初始 URL 的 SSRF 校验）；SSRF 防护在拨号阶段
// 使用已校验的解析结果，消除 DNS rebinding TOCTOU。
func uptimeProbeURL(target string, timeoutSeconds int) map[string]any {
	if timeoutSeconds <= 0 {
		timeoutSeconds = 10
	}
	if timeoutSeconds > 60 {
		timeoutSeconds = 60
	}
	client := NewSafeHTTPClient(time.Duration(timeoutSeconds)*time.Second, UptimeAllowPrivateNet(), false, false)
	start := time.Now()
	var last map[string]any
	for _, method := range []string{http.MethodHead, http.MethodGet} {
		req, err := http.NewRequest(method, target, nil)
		if err != nil {
			return map[string]any{"status": "down", "code": nil, "latency_ms": nil, "error": "URL 非法"}
		}
		resp, err := client.Do(req)
		latency := time.Since(start).Milliseconds()
		if err != nil {
			last = map[string]any{"status": "down", "code": nil, "latency_ms": nil, "error": truncateText(err.Error(), 200)}
			continue
		}
		resp.Body.Close()
		return map[string]any{
			"status": "ok", "code": resp.StatusCode, "latency_ms": latency, "error": nil,
		}
	}
	if last == nil {
		last = map[string]any{"status": "down", "code": nil, "latency_ms": nil, "error": "请求失败"}
	}
	return last
}

// uptimeValidateURL 校验监控地址（scheme + SSRF）；无法解析的主机名在创建阶段
// 放行（离线/内网域名场景），实际探测时拨号校验会 fail-closed。
func uptimeValidateURL(raw string) (string, *HTTPError) {
	value := strings.TrimSpace(raw)
	if value == "" {
		return "", NewHTTPError(400, "监控地址不能为空")
	}
	if len(value) > 2048 {
		return "", NewHTTPError(400, "监控地址过长")
	}
	u, err := url.Parse(value)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		return "", NewHTTPError(400, "URL 必须以 http:// 或 https:// 开头")
	}
	if err := AssertSafeHTTPURL(value, UptimeAllowPrivateNet()); err != nil {
		if !strings.Contains(err.Error(), "无法解析主机名") {
			return "", NewHTTPError(400, err.Error())
		}
	}
	return value, nil
}

// uptimeValidateItem 校验监控项字段（创建时要求必填，更新时只校验已提供字段）。
func uptimeValidateItem(req map[string]any, isCreate bool) *HTTPError {
	if v, ok := req["name"]; ok || isCreate {
		name := strings.TrimSpace(str(v))
		if name == "" || len([]rune(name)) > 64 {
			return NewHTTPError(400, "名称必填且不超过 64 个字符")
		}
		req["name"] = name
	}
	if v, ok := req["url"]; ok || isCreate {
		validated, err := uptimeValidateURL(str(v))
		if err != nil {
			return err
		}
		req["url"] = validated
	}
	if v, ok := req["expect_status"]; ok || isCreate {
		n := int(int64Of(v))
		if n == 0 && isCreate {
			n = 200
		}
		if n < 100 || n > 599 {
			return NewHTTPError(400, "预期状态码需在 100-599 之间")
		}
		req["expect_status"] = n
	}
	if v, ok := req["timeout_seconds"]; ok || isCreate {
		n := int(int64Of(v))
		if n == 0 {
			n = 10
		}
		if n < 1 || n > 60 {
			return NewHTTPError(400, "超时需在 1-60 秒之间")
		}
		req["timeout_seconds"] = n
	}
	if v, ok := req["interval_seconds"]; ok || isCreate {
		n := int(int64Of(v))
		if n == 0 {
			n = 60
		}
		if n < 10 || n > 86400 {
			return NewHTTPError(400, "探测间隔需在 10-86400 秒之间")
		}
		req["interval_seconds"] = n
	}
	if _, ok := req["enabled"]; !ok && isCreate {
		req["enabled"] = true
	}
	return nil
}

// probeAndAlert 探测单个监控项并更新状态机 / 历史，状态变化时推送通知。
func probeAndAlert(item map[string]any) map[string]any {
	result := uptimeProbeURL(str(item["url"]), int(int64Of(item["timeout_seconds"])))
	now := time.Now()
	nowISO := now.Format(time.RFC3339)
	expect := int(int64Of(item["expect_status"]))
	if expect == 0 {
		expect = 200
	}
	isOK := result["status"] == "ok" && int(num(result["code"])) == expect
	current := "down"
	if isOK {
		current = "ok"
	} else if result["status"] == "ok" {
		result["error"] = fmt.Sprintf("状态码 %v 与预期 %d 不符", result["code"], expect)
	}
	prev, hadPrev := item["last_status"]
	item["last_status"] = current
	item["last_code"] = result["code"]
	item["last_latency_ms"] = result["latency_ms"]
	item["last_checked_at"] = nowISO
	item["last_checked_ts"] = float64(now.Unix())
	if current == "down" && str(prev) != "down" {
		item["down_since"] = nowISO
	} else if current == "ok" {
		item["down_since"] = ""
	}
	// 环形保留最近 10 条探测历史。
	history := []any{}
	if arr, ok := item["history"].([]any); ok {
		history = arr
	}
	history = append(history, map[string]any{
		"time": nowISO, "status": current, "code": result["code"], "latency_ms": result["latency_ms"],
	})
	if len(history) > 10 {
		history = history[len(history)-10:]
	}
	item["history"] = history
	// 首次探测（prev 为 nil）不推送，避免开机轰炸。
	if hadPrev && prev != nil && str(prev) != current {
		name := defaultString(str(item["name"]), str(item["id"]))
		var message string
		if current == "down" {
			detail := defaultString(str(result["error"]), fmt.Sprintf("HTTP %v", result["code"]))
			message = "【Graw 站点告警】" + name + "（" + str(item["url"]) + "）不可访问：" + detail
		} else {
			latency := "—"
			if result["latency_ms"] != nil {
				latency = itoa(int(num(result["latency_ms"]))) + "ms"
			}
			message = "【Graw 站点恢复】" + name + "（" + str(item["url"]) + "）已恢复正常：HTTP " +
				fmt.Sprint(result["code"]) + "，延迟 " + latency
		}
		pushAll(message)
	}
	// 手动测试端点直接返回该监控项的目标状态（ok/down），而不是传输层状态。
	result["status"] = current
	result["checked_at"] = nowISO
	return result
}

func registerUptimeRoutes(a *App) {
	a.Get("/api/uptime/status", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		items := uptimeItems(loadUptime())
		up, down, enabled := 0, 0, 0
		for _, it := range items {
			if !boolOf(it["enabled"]) && it["enabled"] != nil {
				continue
			}
			enabled++
			switch str(it["last_status"]) {
			case "ok":
				up++
			case "down":
				down++
			}
		}
		return WriteJSON(w, 200, map[string]any{
			"item_count": len(items), "enabled_count": enabled, "up_count": up, "down_count": down,
		})
	})
	a.Get("/api/uptime/items", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"items": uptimeItems(loadUptime())})
	})
	a.Post("/api/uptime/items", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		var req map[string]any
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if err := uptimeValidateItem(req, true); err != nil {
			return err
		}
		req["id"] = "up_" + randomHex(10)
		req["created_at"] = time.Now().Format(time.RFC3339)
		req["last_status"] = nil
		req["last_code"] = nil
		req["last_latency_ms"] = nil
		req["last_checked_at"] = ""
		req["last_checked_ts"] = 0
		req["down_since"] = ""
		req["history"] = []any{}
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
		if err := uptimeValidateItem(req, false); err != nil {
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
		data := loadUptime()
		items := uptimeItems(data)
		var item map[string]any
		for _, it := range items {
			if str(it["id"]) == id {
				item = it
			}
		}
		if item == nil {
			return NewHTTPError(404, "监控项不存在")
		}
		result := probeAndAlert(item)
		data["items"] = items
		if err := WriteJSONFile(DataPath("uptime.json"), data); err != nil {
			return err
		}
		item["checked_at"] = item["last_checked_at"]
		return WriteJSON(w, 200, result)
	})
}

// --- 证书到期 ---

func loadCertCheck() map[string]any {
	data := map[string]any{
		"enabled": true, "interval_seconds": 86400, "remind_days": []any{30, 7},
	}
	_ = ReadJSON(DataPath("certcheck.json"), &data)
	if _, ok := data["remind_days"]; !ok {
		// 兼容早期 Go 版本写入的 days_before 单档位配置。
		if legacy := int(int64Of(data["days_before"])); legacy > 0 {
			data["remind_days"] = []any{legacy}
		}
	}
	return data
}

// certParseExpiry 解析证书到期时间；失败返回零值。
func certParseExpiry(raw string) (time.Time, bool) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return time.Time{}, false
	}
	for _, layout := range []string{"2006-01-02T15:04:05", time.RFC3339, "2006-01-02 15:04:05"} {
		if t, err := time.ParseInLocation(layout, raw, time.Local); err == nil {
			return t, true
		}
	}
	return time.Time{}, false
}

// certCheckList 计算每张证书的到期状态（与 Python _cert_status 字段一致）：
// status 取 unknown / ok / warn / expired，expiry 为到期时间文本。
func certCheckList() []map[string]any {
	certs := sslCertsList(loadSSLCerts())
	out := []map[string]any{}
	for _, c := range certs {
		path := str(c["cert_path"])
		expires := str(c["expires_at"])
		if expires == "" && path != "" {
			expires = readCertExpiry(path)
		}
		item := map[string]any{
			"id": c["id"], "name": c["name"], "domains": c["domains"],
			"cert_path": path, "expiry": expires, "expires_at": expires,
			"days_left": nil, "status": "unknown",
		}
		if t, ok := certParseExpiry(expires); ok {
			days := time.Until(t).Hours() / 24
			item["days_left"] = round1(days)
			switch {
			case days < 0:
				item["status"] = "expired"
			case days <= 30:
				item["status"] = "warn"
			default:
				item["status"] = "ok"
			}
		}
		out = append(out, item)
	}
	return out
}

// certRemindDays 读取提醒档位；兼容早期 Go 版本写入的 days_before。
func certRemindDays(cfg map[string]any) []int {
	days := []int{}
	switch raw := cfg["remind_days"].(type) {
	case []any:
		for _, item := range raw {
			if s, ok := item.(string); ok {
				if n := atoiSafe(s); n > 0 {
					days = append(days, n)
				}
				continue
			}
			if n := int(num(item)); n > 0 {
				days = append(days, n)
			}
		}
	case []string:
		for _, s := range raw {
			if n := atoiSafe(s); n > 0 {
				days = append(days, n)
			}
		}
	case []int:
		for _, n := range raw {
			if n > 0 {
				days = append(days, n)
			}
		}
	}
	if len(days) == 0 {
		if legacy := int(int64Of(cfg["days_before"])); legacy > 0 {
			days = []int{legacy}
		}
	}
	if len(days) == 0 {
		days = []int{30, 7}
	}
	sort.Sort(sort.Reverse(sort.IntSlice(days)))
	return days
}

// certCheckOnce 执行一次到期检查：同一证书的同一档位只推送一次（reminded 去重）。
func certCheckOnce() int {
	cfg := loadCertCheck()
	if !boolOfDefault(cfg["enabled"], true) {
		return 0
	}
	remind := certRemindDays(cfg)
	reminded := mapOf(cfg["reminded"])
	if reminded == nil {
		reminded = map[string]any{}
	}
	triggered := 0
	for _, cert := range certCheckList() {
		cid := str(cert["id"])
		status := str(cert["status"])
		if status == "unknown" {
			continue
		}
		done := map[string]bool{}
		for _, lv := range toStringSlice(reminded[cid]) {
			done[lv] = true
		}
		level := ""
		message := ""
		name := defaultString(str(cert["name"]), strings.Join(toStringSlice(cert["domains"]), "/"))
		if status == "expired" {
			if !done["expired"] {
				level = "expired"
				message = "【Graw 证书告警】证书 " + name + " 已于 " + truncateText(str(cert["expiry"]), 10) + " 过期，请立即续期"
			}
		} else {
			days := num(cert["days_left"])
			for _, t := range remind {
				key := itoa(t)
				if days <= float64(t) && !done[key] {
					level = key
					remain := int(days)
					if remain < 0 {
						remain = 0
					}
					message = "【Graw 证书提醒】证书 " + name + " 将于 " + itoa(t) + " 天内到期（" +
						truncateText(str(cert["expiry"]), 10) + "），剩余 " + itoa(remain) + " 天，请及时续期"
					break
				}
			}
		}
		if level == "" {
			continue
		}
		pushAll(message)
		levels := toStringSlice(reminded[cid])
		levels = append(levels, level)
		reminded[cid] = levels
		triggered++
	}
	if triggered > 0 {
		cfg["reminded"] = reminded
		_ = WriteJSONFile(DataPath("certcheck.json"), cfg)
	}
	return triggered
}

func registerCertcheckRoutes(a *App) {
	a.Get("/api/certcheck/status", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		cfg := loadCertCheck()
		certs := certCheckList()
		warn, expired, known := 0, 0, 0
		for _, c := range certs {
			switch str(c["status"]) {
			case "warn":
				warn++
				known++
			case "expired":
				expired++
				known++
			case "ok":
				known++
			}
		}
		return WriteJSON(w, 200, map[string]any{
			"enabled": boolOfDefault(cfg["enabled"], true),
			"interval_seconds": func() int {
				if n := int(int64Of(cfg["interval_seconds"])); n >= 3600 {
					return n
				}
				return 86400
			}(),
			"remind_days": certRemindDays(cfg),
			"cert_count":  known, "warn_count": warn, "expired_count": expired,
		})
	})
	a.Get("/api/certcheck/certs", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"certs": certCheckList()})
	})
	a.Post("/api/certcheck/test", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		n := certCheckOnce()
		return WriteJSON(w, 200, map[string]any{"ok": true, "triggered": n})
	})
	a.Put("/api/certcheck/config", perm("notify"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Enabled         *bool `json:"enabled"`
			IntervalSeconds *int  `json:"interval_seconds"`
			RemindDays      []int `json:"remind_days"`
			DaysBefore      *int  `json:"days_before"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		cfg := loadCertCheck()
		if req.Enabled != nil {
			cfg["enabled"] = *req.Enabled
		}
		if req.IntervalSeconds != nil {
			if *req.IntervalSeconds < 3600 || *req.IntervalSeconds > 604800 {
				return NewHTTPError(400, "检查间隔需在 3600-604800 秒之间")
			}
			cfg["interval_seconds"] = *req.IntervalSeconds
		}
		days := req.RemindDays
		if len(days) == 0 && req.DaysBefore != nil {
			days = []int{*req.DaysBefore}
		}
		if len(days) > 0 {
			cleaned := []int{}
			seen := map[int]bool{}
			for _, d := range days {
				if d > 0 && d <= 3650 && !seen[d] {
					seen[d] = true
					cleaned = append(cleaned, d)
				}
			}
			if len(cleaned) == 0 {
				return NewHTTPError(400, "提醒天数必须为 1-3650 的正整数")
			}
			if len(cleaned) > 10 {
				return NewHTTPError(400, "提醒档位最多 10 个")
			}
			sort.Sort(sort.Reverse(sort.IntSlice(cleaned)))
			cfg["remind_days"] = cleaned
			delete(cfg, "days_before")
		}
		if err := WriteJSONFile(DataPath("certcheck.json"), cfg); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{
			"enabled": boolOfDefault(cfg["enabled"], true), "interval_seconds": int(int64Of(cfg["interval_seconds"])),
			"remind_days": certRemindDays(cfg),
		})
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
		// 前端以 target 字段填写「host:port」或纯端口（host 省略时默认 127.0.0.1）；
		// 兼容早期 Go 版本拆分的 host/port 字段。
		host := "127.0.0.1"
		port := 0
		target := strings.TrimSpace(str(item["target"]))
		if target != "" {
			if strings.Contains(target, ":") {
				if h, p, err := net.SplitHostPort(target); err == nil {
					host, port = h, atoiSafe(p)
				} else if h, p, ok := strings.Cut(target, ":"); ok {
					host, port = strings.TrimSpace(h), atoiSafe(strings.TrimSpace(p))
				}
			} else {
				port = atoiSafe(target)
			}
		} else {
			host = defaultString(str(item["host"]), "127.0.0.1")
			port = int(int64Of(item["port"]))
		}
		if port < 1 || port > 65535 {
			return map[string]any{"status": "down", "detail": "端口号超出范围 1-65535"}
		}
		// SSRF 防护：拒绝云元数据等受保护地址；本机/内网服务监控是正当场景，
		// 默认允许私网与回环，可用 GRAW_SVCMON_ALLOW_PRIVATE_NET=0 收紧。
		if err := AssertSafeHost(host, SvcMonitorAllowPrivateNet(), true); err != nil {
			return map[string]any{"status": "down", "detail": "目标地址非法：" + truncateText(err.Error(), 200)}
		}
		// 远端 bash /dev/tcp 不支持 IPv6 字面量，避免拼出必然失败的脚本。
		if remoteActive() && strings.Contains(host, ":") {
			return map[string]any{"status": "unknown", "detail": "远程节点端口探测暂不支持 IPv6"}
		}
		start := time.Now()
		if remoteActive() {
			// host 已通过 SSRF 校验且字符集受限，可安全拼入远程脚本。
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
			return map[string]any{"status": "down", "detail": truncateText(err.Error(), 200)}
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
		if res.Code == 0 && (out == "active" || out == "activating") {
			return map[string]any{"status": "ok", "detail": "服务状态：" + out}
		}
		if res.Code == 127 {
			return map[string]any{"status": "unknown", "detail": "宿主缺少 systemctl"}
		}
		if out == "" {
			out = "exit " + itoa(res.Code)
		}
		return map[string]any{"status": "down", "detail": "服务未运行（" + out + "）"}
	}
	return map[string]any{"status": "unknown", "detail": "未知监控类型：" + kind}
}

// svcValidateItem 校验监控项（kind 白名单、名称/目标长度、间隔与超时范围）。
func svcValidateItem(req map[string]any, isCreate bool) *HTTPError {
	kind := strings.TrimSpace(str(req["kind"]))
	if kind == "" {
		kind = strings.TrimSpace(str(req["type"]))
	}
	if kind == "" && isCreate {
		return NewHTTPError(400, "监控类型不能为空")
	}
	if kind != "" {
		if kind != "port" && kind != "process" && kind != "service" {
			return NewHTTPError(400, "监控类型仅支持 port/process/service")
		}
		req["kind"] = kind
	}
	if v, ok := req["name"]; ok || isCreate {
		name := strings.TrimSpace(str(v))
		if name == "" || len([]rune(name)) > 64 {
			return NewHTTPError(400, "名称必填且不超过 64 个字符")
		}
		req["name"] = name
	}
	if v, ok := req["target"]; ok || isCreate {
		target := strings.TrimSpace(str(v))
		if target == "" || len([]rune(target)) > 255 {
			return NewHTTPError(400, "监控目标必填且不超过 255 个字符")
		}
		req["target"] = target
	}
	if v, ok := req["timeout_seconds"]; ok || isCreate {
		n := int(int64Of(v))
		if n == 0 {
			n = 5
		}
		if n < 1 || n > 60 {
			return NewHTTPError(400, "超时需在 1-60 秒之间")
		}
		req["timeout_seconds"] = n
	}
	if v, ok := req["interval_seconds"]; ok || isCreate {
		n := int(int64Of(v))
		if n == 0 {
			n = 60
		}
		if n < 10 || n > 86400 {
			return NewHTTPError(400, "探测间隔需在 10-86400 秒之间")
		}
		req["interval_seconds"] = n
	}
	if _, ok := req["enabled"]; !ok && isCreate {
		req["enabled"] = true
	}
	return nil
}

// probeSvcAndAlert 探测并更新 last_status/last_detail/last_checked_at/down_since/history，
// 状态变化时推送通知（首次探测与 unknown 不推送）。
func probeSvcAndAlert(item map[string]any) map[string]any {
	result := probeSvcItem(item)
	now := time.Now()
	nowISO := now.Format(time.RFC3339)
	prev, hadPrev := item["last_status"]
	current := defaultString(str(result["status"]), "down")
	item["last_status"] = current
	item["last_detail"] = str(result["detail"])
	item["last_checked_at"] = nowISO
	item["last_checked_ts"] = float64(now.Unix())
	if current == "down" && str(prev) != "down" {
		item["down_since"] = nowISO
	} else if current == "ok" {
		item["down_since"] = ""
	}
	history := []any{}
	if arr, ok := item["history"].([]any); ok {
		history = arr
	}
	history = append(history, map[string]any{
		"time": nowISO, "status": current, "detail": str(result["detail"]),
	})
	if len(history) > 10 {
		history = history[len(history)-10:]
	}
	item["history"] = history
	if hadPrev && prev != nil && str(prev) != current && (current == "ok" || current == "down") {
		name := defaultString(str(item["name"]), str(item["id"]))
		kindLabel := map[string]string{"port": "端口", "process": "进程", "service": "服务"}[str(item["kind"])]
		if kindLabel == "" {
			kindLabel = str(item["kind"])
		}
		var message string
		if current == "down" {
			message = "【Graw 服务告警】" + name + "（" + kindLabel + " " + str(item["target"]) + "）异常：" +
				defaultString(str(result["detail"]), "不可用")
		} else {
			message = "【Graw 服务恢复】" + name + "（" + kindLabel + " " + str(item["target"]) + "）已恢复正常：" +
				defaultString(str(result["detail"]), "可用")
		}
		pushAll(message)
	}
	return result
}

func registerSvcMonitorRoutes(a *App) {
	a.Get("/api/svcmonitor/status", perm("svcmonitor"), func(w http.ResponseWriter, r *http.Request) error {
		items := svcItems(loadSvcMonitor())
		up, down, enabled := 0, 0, 0
		for _, it := range items {
			if !boolOf(it["enabled"]) && it["enabled"] != nil {
				continue
			}
			enabled++
			switch str(it["last_status"]) {
			case "ok":
				up++
			case "down":
				down++
			}
		}
		return WriteJSON(w, 200, map[string]any{
			"item_count": len(items), "enabled_count": enabled, "up_count": up, "down_count": down,
		})
	})
	a.Get("/api/svcmonitor/items", perm("svcmonitor"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"items": svcItems(loadSvcMonitor())})
	})
	a.Post("/api/svcmonitor/items", perm("svcmonitor"), func(w http.ResponseWriter, r *http.Request) error {
		var req map[string]any
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if err := svcValidateItem(req, true); err != nil {
			return err
		}
		req["id"] = "svc_" + randomHex(10)
		req["created_at"] = time.Now().Format(time.RFC3339)
		req["last_status"] = nil
		req["last_detail"] = ""
		req["last_checked_at"] = ""
		req["last_checked_ts"] = 0
		req["down_since"] = ""
		req["history"] = []any{}
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
		if err := svcValidateItem(req, false); err != nil {
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
		data := loadSvcMonitor()
		items := svcItems(data)
		var item map[string]any
		for _, it := range items {
			if str(it["id"]) == id {
				item = it
			}
		}
		if item == nil {
			return NewHTTPError(404, "监控项不存在")
		}
		result := probeSvcAndAlert(item)
		data["items"] = items
		if err := WriteJSONFile(DataPath("svcmonitor.json"), data); err != nil {
			return err
		}
		item["checked_at"] = item["last_checked_at"]
		return WriteJSON(w, 200, result)
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
		switch req.Action {
		case "start", "stop", "restart", "enable", "disable":
		default:
			return WriteJSON(w, 200, map[string]any{"ok": false, "status": "unknown", "detail": "不支持的服务动作"})
		}
		res := HostCmd(30*time.Second, "systemctl", req.Action, str(item["target"]))
		if res.Code == 127 {
			return WriteJSON(w, 200, map[string]any{"ok": false, "status": "unknown", "detail": "宿主缺少 systemctl"})
		}
		if res.Code != 0 {
			msg := strings.TrimSpace(res.Stderr + res.Stdout)
			return WriteJSON(w, 200, map[string]any{
				"ok": false, "status": "down",
				"detail": "systemctl " + req.Action + " 失败（exit " + itoa(res.Code) + "）：" + truncateText(msg, 200),
			})
		}
		// 执行成功后回验当前状态与开机自启状态。
		probe := probeSvcItem(item)
		isEnabled := false
		if out := HostCmd(10*time.Second, "systemctl", "is-enabled", str(item["target"])); out.Code == 0 {
			isEnabled = strings.TrimSpace(out.Stdout) == "enabled"
		}
		return WriteJSON(w, 200, map[string]any{
			"ok": true, "status": str(probe["status"]),
			"detail":     "已执行 " + req.Action + "，当前服务状态：" + str(probe["detail"]),
			"is_enabled": isEnabled,
		})
	})
}

// notifyAlertMu 保护「规则 id -> 上次告警时间」冷却表。
var (
	notifyAlertMu   sync.Mutex
	notifyLastAlert = map[string]float64{}
)

// notifyMetricValue 读取资源指标（CPU/内存/磁盘/负载/CPU 温度）。
// 温度取 CPU 类传感器；无传感器时返回 false，由调用方跳过该规则（不产生假告警）。
func notifyMetricValue(metric string) (float64, bool) {
	overview := metricsHub.overview()
	mem := mapOf(overview["memory"])
	storage := mapOf(overview["storage"])
	load := mapOf(overview["load"])
	switch metric {
	case "cpu":
		return num(overview["cpu"]), true
	case "mem":
		return num(mem["percent"]), true
	case "disk":
		return num(storage["percent"]), true
	case "load":
		return num(load["percent"]), true
	case "temp":
		for _, item := range collectTemps() {
			if str(item["kind"]) == "cpu" {
				return num(item["value"]), true
			}
		}
		return 0, false
	}
	return 0, false
}

// notifyCheckOnce 执行一次阈值检查，返回本次触发告警数。
// 规则命中且在冷却期外 -> 写记录 + 推送所有启用渠道（与 Python notify._check_once 一致）。
func notifyCheckOnce() int {
	data := loadNotify()
	if !boolOf(data["enabled"]) {
		return 0
	}
	now := float64(time.Now().Unix())
	cooldown := float64(notifyCooldown(data))
	triggered := 0
	for _, rule := range notifyList(data, "rules") {
		if !boolOf(rule["enabled"]) {
			continue
		}
		metric := str(rule["metric"])
		threshold := num(rule["threshold"])
		value, ok := notifyMetricValue(metric)
		if !ok || value < threshold {
			continue
		}
		id := str(rule["id"])
		notifyAlertMu.Lock()
		last := notifyLastAlert[id]
		if now-last < cooldown {
			notifyAlertMu.Unlock()
			continue
		}
		notifyLastAlert[id] = now
		if len(notifyLastAlert) > 4096 {
			for key, ts := range notifyLastAlert {
				if now-ts > 86400 {
					delete(notifyLastAlert, key)
				}
			}
		}
		notifyAlertMu.Unlock()

		labels := map[string]string{"cpu": "CPU 使用率", "mem": "内存使用率", "disk": "磁盘使用率", "load": "系统负载", "temp": "CPU 温度"}
		units := map[string]string{"temp": "℃"}
		label := defaultString(labels[metric], metric)
		unit := units[metric]
		message := fmt.Sprintf("【Graw 资源告警】%s %.1f%s（阈值 %.0f%s）%s",
			label, value, unit, threshold, unit, time.Now().Format("2006-01-02 15:04:05"))
		sent, failed := pushAll(message)
		appendNotifyLogEntry(map[string]any{
			"metric": metric, "value": round1(value), "threshold": threshold,
			"message": message, "sent_channels": sent, "failed_channels": failed,
		})
		triggered++
	}
	return triggered
}

// StartMonitors 启动可用性 / 服务监控 / 资源阈值 / 证书检查后台协程。
func StartMonitors() {
	// 站点可用性：每 10 秒 tick 一次，只探测「已启用且到达间隔」的项。
	go func() {
		for {
			func() {
				defer func() { _ = recover() }()
				data := loadUptime()
				items := uptimeItems(data)
				now := float64(time.Now().Unix())
				changed := false
				for _, it := range items {
					if !boolOf(it["enabled"]) && it["enabled"] != nil {
						continue
					}
					last := num(it["last_checked_ts"])
					interval := int(int64Of(it["interval_seconds"]))
					if interval < 10 {
						interval = 60
					}
					if now-last < float64(interval) {
						continue
					}
					probeAndAlert(it)
					changed = true
				}
				if changed {
					data["items"] = items
					_ = WriteJSONFile(DataPath("uptime.json"), data)
				}
			}()
			time.Sleep(10 * time.Second)
		}
	}()
	// 服务/端口监控：每 10 秒 tick 一次，调度逻辑同站点可用性。
	go func() {
		for {
			func() {
				defer func() { _ = recover() }()
				data := loadSvcMonitor()
				items := svcItems(data)
				now := float64(time.Now().Unix())
				changed := false
				for _, it := range items {
					if !boolOf(it["enabled"]) && it["enabled"] != nil {
						continue
					}
					last := num(it["last_checked_ts"])
					interval := int(int64Of(it["interval_seconds"]))
					if interval < 10 {
						interval = 60
					}
					if now-last < float64(interval) {
						continue
					}
					probeSvcAndAlert(it)
					changed = true
				}
				if changed {
					data["items"] = items
					_ = WriteJSONFile(DataPath("svcmonitor.json"), data)
				}
			}()
			time.Sleep(10 * time.Second)
		}
	}()
	// 通知中心资源阈值：按配置间隔检查（最小 10 秒）。
	go func() {
		for {
			func() {
				defer func() { _ = recover() }()
				notifyCheckOnce()
			}()
			time.Sleep(time.Duration(notifyInterval(loadNotify())) * time.Second)
		}
	}()
	go func() {
		for {
			func() {
				defer func() { _ = recover() }()
				certCheckOnce()
			}()
			interval := int(int64Of(loadCertCheck()["interval_seconds"]))
			if interval < 3600 {
				interval = 86400
			}
			time.Sleep(time.Duration(interval) * time.Second)
		}
	}()
}
