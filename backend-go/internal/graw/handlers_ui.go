package graw

import (
	"encoding/base64"
	"encoding/json"
	"net/http"
	"regexp"
	"strings"
)

// 界面品牌配置（data/ui.json）。
type UIConfig struct {
	SiteName           string   `json:"site_name"`
	Welcome            string   `json:"welcome"`
	Logo               string   `json:"logo"`
	Background         string   `json:"background"`
	Backgrounds        []string `json:"backgrounds"`
	WallpaperVideo     string   `json:"wallpaper_video"`
	BackgroundMode     string   `json:"background_mode"`
	BackgroundInterval int      `json:"background_interval"`
	RingColor          string   `json:"ring_color"`
	RingAlarm          bool     `json:"ring_alarm"`
}

func defaultUI() UIConfig {
	return UIConfig{
		SiteName: "Graw", Welcome: "", Logo: "", Background: "",
		Backgrounds: []string{}, WallpaperVideo: "", BackgroundMode: "image",
		BackgroundInterval: 8, RingColor: "#409eff", RingAlarm: true,
	}
}

var (
	imageDataURLRe = regexp.MustCompile(`(?i)^data:image/(?:png|jpe?g|gif|webp|svg\+xml);base64,`)
	videoDataURLRe = regexp.MustCompile(`(?i)^data:video/(?:mp4|webm|ogg);base64,`)
	ringColorRe    = regexp.MustCompile(`^#[0-9a-fA-F]{6}$`)
)

const (
	logoMaxBytes      = 2 * 1024 * 1024
	backgroundMaxByte = 8 * 1024 * 1024
	maxBackgrounds    = 12
	videoMaxBytes     = 50 * 1024 * 1024
	publicBgMaxBytes  = 1024 * 1024
)

func loadUIConfig() UIConfig {
	cfg := defaultUI()
	var raw map[string]any
	if err := ReadJSON(DataPath("ui.json"), &raw); err != nil {
		return cfg
	}
	if s, ok := raw["site_name"].(string); ok {
		cfg.SiteName = s
	}
	if s, ok := raw["welcome"].(string); ok {
		cfg.Welcome = s
	}
	if s, ok := raw["logo"].(string); ok {
		cfg.Logo = s
	}
	if s, ok := raw["background"].(string); ok {
		cfg.Background = s
	}
	if arr, ok := raw["backgrounds"].([]any); ok {
		list := []string{}
		for _, item := range arr {
			if s, ok := item.(string); ok && len(list) < maxBackgrounds {
				list = append(list, s)
			}
		}
		cfg.Backgrounds = list
	}
	if s, ok := raw["wallpaper_video"].(string); ok {
		cfg.WallpaperVideo = s
	}
	if s, ok := raw["background_mode"].(string); ok && (s == "image" || s == "video") {
		cfg.BackgroundMode = s
	}
	if n, ok := raw["background_interval"].(json.Number); ok {
		if v, err := n.Int64(); err == nil {
			cfg.BackgroundInterval = clampInterval(int(v))
		}
	} else if f, ok := raw["background_interval"].(float64); ok {
		cfg.BackgroundInterval = clampInterval(int(f))
	}
	if s, ok := raw["ring_color"].(string); ok && ringColorRe.MatchString(s) {
		cfg.RingColor = strings.ToLower(s)
	}
	if b, ok := raw["ring_alarm"].(bool); ok {
		cfg.RingAlarm = b
	}
	if cfg.SiteName == "" {
		cfg.SiteName = "Graw"
	}
	return cfg
}

func clampInterval(v int) int {
	if v < 3 {
		return 3
	}
	if v > 120 {
		return 120
	}
	return v
}

type wallPaperOverride struct {
	Backgrounds        []string `json:"backgrounds"`
	WallpaperVideo     string   `json:"wallpaper_video"`
	BackgroundMode     string   `json:"background_mode"`
	BackgroundInterval int      `json:"background_interval"`
}

type ringOverride struct {
	RingColor string `json:"ring_color"`
	RingAlarm bool   `json:"ring_alarm"`
}

type userUIOverride struct {
	Wallpaper *wallPaperOverride `json:"wallpaper,omitempty"`
	Ring      *ringOverride      `json:"ring,omitempty"`
}

func loadUIOverrides() map[string]userUIOverride {
	out := map[string]userUIOverride{}
	_ = ReadJSON(DataPath("ui_user.json"), &out)
	return out
}

func saveUIOverrides(data map[string]userUIOverride) {
	_ = WriteJSONFile(DataPath("ui_user.json"), data)
}

func validateImageDataURL(value string, maxBytes int, field string) (string, *HTTPError) {
	value = strings.TrimSpace(value)
	if value == "" {
		return "", nil
	}
	if !imageDataURLRe.MatchString(value) {
		return "", NewHTTPError(400, field+" 需为 data:image/*;base64 图片数据")
	}
	parts := strings.SplitN(value, ",", 2)
	if len(parts) != 2 {
		return "", NewHTTPError(400, field+" 不是合法的 Base64 图片数据")
	}
	body := strings.SplitN(parts[1], ";", 2)[0]
	raw, err := base64.StdEncoding.DecodeString(body)
	if err != nil {
		return "", NewHTTPError(400, field+" 不是合法的 Base64 图片数据")
	}
	if len(raw) > maxBytes {
		return "", NewHTTPError(400, field+" 图片过大（最大 "+itoa(maxBytes/(1024*1024))+"MB）")
	}
	return value, nil
}

func validateVideoDataURL(value string) (string, *HTTPError) {
	value = strings.TrimSpace(value)
	if value == "" {
		return "", nil
	}
	if !videoDataURLRe.MatchString(value) {
		return "", NewHTTPError(400, "视频壁纸需为 data:video/(mp4|webm|ogg);base64 数据")
	}
	parts := strings.SplitN(value, ",", 2)
	if len(parts) != 2 {
		return "", NewHTTPError(400, "视频壁纸不是合法的 Base64 数据")
	}
	body := strings.SplitN(parts[1], ";", 2)[0]
	raw, err := base64.StdEncoding.DecodeString(body)
	if err != nil {
		return "", NewHTTPError(400, "视频壁纸不是合法的 Base64 数据")
	}
	if len(raw) > videoMaxBytes {
		return "", NewHTTPError(400, "视频壁纸过大（最大 50MB）")
	}
	return value, nil
}

func effectiveWallpaper(username string, overrides map[string]userUIOverride) map[string]any {
	g := loadUIConfig()
	if acc, ok := overrides[username]; ok && acc.Wallpaper != nil {
		w := acc.Wallpaper
		bgs := w.Backgrounds
		if len(bgs) == 0 {
			bgs = g.Backgrounds
		}
		mode := w.BackgroundMode
		if mode == "" {
			mode = g.BackgroundMode
		}
		interval := w.BackgroundInterval
		if interval == 0 {
			interval = g.BackgroundInterval
		}
		video := w.WallpaperVideo
		if video == "" {
			video = g.WallpaperVideo
		}
		return map[string]any{
			"backgrounds": bgs, "wallpaper_video": video,
			"background_mode": mode, "background_interval": interval,
		}
	}
	return map[string]any{
		"backgrounds": g.Backgrounds, "wallpaper_video": g.WallpaperVideo,
		"background_mode": g.BackgroundMode, "background_interval": g.BackgroundInterval,
	}
}

func effectiveRing(username string, overrides map[string]userUIOverride) map[string]any {
	g := loadUIConfig()
	if acc, ok := overrides[username]; ok && acc.Ring != nil && acc.Ring.RingColor != "" {
		return map[string]any{"ring_color": strings.ToLower(acc.Ring.RingColor), "ring_alarm": acc.Ring.RingAlarm}
	}
	return map[string]any{"ring_color": g.RingColor, "ring_alarm": g.RingAlarm}
}

func registerUIRoutes(a *App) {
	a.Get("/api/ui/public", public(), func(w http.ResponseWriter, r *http.Request) error {
		cfg := loadUIConfig()
		background := cfg.Background
		if background != "" {
			parts := strings.SplitN(background, ",", 2)
			if len(parts) == 2 {
				body := strings.SplitN(parts[1], ";", 2)[0]
				if raw, err := base64.StdEncoding.DecodeString(body); err != nil || len(raw) > publicBgMaxBytes {
					background = ""
				}
			}
		}
		return WriteJSON(w, 200, map[string]any{
			"site_name": cfg.SiteName, "welcome": cfg.Welcome, "logo": cfg.Logo,
			"background": background, "ring_color": cfg.RingColor, "ring_alarm": cfg.RingAlarm,
		})
	})
	a.Get("/api/ui/config", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		u := CurrentUser(r)
		cfg := loadUIConfig()
		overrides := loadUIOverrides()
		acc := overrides[u.Username()]
		var wp any
		var ring any
		if acc.Wallpaper != nil {
			wp = acc.Wallpaper
		}
		if acc.Ring != nil {
			ring = acc.Ring
		}
		resp := uiConfigMap(cfg)
		resp["personal"] = map[string]any{"wallpaper": wp, "ring": ring}
		return WriteJSON(w, 200, resp)
	})
	a.Get("/api/ui/effective", loginOnly(), func(w http.ResponseWriter, r *http.Request) error {
		u := CurrentUser(r)
		overrides := loadUIOverrides()
		resp := effectiveWallpaper(u.Username(), overrides)
		for k, v := range effectiveRing(u.Username(), overrides) {
			resp[k] = v
		}
		return WriteJSON(w, 200, resp)
	})
	a.Put("/api/ui/config", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		u := CurrentUser(r)
		var req struct {
			SiteName           string   `json:"site_name"`
			Welcome            string   `json:"welcome"`
			Logo               string   `json:"logo"`
			Background         string   `json:"background"`
			Backgrounds        []string `json:"backgrounds"`
			WallpaperVideo     string   `json:"wallpaper_video"`
			BackgroundMode     string   `json:"background_mode"`
			BackgroundInterval int      `json:"background_interval"`
			RingColor          string   `json:"ring_color"`
			RingAlarm          bool     `json:"ring_alarm"`
			WallpaperPersonal  bool     `json:"wallpaper_personal"`
			RingPersonal       bool     `json:"ring_personal"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		siteName := strings.TrimSpace(req.SiteName)
		welcome := strings.TrimSpace(req.Welcome)
		logo, err := validateImageDataURL(req.Logo, logoMaxBytes, "Logo")
		if err != nil {
			return err
		}
		background, err := validateImageDataURL(req.Background, backgroundMaxByte, "背景")
		if err != nil {
			return err
		}
		backgrounds := []string{}
		for _, bg := range req.Backgrounds {
			if strings.TrimSpace(bg) == "" {
				continue
			}
			if len(backgrounds) >= maxBackgrounds {
				break
			}
			v, err := validateImageDataURL(bg, backgroundMaxByte, "轮播背景")
			if err != nil {
				return err
			}
			backgrounds = append(backgrounds, v)
		}
		if len(backgrounds) == 0 && background != "" {
			backgrounds = []string{background}
		}
		video, err := validateVideoDataURL(req.WallpaperVideo)
		if err != nil {
			return err
		}
		mode := req.BackgroundMode
		if mode != "image" && mode != "video" {
			mode = "image"
		}
		interval := clampInterval(req.BackgroundInterval)
		ringColor := strings.ToLower(strings.TrimSpace(req.RingColor))
		if !ringColorRe.MatchString(ringColor) {
			return NewHTTPError(400, "环形图颜色需为 #RRGGBB 格式")
		}
		if len(siteName) > 60 {
			return NewHTTPError(400, "网站名过长（最多 60 个字符）")
		}
		if len(welcome) > 200 {
			return NewHTTPError(400, "欢迎语过长（最多 200 个字符）")
		}
		cur := loadUIConfig()
		data := UIConfig{
			SiteName: siteName, Welcome: welcome, Logo: logo, Background: background,
		}
		if req.WallpaperPersonal {
			data.Backgrounds = cur.Backgrounds
			data.WallpaperVideo = cur.WallpaperVideo
			data.BackgroundMode = cur.BackgroundMode
			data.BackgroundInterval = cur.BackgroundInterval
		} else {
			data.Backgrounds = backgrounds
			data.WallpaperVideo = video
			data.BackgroundMode = mode
			data.BackgroundInterval = interval
		}
		if req.RingPersonal {
			data.RingColor = cur.RingColor
			data.RingAlarm = cur.RingAlarm
		} else {
			data.RingColor = ringColor
			data.RingAlarm = req.RingAlarm
		}
		if data.SiteName == "" {
			data.SiteName = "Graw"
		}
		if err := WriteJSONFile(DataPath("ui.json"), data); err != nil {
			return NewHTTPError(500, "保存界面配置失败")
		}
		overrides := loadUIOverrides()
		acc := overrides[u.Username()]
		if req.WallpaperPersonal {
			acc.Wallpaper = &wallPaperOverride{
				Backgrounds: backgrounds, WallpaperVideo: video,
				BackgroundMode: mode, BackgroundInterval: interval,
			}
		} else {
			acc.Wallpaper = nil
		}
		if req.RingPersonal {
			acc.Ring = &ringOverride{RingColor: ringColor, RingAlarm: req.RingAlarm}
		} else {
			acc.Ring = nil
		}
		overrides[u.Username()] = acc
		saveUIOverrides(overrides)
		resp := uiConfigMap(data)
		var wp any
		if acc.Wallpaper != nil {
			wp = acc.Wallpaper
		}
		var ring any
		if acc.Ring != nil {
			ring = acc.Ring
		}
		resp["personal"] = map[string]any{"wallpaper": wp, "ring": ring}
		return WriteJSON(w, 200, resp)
	})
}

func uiConfigMap(c UIConfig) map[string]any {
	return map[string]any{
		"site_name": c.SiteName, "welcome": c.Welcome, "logo": c.Logo,
		"background": c.Background, "backgrounds": c.Backgrounds,
		"wallpaper_video": c.WallpaperVideo, "background_mode": c.BackgroundMode,
		"background_interval": c.BackgroundInterval, "ring_color": c.RingColor,
		"ring_alarm": c.RingAlarm,
	}
}
