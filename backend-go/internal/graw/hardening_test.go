package graw

// hardening_test.go —— 安全边界回归用例。
//
// 覆盖三类「误操作/注入会直接破坏宿主机或越权」的逻辑：
//   - 文件删除的关键路径守卫（refuseCriticalDelete / hostPathContains）；
//   - Agent 代理前置鉴权所依赖的路由元信息（public/custom/模块权限）；
//   - 工具箱端口扫描的主机白名单，以及 PowerShell 双引号转义。
//
// 这些用例不依赖网络与外部命令，可在任意平台执行。

import (
	"os"
	"path/filepath"
	"testing"
)

// TestRefuseCriticalDeleteBlocksSystemPaths 验证根目录与一级系统目录被拒绝。
func TestRefuseCriticalDeleteBlocksSystemPaths(t *testing.T) {
	blocked := []string{"/", "/etc", "/usr", "/var", "/bin", "/boot"}
	for _, p := range blocked {
		if err := refuseCriticalDelete(p); err == nil {
			t.Errorf("refuseCriticalDelete(%q) 应被拒绝，实际放行", p)
		}
	}
}

// TestRefuseCriticalDeleteAllowsNormalPath 验证普通业务路径不会被误伤。
func TestRefuseCriticalDeleteAllowsNormalPath(t *testing.T) {
	dir := t.TempDir()
	if err := refuseCriticalDelete(filepath.Join(dir, "site", "index.html")); err != nil {
		t.Errorf("普通路径不应被拒绝: %v", err)
	}
}

// TestRefuseCriticalDeleteBlocksDataDirAncestor 验证包含面板数据目录的祖先路径被拒绝。
func TestRefuseCriticalDeleteBlocksDataDirAncestor(t *testing.T) {
	data := GetConfig().DataDir
	parent := filepath.Dir(data)
	if parent == data {
		t.Skip("数据目录已位于文件系统根，无需测试祖先路径")
	}
	if err := refuseCriticalDelete(parent); err == nil {
		t.Errorf("包含 data 目录的父路径 %q 应被拒绝", parent)
	}
}

// TestHostPathContains 验证按路径分段判断祖先关系，避免 /var 匹配到 /varx。
func TestHostPathContains(t *testing.T) {
	root := filepath.Join(string(os.PathSeparator), "srv")
	if !hostPathContains(root, filepath.Join(root, "app")) {
		t.Errorf("%q 应包含 %q", root, filepath.Join(root, "app"))
	}
	if !hostPathContains(root, root) {
		t.Errorf("%q 应包含自身", root)
	}
	if hostPathContains(root, root+"x") {
		t.Errorf("%q 不应包含 %q", root, root+"x")
	}
}

// TestMatchRoutePattern 验证代理鉴权用的模式匹配（含伪静态前缀的误匹配防护）。
func TestMatchRoutePattern(t *testing.T) {
	cases := []struct {
		pattern string
		path    string
		want    bool
	}{
		{"/api/appstore/icons/{app_id}", "/api/appstore/icons/hello", true},
		{"/api/appstore/icons/{app_id}", "/api/appstore/icons", false},
		{"/api/files/{sub...}", "/api/files/a/b", true},
		{"/api/sites/{site_id}", "/api/sitesopts/mode", false},
		{"/api/system/overview", "/api/system/overview", true},
		{"/api/system/overview", "/api/system/overviews", false},
	}
	for _, c := range cases {
		if got := matchRoutePattern(c.pattern, c.path); got != c.want {
			t.Errorf("matchRoutePattern(%q, %q) = %v，期望 %v", c.pattern, c.path, got, c.want)
		}
	}
}

// TestProxyRouteAuthMetadata 验证 public/custom/模块权限被正确登记，
// 这是「Agent 节点下图标与插件令牌不再 401」的前提。
func TestProxyRouteAuthMetadata(t *testing.T) {
	a := NewApp()
	if info := a.routeInfoFor("GET", "/api/appstore/icons/foo"); info == nil || !info.Public {
		t.Errorf("应用图标路由应为 public，实际 %+v", info)
	}
	if info := a.routeInfoFor("GET", "/api/op/me"); info == nil || !info.Custom {
		t.Errorf("/api/op/me 应为 custom 鉴权，实际 %+v", info)
	}
	info := a.routeInfoFor("GET", "/api/files/list")
	if info == nil || len(info.Modules) != 1 || info.Modules[0] != "files" {
		t.Errorf("/api/files/list 应声明 files 模块权限，实际 %+v", info)
	}
}

// TestSessionActiveRejectsEmptySID 验证空 sid 的旧令牌不再被放行。
func TestSessionActiveRejectsEmptySID(t *testing.T) {
	if SessionActive("") {
		t.Fatal("空 sid 不应被视为有效会话")
	}
}

// TestPortScanRejectsInjectedHost 验证端口扫描不会把非法 host 拼进远程脚本。
func TestPortScanRejectsInjectedHost(t *testing.T) {
	_, err := execToolboxTool("port_scan", map[string]any{
		"host":  "127.0.0.1;touch /tmp/pwn",
		"ports": []any{float64(80)},
	})
	if err == nil {
		t.Fatal("非法主机地址应返回错误")
	}
}

// TestPowerShellEscapeDouble 验证 $、反引号与双引号都会被转义。
func TestPowerShellEscapeDouble(t *testing.T) {
	in := "C:\\tmp\\$(calc)`x\"y"
	want := "C:\\tmp\\`$(calc)``x`\"y"
	if got := powershellEscapeDouble(in); got != want {
		t.Errorf("powershellEscapeDouble(%q) = %q，期望 %q", in, got, want)
	}
}
