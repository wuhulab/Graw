package graw

// audit.go —— 面板操作审计、登录日志与异常登录提醒。
//
// 两条链路：
//  1. AuditRecord：敏感操作流水写入 data/panel.log（单行文本，超过 5MB 轮转为 panel.log.1），
//     与 Python 旧版共用同一文件；
//  2. RecordLogin：登录成败写入 data/login_logs.json（保留最近 500 条），并与
//     data/login_known.json 里的账号历史 IP/设备指纹比对，首次出现即标记异常并推送通知。
//
// 约束与易踩坑：
//   - 审计写入尽力而为：任何错误/panic 都静默降级，绝不能因为记日志失败而阻断登录或业务；
//   - panel.log 权限 0600、data 目录 0700；不要在这里写入密码、密钥、令牌等凭据；
//   - 日志按空格拼接字段，字段值里的换行可伪造日志行；调用方必须剔除 \r\n
//     （登录流程已处理，CWE-117），新增调用点请沿用；
//   - 异常提醒依赖通知模块注入的 PushAllFunc（为 nil 时跳过），_config.alert_enabled 可关闭。

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
	"unicode/utf8"
)

// 面板操作审计日志（data/panel.log，与 Python 版同一文件）。
var (
	auditMu     sync.Mutex               // 串行化「检查大小 + 轮转 + 追加」，避免并发写乱序
	panelLogMax = int64(5 * 1024 * 1024) // 单文件上限 5MB，超过后轮转为 panel.log.1（只保留一代）
)

// AuditRecord 追加一条操作审计（action/user/ip/detail 均为可选字段）。
// 写入失败静默忽略：审计不能反过来影响业务可用性。
func AuditRecord(action, user, ip, detail string) {
	ts := time.Now().Format("2006-01-02 15:04:05")
	// 固定格式 [时间] [动作] [用户:x] [IP:x] 详情，便于 grep 与 Python 旧版对齐。
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
	// 轮转策略：超限时直接改名覆盖上一代 .1，不删除、不压缩，保证写入路径足够简单。
	if st, err := os.Stat(path); err == nil && st.Size() > panelLogMax {
		_ = os.Rename(path, path+".1")
	}
	// 0600：日志里可能含路径与用户名，不对其他系统用户开放。
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

// LoginLogEntry 一条登录日志（成功与失败都记录，按时间倒序保存在 login_logs.json）。
// Abnormal/AbnormalReason 只在 status=success 时计算：首次出现的 IP 或设备会被标为异常。
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

// KnownUser 账号历史指纹：该账号曾用过的 IP 与设备描述。
// 用于登录后的异常检测，每个列表最多保留 200 条（见 trimList）。
type KnownUser struct {
	IPs     []string `json:"ips"`
	Devices []string `json:"devices"`
}

// KnownData 登录指纹文件（含 _config）。
// 顶层以用户名为 key，另有保留键 "_config" 存放 alert_enabled 等全局开关。
type KnownData map[string]json.RawMessage

var (
	loginLogMu sync.Mutex // 串行化登录日志与指纹文件的「读-改-写」
	// osPatterns / browserPatterns 的顺序即匹配优先级：
	// 更具体的版本号必须排在通用关键字之前（如 Windows NT 10.0 先于 Windows）。
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
		// Edge/Opera 的 UA 里也含 Chrome/，必须先匹配它们再匹配 Chrome。
		{regexp.MustCompile(`Edg/`), "Edge"},
		{regexp.MustCompile(`OPR/|Opera`), "Opera"},
		{regexp.MustCompile(`Chrome/`), "Chrome"},
		{regexp.MustCompile(`Firefox/`), "Firefox"},
		{regexp.MustCompile(`Safari/`), "Safari"},
		{regexp.MustCompile(`curl/|wget/`), "命令行工具"},
		{regexp.MustCompile(`python-requests|PostmanRuntime|axios`), "API 客户端"},
	}
)

// ParseDevice 从 User-Agent 解析可读设备描述（形如 "Chrome · Windows 10/11"）。
// 空 UA 返回「未知设备」；识别失败时保留「未知浏览器 · 未知系统」而不会报错。
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

// randomHex 生成 n 位十六进制随机串（登录日志 ID）。
// 非密码学用途，但同样使用 crypto/rand，避免 ID 可预测导致日志条目被枚举/碰撞。
func randomHex(n int) string {
	b := make([]byte, (n+1)/2)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)[:n]
}

// RecordLogin 写入登录日志并做异常检测；任何异常静默降级。
// status 取 "success"/"failed"；仅 success 才做指纹比对与异常通知。
// 返回值是本次写入的条目（便于调用方即时展示），但即使写盘失败也会返回。
func RecordLogin(username, ip, ua, status, detail string) LoginLogEntry {
	// 审计/通知失败不能影响登录主流程，统一用 recover 兜底。
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
	// 只有成功登录才更新指纹：失败尝试不应污染账号的历史 IP/设备基线。
	if status == "success" && username != "" {
		entry.Abnormal, entry.AbnormalReason = checkAbnormal(username, ip, entry.Device)
	}
	appendLoginLog(entry)
	if entry.Abnormal {
		notifyAbnormal(entry)
	}
	return entry
}

// truncateStr 按字节截断字符串到不超过 n；超长时回退到最近的字符边界，
// 避免把中文/emoji 切成半个字符（JSON 落盘后会变成替换字符）。
func truncateStr(s string, n int) string {
	if len(s) <= n {
		return s
	}
	if n <= 0 {
		return ""
	}
	// RuneStart 判断该字节是否是 UTF-8 字符的首字节；不是就往前退。
	for n > 0 && !utf8.RuneStart(s[n]) {
		n--
	}
	return s[:n]
}

// loadLoginLogs 读取登录日志；文件缺失或损坏返回 nil（调用方按空表处理）。
func loadLoginLogs() []LoginLogEntry {
	var logs []LoginLogEntry
	if err := ReadJSON(DataPath("login_logs.json"), &logs); err != nil {
		return nil
	}
	return logs
}

// appendLoginLog 把新条目插到最前并只保留最近 500 条。
// 落盘失败被忽略：日志丢失不应阻断登录。
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

// loadKnown 读取登录指纹文件，并保证存在 _config 段（默认开启异常提醒）。
// 缺失 _config 时只在内存补默认值，真正落盘发生在 checkAbnormal 写回时。
func loadKnown() map[string]any {
	data := map[string]any{}
	_ = ReadJSON(DataPath("login_known.json"), &data)
	if _, ok := data["_config"]; !ok {
		data["_config"] = map[string]any{"alert_enabled": true}
	}
	return data
}

// checkAbnormal 比对账号历史 IP/设备并写回新指纹，返回是否异常及原因。
// 本函数内部自行加锁 loginLogMu；调用方不要已持有该锁（sync.Mutex 不可重入）。
// 返回原因形如 "新IP / 新设备"，供通知文案直接使用。
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
	// 逐项比对：未出现过的 IP/设备即为异常信号，并立即纳入基线供下次比对。
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

// toStringSlice 把 JSON 反序列化得到的 []any 或已有的 []string 统一成 []string。
// 非字符串元素被丢弃（数据文件可能被手工编辑过）。
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

// trimList 把集合转成排序后的切片，并只保留「字典序最后」的 max 项。
// 注意它不是按最近使用排序（集合已丢失时间信息），只是限制文件体积的近似裁剪。
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
// 返回 (成功数, 失败数)；未注入时为 nil，调用方必须判空。
var PushAllFunc func(message string) (int, int)

// notifyAbnormal 向所有通知渠道推送异常登录提醒。
// 受 login_known.json 的 _config.alert_enabled 控制；未注入通知模块或推送失败时静默跳过。
func notifyAbnormal(entry LoginLogEntry) {
	defer func() { _ = recover() }()
	// 读取开关（不做写入，避免与 checkAbnormal 的写回竞争）。
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
