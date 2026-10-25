package graw

import (
	"encoding/json"
	"testing"
)

func TestOAuthSettingsRequestDecode(t *testing.T) {
	// 保存配置的请求体使用匿名嵌入结构，这里锁定 JSON 字段能被正确读出，
	// 避免以后改结构时出现「前端传了但后端收不到」的静默故障。
	var req oauthSettingsRequest
	raw := `{"enabled":true,"provider_name":"GitHub","bind_identity":"alice"}`
	if err := json.Unmarshal([]byte(raw), &req); err != nil {
		t.Fatalf("Unmarshal: %v", err)
	}
	if !req.Enabled || req.ProviderName != "GitHub" || req.BindIdentity != "alice" {
		t.Fatalf("嵌入字段解析异常: %+v", req.oauthSettings)
	}
}

func TestOAuthIdentityMatches(t *testing.T) {
	info := map[string]any{
		"login": "Alice",
		"email": "alice@example.com",
		"id":    float64(42),
	}
	cases := []struct {
		identity string
		want     bool
	}{
		{"alice", true},             // 登录名，大小写不敏感
		{"ALICE@EXAMPLE.COM", true}, // 邮箱
		{"42", true},                // 数值 ID（subject 兜底）
		{"bob", false},              // 不匹配
		{"", false},                 // 未配置标识：一律拒绝
	}
	for _, c := range cases {
		if got := oauthIdentityMatches(info, "42", c.identity); got != c.want {
			t.Fatalf("oauthIdentityMatches(%q)=%v, want %v", c.identity, got, c.want)
		}
	}
}

func TestSanitizeOAuthRedirect(t *testing.T) {
	cases := []struct {
		in   string
		want string
	}{
		{"/shunx-abc", "/shunx-abc"},
		{"//evil.com", "/"},
		{"https://evil.com", "/"},
		{"", "/"},
		{"/a/../b", "/a/../b"}, // 仅去查询串，不做额外路径重写
	}
	for _, c := range cases {
		if got := sanitizeOAuthRedirect(c.in); got != c.want {
			t.Fatalf("sanitizeOAuthRedirect(%q)=%q, want %q", c.in, got, c.want)
		}
	}
}

func TestSanitizeOAuthUsername(t *testing.T) {
	cases := []struct {
		raw     string
		subject string
		want    string
	}{
		{"Alice", "1", "Alice"},
		{"a b/c", "2", "a-b-c"},
		{"", "12345", "oauth-12345"},
		{"x", "9", "oauth-9"}, // 太短时用 subject 兜底
	}
	for _, c := range cases {
		if got := sanitizeOAuthUsername(c.raw, c.subject); got != c.want {
			t.Fatalf("sanitizeOAuthUsername(%q,%q)=%q, want %q", c.raw, c.subject, got, c.want)
		}
	}
}

func TestValidateOAuthSettingsBindPair(t *testing.T) {
	c := defaultOAuthSettings()
	c.BindUsername = "admin"
	c.BindIdentity = ""
	if err := validateOAuthSettings(&c); err == nil {
		t.Fatal("只填绑定账号、不填第三方标识时应拒绝保存")
	}
	c.BindIdentity = "alice"
	if err := validateOAuthSettings(&c); err != nil {
		t.Fatalf("成对填写时应通过: %v", err)
	}

	// 启用时缺少必要字段必须报错
	c.Enabled = true
	c.ClientID = ""
	c.ClientSecret = ""
	if err := validateOAuthSettings(&c); err == nil {
		t.Fatal("启用 OAuth2 但缺少 client 凭据时应拒绝保存")
	}
}
