package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"graw-go/internal/graw"
)

// 集成测试：离线重置密码 → 启动真实 HTTP 服务 → 用新密码登录并访问受保护接口。
// TestMain 在包内任何 GetConfig 调用之前固定独立的 GRAW_DATA_DIR，避免污染真实数据。
func TestMain(m *testing.M) {
	dir, err := os.MkdirTemp("", "graw-go-integration-*")
	if err != nil {
		panic(err)
	}
	_ = os.Setenv("GRAW_DATA_DIR", dir)
	// 避免测试机上的 HOST_ROOT / 远端节点影响本机路径语义
	_ = os.Unsetenv("HOST_ROOT")
	code := m.Run()
	_ = os.RemoveAll(dir)
	os.Exit(code)
}

func postJSON(t *testing.T, url string, body any, token string) (int, map[string]any) {
	t.Helper()
	raw, err := json.Marshal(body)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	req, err := http.NewRequest(http.MethodPost, url, bytes.NewReader(raw))
	if err != nil {
		t.Fatalf("request: %v", err)
	}
	req.Header.Set("Content-Type", "application/json")
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("do: %v", err)
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(resp.Body)
	var out map[string]any
	_ = json.Unmarshal(data, &out)
	return resp.StatusCode, out
}

func TestPasswordResetThenHTTPLogin(t *testing.T) {
	dataDir := os.Getenv("GRAW_DATA_DIR")
	t.Logf("GRAW_DATA_DIR=%s resolved=%s users=%s", dataDir, graw.ResolveDataDir(), graw.UsersFilePath(dataDir))
	_ = os.Remove(graw.UsersFilePath(dataDir))
	if err := graw.SeedDefaultUsers(); err != nil {
		t.Fatalf("SeedDefaultUsers: %v", err)
	}
	if _, err := os.Stat(graw.UsersFilePath(dataDir)); err != nil {
		t.Fatalf("播种后 users.json 不存在: %v（resolved=%s）", err, graw.ResolveDataDir())
	}

	const newPassword = "GoReset#2026"
	if err := graw.ResetUserPasswordInDir(dataDir, "admin", newPassword); err != nil {
		t.Fatalf("ResetUserPasswordInDir: %v", err)
	}

	server := httptest.NewServer(graw.NewApp())
	defer server.Close()
	loginURL := server.URL + "/api/auth/login"

	// 旧默认密码必须失效
	code, _ := postJSON(t, loginURL, map[string]string{
		"username": "admin", "password": graw.DefaultPassword,
	}, "")
	if code != http.StatusUnauthorized {
		t.Fatalf("旧密码登录状态码 = %d，期望 401", code)
	}

	// 新密码登录成功，且不再要求强制改密
	code, body := postJSON(t, loginURL, map[string]string{
		"username": "admin", "password": newPassword,
	}, "")
	if code != http.StatusOK {
		t.Fatalf("新密码登录状态码 = %d，响应 = %v", code, body)
	}
	token, _ := body["token"].(string)
	if token == "" {
		t.Fatalf("登录响应缺少 token: %v", body)
	}
	user, _ := body["user"].(map[string]any)
	if user == nil {
		t.Fatalf("登录响应缺少 user: %v", body)
	}
	if user["must_change_password"] == true {
		t.Fatalf("重置后不应再强制改密: %v", user)
	}

	// 新令牌可以访问受保护接口
	req, _ := http.NewRequest(http.MethodGet, server.URL+"/api/auth/me", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("me: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("/api/auth/me 状态码 = %d", resp.StatusCode)
	}
	meRaw, _ := io.ReadAll(resp.Body)
	if !strings.Contains(string(meRaw), `"admin"`) {
		t.Fatalf("/api/auth/me 响应异常: %s", string(meRaw))
	}

	// 破坏性操作：删除根目录必须被 403 拒绝，而不是真的执行
	code, body = postJSON(t, server.URL+"/api/files/delete", map[string]string{"path": "/"}, token)
	if code != http.StatusForbidden {
		t.Fatalf("删除根目录状态码 = %d，期望 403，响应 = %v", code, body)
	}

	// 2FA 生成密钥必须二次确认密码，防止会话被劫持后直接改绑
	code, _ = postJSON(t, server.URL+"/api/auth/2fa/setup", map[string]string{}, token)
	if code != http.StatusUnprocessableEntity {
		t.Fatalf("无密码调用 2FA setup 状态码 = %d，期望 422", code)
	}

	// 数据文件不得出现明文新密码
	raw, err := os.ReadFile(graw.UsersFilePath(dataDir))
	if err != nil {
		t.Fatalf("读取 users.json: %v", err)
	}
	if strings.Contains(string(raw), newPassword) {
		t.Fatal("users.json 不应保存明文密码")
	}
}
