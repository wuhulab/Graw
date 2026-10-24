package graw

import (
	"net/http"
	"strings"
	"sync"
	"time"
)

// ShunX 安全入口配置（data/shunx.json）。
type shunxConfig struct {
	EntryPath *string `json:"entry_path"`
	Enabled   bool    `json:"enabled"`
}

var shunxMu sync.Mutex

func loadShunx() shunxConfig {
	var cfg shunxConfig
	if err := ReadJSON(DataPath("shunx.json"), &cfg); err != nil {
		return shunxConfig{}
	}
	return cfg
}

// getEntryPath 返回规范化后的入口路径，未配置返回空串。
func getEntryPath() string {
	cfg := loadShunx()
	if cfg.EntryPath == nil {
		return ""
	}
	return strings.Trim(strings.TrimSpace(*cfg.EntryPath), "/")
}

// VerifyEntry 登录前置校验：入口已配置时要求 X-ShunX-Entry 头匹配。
func VerifyEntry(r *http.Request) *HTTPError {
	entry := getEntryPath()
	if entry == "" {
		return nil
	}
	header := strings.ToLower(strings.Trim(strings.TrimSpace(r.Header.Get("X-ShunX-Entry")), "/"))
	if header != strings.ToLower(entry) {
		return NewHTTPError(403, "请通过安全入口访问（当前访问路径不是已配置的安全入口）")
	}
	return nil
}

var (
	shunxStatusMu   sync.Mutex
	shunxStatusHits = map[string][]float64{}
)

func shunxStatusThrottle(r *http.Request) *HTTPError {
	ip := ClientIP(r)
	now := float64(time.Now().Unix())
	shunxStatusMu.Lock()
	hits := shunxStatusHits[ip]
	cut := now - 60
	kept := hits[:0]
	for _, t := range hits {
		if t > cut {
			kept = append(kept, t)
		}
	}
	shunxStatusHits[ip] = kept
	over := len(kept) >= 30
	if !over {
		shunxStatusHits[ip] = append(shunxStatusHits[ip], now)
	}
	shunxStatusMu.Unlock()
	if over {
		return NewHTTPError(429, "请求过于频繁，请稍后再试")
	}
	return nil
}

func registerCoreRoutes(a *App) {
	a.Get("/api/health", public(), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{
			"status": "ok", "name": "Graw", "version": SelfAppVersion(),
		})
	})
	// ShunX 安全入口：status 公开（不泄露入口本身），config 内部自行鉴权。
	a.Get("/api/shunx/status", public(), func(w http.ResponseWriter, r *http.Request) error {
		if err := shunxStatusThrottle(r); err != nil {
			return err
		}
		entry := getEntryPath()
		normalized := strings.ToLower(strings.Trim(strings.TrimSpace(r.URL.Query().Get("path")), "/"))
		return WriteJSON(w, 200, map[string]any{
			"enabled": entry != "",
			"matched": entry != "" && normalized == strings.ToLower(entry),
		})
	})
	a.Get("/api/shunx/config", loginOnly(), func(w http.ResponseWriter, r *http.Request) error {
		u := CurrentUser(r)
		cfg := loadShunx()
		enabled := getEntryPath() != ""
		if u.Role() != "admin" {
			return WriteJSON(w, 200, map[string]any{"entry_path": nil, "enabled": enabled})
		}
		return WriteJSON(w, 200, map[string]any{"entry_path": cfg.EntryPath, "enabled": enabled})
	})
	a.Put("/api/shunx/config", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			EntryPath string `json:"entry_path"`
			Enabled   bool   `json:"enabled"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		entry := strings.Trim(strings.TrimSpace(req.EntryPath), "/")
		if entry != "" {
			for _, ch := range entry {
				if !(ch >= 'a' && ch <= 'z' || ch >= 'A' && ch <= 'Z' || ch >= '0' && ch <= '9' || ch == '_' || ch == '-') {
					return NewHTTPError(400, "入口路径仅允许字母、数字、下划线和中划线")
				}
			}
		}
		shunxMu.Lock()
		var save shunxConfig
		if entry != "" {
			e := entry
			save.EntryPath = &e
		}
		save.Enabled = req.Enabled && entry != ""
		err := WriteJSONFile(DataPath("shunx.json"), save)
		shunxMu.Unlock()
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{
			"ok": true, "config": map[string]any{"entry_path": save.EntryPath, "enabled": save.Enabled},
		})
	})
}
