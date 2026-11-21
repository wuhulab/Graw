package graw

import (
	"net"
	"net/http"
	"strings"
)

// 面板安全设置：授权 IP / 域名绑定 / 密码策略 / 水印 / 标签页锁定。
// 配置存 data/panel_security.json；IP 与域名限制在请求入口统一判定，
// 密码策略在 validatePasswordStrength 中生效，水印与标签页锁由前端读取。

type panelSecurityConfig struct {
	AllowedIPs        []string `json:"allowed_ips"`
	AllowedDomains    []string `json:"allowed_domains"`
	PasswordMinLength int      `json:"password_min_length"`
	PasswordComplex   bool     `json:"password_complexity"`
	Watermark         string   `json:"watermark"`
	TabLock           bool     `json:"tab_lock"`
}

func defaultPanelSecurity() panelSecurityConfig {
	return panelSecurityConfig{PasswordMinLength: 8}
}

func loadPanelSecurity() panelSecurityConfig {
	cfg := defaultPanelSecurity()
	_ = ReadJSON(DataPath("panel_security.json"), &cfg)
	if cfg.PasswordMinLength < 8 {
		cfg.PasswordMinLength = 8
	}
	if cfg.PasswordMinLength > 64 {
		cfg.PasswordMinLength = 64
	}
	return cfg
}

func savePanelSecurity(cfg panelSecurityConfig) error {
	CaptureSnapshot("panel-security", "settings", DataPath("panel_security.json"))
	return WriteJSONFile(DataPath("panel_security.json"), cfg)
}

func ipAllowed(ipStr string, list []string) bool {
	ip := net.ParseIP(strings.TrimSpace(ipStr))
	if ip == nil {
		return false
	}
	// 本机访问始终放行，避免配置失误把管理员锁在面板外。
	if ip.IsLoopback() {
		return true
	}
	for _, item := range list {
		item = strings.TrimSpace(item)
		if item == "" {
			continue
		}
		if strings.Contains(item, "/") {
			if _, cidr, err := net.ParseCIDR(item); err == nil && cidr.Contains(ip) {
				return true
			}
			continue
		}
		if allowed := net.ParseIP(item); allowed != nil && allowed.Equal(ip) {
			return true
		}
	}
	return false
}

func hostAllowed(host string, list []string) bool {
	h := strings.ToLower(strings.TrimSpace(host))
	if i := strings.LastIndex(h, ":"); i > 0 && !strings.Contains(h[i:], "]") {
		h = h[:i]
	}
	h = strings.Trim(h, "[]")
	if h == "localhost" || h == "127.0.0.1" || h == "::1" {
		return true
	}
	for _, item := range list {
		if strings.EqualFold(strings.TrimSpace(item), h) {
			return true
		}
	}
	return false
}

// panelSecurityGate 在请求入口执行 IP / 域名限制；未配置时直接放行。
func panelSecurityGate(r *http.Request) *HTTPError {
	cfg := loadPanelSecurity()
	if len(cfg.AllowedIPs) == 0 && len(cfg.AllowedDomains) == 0 {
		return nil
	}
	// 健康检查用于监控探活，不参与限制，避免授权 IP 变更后监控误报。
	if r.URL.Path == "/api/health" {
		return nil
	}
	if len(cfg.AllowedIPs) > 0 && !ipAllowed(ClientIP(r), cfg.AllowedIPs) {
		return NewHTTPError(403, "当前 IP 不在面板授权列表中")
	}
	if len(cfg.AllowedDomains) > 0 && !hostAllowed(r.Host, cfg.AllowedDomains) {
		return NewHTTPError(403, "当前访问域名不在面板绑定域名列表中")
	}
	return nil
}

func registerPanelSecurityRoutes(a *App) {
	a.Get("/api/panel/security", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"security": loadPanelSecurity()})
	})
	a.Put("/api/panel/security", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		var cfg panelSecurityConfig
		if err := DecodeJSON(r, &cfg); err != nil {
			return err
		}
		if cfg.PasswordMinLength < 8 || cfg.PasswordMinLength > 64 {
			return NewHTTPError(400, "密码最小长度需为 8-64")
		}
		if len(cfg.AllowedIPs) > 100 || len(cfg.AllowedDomains) > 50 {
			return NewHTTPError(400, "授权 IP 最多 100 条，绑定域名最多 50 条")
		}
		for _, item := range cfg.AllowedIPs {
			item = strings.TrimSpace(item)
			if item == "" {
				continue
			}
			if strings.Contains(item, "/") {
				if _, _, err := net.ParseCIDR(item); err != nil {
					return NewHTTPError(400, "授权 IP 格式非法: "+item)
				}
			} else if net.ParseIP(item) == nil {
				return NewHTTPError(400, "授权 IP 格式非法: "+item)
			}
		}
		for _, d := range cfg.AllowedDomains {
			d = strings.TrimSpace(d)
			if d == "" {
				continue
			}
			if strings.ContainsAny(d, " /\\\t\r\n") {
				return NewHTTPError(400, "绑定域名格式非法: "+d)
			}
		}
		cfg.Watermark = strings.TrimSpace(cfg.Watermark)
		if len(cfg.Watermark) > 64 {
			return NewHTTPError(400, "水印文字不能超过 64 字符")
		}
		if err := savePanelSecurity(cfg); err != nil {
			return err
		}
		AuditRecord("更新面板安全设置", CurrentUser(r).Username(), ClientIP(r), "")
		return WriteJSON(w, 200, map[string]any{"ok": true, "security": cfg})
	})
}
