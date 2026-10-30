package graw

// security_parity_test.go —— SSRF 防护与前端字段兼容的回归用例。
//
// 覆盖 Python 旧版已有、Go 版此前缺失的安全/兼容行为：
//   - 通知 Webhook / 可用性探测 / 网络储存的 SSRF 地址判定；
//   - 内网映射代理剥离面板令牌与重定向地址改写；
//   - 通知渠道脱敏字段（has_password / has_bot_token / has_key）与规则校验；
//   - FRP 配置文件路径穿越防护；
//   - Podman registries.conf 的 TOML 解析与生成。

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestAssertSafeHTTPURLBlocksProtectedTargets(t *testing.T) {
	blocked := []string{
		"http://127.0.0.1/hook",
		"http://[::1]/hook",
		"http://169.254.169.254/latest/meta-data/",
		"http://10.0.0.1/hook",
		"ftp://example.com/hook",
	}
	for _, raw := range blocked {
		if err := AssertSafeHTTPURL(raw, false); err == nil {
			t.Errorf("AssertSafeHTTPURL(%q) 应被拒绝，实际放行", raw)
		}
	}
}

func TestAssertSafeHTTPURLAllowsPrivateWhenExplicit(t *testing.T) {
	// allowPrivate=true 仅放行 RFC1918，回环与云元数据仍必须拒绝。
	if err := AssertSafeHTTPURL("http://10.0.0.1/hook", true); err != nil {
		t.Errorf("允许私网时 http://10.0.0.1 不应被拒绝: %v", err)
	}
	if err := AssertSafeHTTPURL("http://169.254.169.254/hook", true); err == nil {
		t.Fatal("云元数据地址即使允许私网也必须拒绝")
	}
	if err := AssertSafeHTTPURL("http://127.0.0.1/hook", true); err == nil {
		t.Fatal("回环地址默认必须拒绝")
	}
}

func TestAssertSafeHostLoopbackExplicit(t *testing.T) {
	// SMTP 等本机服务允许显式放行回环，但链路本地始终拒绝。
	if err := AssertSafeHost("127.0.0.1:25", true, true); err != nil {
		t.Errorf("显式允许回环时本机 SMTP 不应被拒绝: %v", err)
	}
	if err := AssertSafeHost("169.254.169.254", true, true); err == nil {
		t.Fatal("链路本地/云元数据地址必须始终拒绝")
	}
}

func TestNetmapStripAuthQuery(t *testing.T) {
	got := netmapStripAuthQuery("__graw_token=abc&foo=1&bar=2")
	if strings.Contains(got, "__graw_token") {
		t.Fatalf("鉴权参数不应透传给目标服务，实际 %q", got)
	}
	if !strings.Contains(got, "foo=1") || !strings.Contains(got, "bar=2") {
		t.Fatalf("业务查询参数不应被丢弃，实际 %q", got)
	}
}

func TestNetmapRewriteLocation(t *testing.T) {
	base := "http://127.0.0.1:3000/app"
	got := netmapRewriteLocation("http://127.0.0.1:3000/app/login?next=%2Fhome", base, "/api/netmap/proxy/n1")
	want := "/api/netmap/proxy/n1/login?next=%2Fhome"
	if got != want {
		t.Errorf("绝对 Location 应改写为代理路径，got=%q want=%q", got, want)
	}
	// 相对地址原样放行（浏览器会按代理前缀解析）。
	if got := netmapRewriteLocation("/login", base, "/api/netmap/proxy/n1"); got != "/login" {
		t.Errorf("相对 Location 不应改写，got=%q", got)
	}
}

func TestIsFullAdminRejectsRestrictedAdmin(t *testing.T) {
	full := User{"username": "root", "role": "admin"}
	if !IsFullAdmin(full) {
		t.Error("perms 缺失的管理员应为完整管理员")
	}
	restricted := User{"username": "ops", "role": "admin", "perms": []any{"sites"}}
	if IsFullAdmin(restricted) {
		t.Error("受限管理员不得通过面板自身安全边界（否则可改 perms 提权）")
	}
	if IsFullAdmin(User{"username": "u", "role": "user"}) {
		t.Error("普通用户不是管理员")
	}
}

func TestPublicUserHidesCredentials(t *testing.T) {
	u := User{"username": "admin", "role": "admin", "password": "hash", "otp_secret": "SECRET", "otp_enabled": true}
	pub := PublicUser(u)
	if _, ok := pub["password"]; ok {
		t.Error("/api/auth/me 不应返回密码哈希")
	}
	if _, ok := pub["otp_secret"]; ok {
		t.Error("/api/auth/me 不应返回 TOTP 密钥")
	}
	if pub["otp_enabled"] != true {
		t.Error("脱敏字段应保留 otp_enabled 状态")
	}
}

func TestMaskChannelHidesSecrets(t *testing.T) {
	ch := map[string]any{
		"id": "ch_1", "name": "邮件", "type": "smtp", "enabled": true,
		"config": map[string]any{"host": "smtp.example.com", "password": "secret", "username": "u"},
	}
	masked := maskChannel(ch)
	cfg := mapOf(masked["config"])
	if _, ok := cfg["password"]; ok {
		t.Error("密码原文不应回传前端")
	}
	if cfg["has_password"] != true {
		t.Errorf("应返回 has_password 标记，实际 %+v", cfg)
	}
}

func TestValidateNotifyChannelRejectsSSRFWebhook(t *testing.T) {
	ch := map[string]any{
		"name": "坏 Webhook", "type": "webhook",
		"config": map[string]any{"url": "http://169.254.169.254/latest/meta-data/"},
	}
	if err := validateNotifyChannel(ch); err == nil {
		t.Fatal("指向云元数据的 Webhook 必须被拒绝")
	}
}

func TestValidateNotifyRule(t *testing.T) {
	rule := map[string]any{"metric": "cpu", "threshold": 80}
	if err := validateNotifyRule(rule); err != nil {
		t.Errorf("合法规则不应报错: %v", err)
	}
	bad := map[string]any{"metric": "unknown", "threshold": 80}
	if err := validateNotifyRule(bad); err == nil {
		t.Fatal("未知指标必须被拒绝")
	}
}

func TestUptimeValidateURL(t *testing.T) {
	if _, err := uptimeValidateURL("file:///etc/passwd"); err == nil {
		t.Fatal("非 http(s) 地址必须被拒绝")
	}
	if _, err := uptimeValidateURL("http://127.0.0.1/"); err == nil {
		t.Fatal("回环地址默认必须被拒绝（未设置 GRAW_UPTIME_ALLOW_PRIVATE_NET）")
	}
}

func TestFRPConfigPathTraversalRejected(t *testing.T) {
	blocked := []string{
		"/etc/frp/../passwd",
		"/etc/frp-evil/frps.toml",
		"/tmp/frps.toml",
	}
	for _, p := range blocked {
		if _, err := validateFRPConfigPath(p); err == nil {
			t.Errorf("frp configPath %q 应被拒绝", p)
		}
	}
	if _, err := validateFRPConfigPath("/etc/frp/frps.toml"); err != nil {
		t.Errorf("/etc/frp/frps.toml 应被允许: %v", err)
	}
}

func TestSafeUserPathBlocksSymlinkParentIntoDataDir(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("符号链接测试仅适用于 POSIX 环境")
	}
	data := GetConfig().DataDir
	link := filepath.Join(t.TempDir(), "datalink")
	if err := os.Symlink(data, link); err != nil {
		t.Skipf("无法创建符号链接: %v", err)
	}
	// 目标文件尚不存在：安全的实现必须先解析父目录的真实路径再拦截。
	if _, err := safeUserPath(filepath.Join(link, "secret.key")); err == nil {
		t.Fatal("经符号链接父目录写入 data 目录必须被拒绝")
	}
}

func TestSafeUserPathAllowsNormalPath(t *testing.T) {
	dir := t.TempDir()
	if _, err := safeUserPath(filepath.Join(dir, "hello.txt")); err != nil {
		t.Errorf("普通临时路径不应被拒绝: %v", err)
	}
}

func TestRegistriesTOMLRoundTrip(t *testing.T) {
	content := buildRegistriesTOML([]string{"docker.io"}, []string{"https://mirror.example.com"}, []string{"registry.internal:5000"})
	search, mirrors, private := parseRegistriesTOML(content)
	if len(search) == 0 || search[0] != "docker.io" {
		t.Errorf("search 解析结果异常: %+v", search)
	}
	if len(mirrors) != 1 || mirrors[0] != "https://mirror.example.com" {
		t.Errorf("mirrors 解析结果异常: %+v", mirrors)
	}
	if len(private) != 1 || private[0] != "registry.internal:5000" {
		t.Errorf("private 解析结果异常: %+v", private)
	}
}
