package graw

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
)

// 面板操作审计日志（data/panel.log，与 Python 版同一文件）。
var (
	auditMu     sync.Mutex
	panelLogMax = int64(5 * 1024 * 1024)
)

func AuditRecord(action, user, ip, detail string) {
	ts := time.Now().Format("2006-01-02 15:04:05")
	parts := []string{"[" + ts + "]", "[" + action + "]"}
	if user != "" {
		parts = append(parts, "[用户:"+user+"]")
	}
	if ip != "" {
		parts = append(parts, "[IP:"+ip+"]")
	}
	if detail != "" {
		parts = append(parts, detail)
	}
	line := strings.Join(parts, " ") + "\n"

	auditMu.Lock()
	defer auditMu.Unlock()
	path := DataPath("panel.log")
	if st, err := os.Stat(path); err == nil && st.Size() > panelLogMax {
		_ = os.Rename(path, path+".1")
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return
	}
	_, _ = f.WriteString(line)
	_ = f.Close()
}

// ---------------------------------------------------------------------------
// 登录日志 / 异常登录提示
// ---------------------------------------------------------------------------

// LoginLogEntry 一条登录日志。
type LoginLogEntry struct {
	ID             string `json:"id"`
	Username       string `json:"username"`
	IP             string `json:"ip"`
	UA             string `json:"ua"`
	Device         string `json:"device"`
	Status         string `json:"status"`
	Abnormal       bool   `json:"abnormal"`
	AbnormalReason string `json:"abnormal_reason"`
	Time           string `json:"time"`
	Detail         string `json:"detail"`
}

// KnownUser 账号历史指纹。
type KnownUser struct {
	IPs     []string `json:"ips"`
	Devices []string `json:"devices"`
}

// KnownData 登录指纹文件（含 _config）。
type KnownData map[string]json.RawMessage

var (
	loginLogMu sync.Mutex
	osPatterns = []struct {
		re   *regexp.Regexp
		name string
	}{
		{regexp.MustCompile(`Windows NT 10\.0`), "Windows 10/11"},
		{regexp.MustCompile(`Windows NT 6\.3`), "Windows 8.1"},
		{regexp.MustCompile(`Windows NT 6\.[12]`), "Windows 7/8"},
		{regexp.MustCompile(`Windows`), "Windows"},
		{regexp.MustCompile(`Android`), "Android"},
		{regexp.MustCompile(`iPhone`), "iPhone"},
		{regexp.MustCompile(`iPad`), "iPad"},
		{regexp.MustCompile(`Mac OS X`), "macOS"},
		{regexp.MustCompile(`Macintosh`), "macOS"},
		{regexp.MustCompile(`Linux`), "Linux"},
		{regexp.MustCompile(`CrOS`), "ChromeOS"},
	}
	browserPatterns = []struct {
		re   *regexp.Regexp
		name string
	}{
		{regexp.MustCompile(`Edg/`), "Edge"},
		{regexp.MustCompile(`OPR/|Opera`), "Opera"},
		{regexp.MustCompile(`Chrome/`), "Chrome"},
		{regexp.MustCompile(`Firefox/`), "Firefox"},
		{regexp.MustCompile(`Safari/`), "Safari"},
		{regexp.MustCompile(`curl/|wget/`), "命令行工具"},
		{regexp.MustCompile(`python-requests|PostmanRuntime|axios`), "API 客户端"},
	}
)

// ParseDevice 从 User-Agent 解析可读设备描述。
func ParseDevice(ua string) string {
	ua = strings.TrimSpace(ua)
	if ua == "" {
		return "未知设备"
	}
	browser, osName := "未知浏览器", "未知系统"
	for _, p := range browserPatterns {
		if p.re.MatchString(ua) {
			browser = p.name
			break
		}
	}
	for _, p := range osPatterns {
		if p.re.MatchString(ua) {
			osName = p.name
			break
		}
	}
	return browser + " · " + osName
}

func randomHex(n int) string {
	b := make([]byte, (n+1)/2)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)[:n]
}

// RecordLogin 写入登录日志并做异常检测；任何异常静默降级。
func RecordLogin(username, ip, ua, status, detail string) LoginLogEntry {
	defer func() { _ = recover() }()
	entry := LoginLogEntry{
		ID:       randomHex(12),
		Username: username,
		IP:       ip,
		UA:       truncateStr(ua, 500),
		Device:   ParseDevice(ua),
		Status:   status,
		Time:     time.Now().Format("2006-01-02 15:04:05"),
		Detail:   truncateStr(detail, 300),
	}
	if status == "success" && username != "" {
		entry.Abnormal, entry.AbnormalReason = checkAbnormal(username, ip, entry.Device)
	}
	appendLoginLog(entry)
	if entry.Abnormal {
		notifyAbnormal(entry)
	}
	return entry
}

func truncateStr(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}

func loadLoginLogs() []LoginLogEntry {
	var logs []LoginLogEntry
	if err := ReadJSON(DataPath("login_logs.json"), &logs); err != nil {
		return nil
	}
	return logs
}

func appendLoginLog(entry LoginLogEntry) {
	loginLogMu.Lock()
	defer loginLogMu.Unlock()
	logs := loadLoginLogs()
	logs = append([]LoginLogEntry{entry}, logs...)
	if len(logs) > 500 {
		logs = logs[:500]
	}
	_ = WriteJSONFile(DataPath("login_logs.json"), logs)
}

func loadKnown() map[string]any {
	data := map[string]any{}
	_ = ReadJSON(DataPath("login_known.json"), &data)
	if _, ok := data["_config"]; !ok {
		data["_config"] = map[string]any{"alert_enabled": true}
	}
	return data
}

func checkAbnormal(username, ip, device string) (bool, string) {
	loginLogMu.Lock()
	defer loginLogMu.Unlock()
	known := loadKnown()
	userKnown, _ := known[username].(map[string]any)
	if userKnown == nil {
		userKnown = map[string]any{"ips": []any{}, "devices": []any{}}
	}
	ips := toStringSlice(userKnown["ips"])
	devices := toStringSlice(userKnown["devices"])
	ipSet := map[string]bool{}
	devSet := map[string]bool{}
	for _, v := range ips {
		ipSet[v] = true
	}
	for _, v := range devices {
		devSet[v] = true
	}
	reasons := []string{}
	if ip != "" && !ipSet[ip] {
		reasons = append(reasons, "新IP")
		ipSet[ip] = true
	}
	if device != "" && !devSet[device] {
		reasons = append(reasons, "新设备")
		devSet[device] = true
	}
	userKnown["ips"] = trimList(ipSet, 200)
	userKnown["devices"] = trimList(devSet, 200)
	known[username] = userKnown
	_ = WriteJSONFile(DataPath("login_known.json"), known)
	if len(reasons) > 0 {
		return true, strings.Join(reasons, " / ")
	}
	return false, ""
}

func toStringSlice(v any) []string {
	out := []string{}
	switch arr := v.(type) {
	case []any:
		for _, item := range arr {
			if s, ok := item.(string); ok {
				out = append(out, s)
			}
		}
	case []string:
		out = append(out, arr...)
	}
	return out
}

func trimList(set map[string]bool, max int) []string {
	list := make([]string, 0, len(set))
	for k := range set {
		list = append(list, k)
	}
	sort.Strings(list)
	if len(list) > max {
		list = list[len(list)-max:]
	}
	return list
}

// PushAllFunc 由通知模块注入：向全部启用渠道推送消息。
var PushAllFunc func(message string) (int, int)

func notifyAbnormal(entry LoginLogEntry) {
	defer func() { _ = recover() }()
	known := loadKnown()
	cfg, _ := known["_config"].(map[string]any)
	if cfg != nil {
		if enabled, ok := cfg["alert_enabled"].(bool); ok && !enabled {
			return
		}
	}
	if PushAllFunc == nil {
		return
	}
	msg := fmt.Sprintf("【Graw 异常登录提醒】账号「%s」于 %s 从 %s（%s）登录面板。\n原因：该账号首次出现在%s。如非本人操作，请立即修改密码！",
		entry.Username, entry.Time, entry.IP, entry.Device, entry.AbnormalReason)
	PushAllFunc(msg)
}
