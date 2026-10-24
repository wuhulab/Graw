// Package graw 实现 Graw 面板的 Go 后端。
//
// 本包是 backend/（Python/FastAPI）的完整重构版：接口路径、请求/响应
// 结构与数据文件格式保持兼容，前端无需改动即可切换。
package graw

import (
	"bytes"
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

// AppVersion 面板版本号（与 Python 后端保持一致，单一来源升级时同步修改）。
const AppVersion = "1.8.1"

// Config 汇总运行期配置（路径、限额、环境变量派生项）。
type Config struct {
	DataDir       string // data 目录（users.json / nodes.json 等敏感配置）
	FrontendDist  string // 前端构建产物目录（存在时由本服务托管）
	HostRoot      string // 容器内宿主机根目录挂载点（HOST_ROOT），空表示本机直跑
	HostDataDir   string // 宿主机上对应 data 目录的路径（GRAW_HOST_DATA）
	MaxBodyBytes  int64  // 普通请求体上限
	MaxUploadByte int64  // multipart 上传上限
	ProxyDepth    int    // 可信反向代理层数（TRUSTED_PROXY_DEPTH）
	OnlineSeconds int64  // 会话在线判定阈值（GRAW_SESSION_ONLINE_SECONDS）
	MetricsDir    string
}

var (
	cfgOnce sync.Once
	cfgVal  *Config
)

// GetConfig 返回进程级配置（首次调用时解析环境变量与目录）。
func GetConfig() *Config {
	cfgOnce.Do(func() { cfgVal = loadConfig() })
	return cfgVal
}

func loadConfig() *Config {
	dataDir := strings.TrimSpace(os.Getenv("GRAW_DATA_DIR"))
	if dataDir == "" {
		// 优先复用 Python 版的 backend/data，便于两者共享账号/节点/密钥；
		// 找不到时退回当前目录下的 data（独立部署场景）。
		candidates := []string{
			filepath.Join("..", "backend", "data"),
			"data",
		}
		for _, c := range candidates {
			if st, err := os.Stat(c); err == nil && st.IsDir() {
				dataDir = c
				break
			}
		}
		if dataDir == "" {
			dataDir = "data"
		}
	}
	dataDir, _ = filepath.Abs(dataDir)
	_ = os.MkdirAll(dataDir, 0o700)

	frontend := strings.TrimSpace(os.Getenv("GRAW_FRONTEND_DIST"))
	if frontend == "" {
		for _, c := range []string{
			filepath.Join("..", "frontend", "dist"),
			filepath.Join("frontend", "dist"),
		} {
			if st, err := os.Stat(c); err == nil && st.IsDir() {
				frontend, _ = filepath.Abs(c)
				break
			}
		}
	}

	proxyDepth, _ := strconv.Atoi(strings.TrimSpace(os.Getenv("TRUSTED_PROXY_DEPTH")))
	online, _ := strconv.ParseInt(strings.TrimSpace(os.Getenv("GRAW_SESSION_ONLINE_SECONDS")), 10, 64)
	if online <= 0 {
		online = 2 * 3600
	}

	return &Config{
		DataDir:       dataDir,
		FrontendDist:  frontend,
		HostRoot:      strings.TrimRight(strings.TrimSpace(os.Getenv("HOST_ROOT")), "/\\"),
		HostDataDir:   strings.TrimRight(strings.TrimSpace(os.Getenv("GRAW_HOST_DATA")), "/\\"),
		MaxBodyBytes:  envMB("GRAW_MAX_BODY_MB", 16),
		MaxUploadByte: envMB("GRAW_MAX_UPLOAD_MB", 2048),
		ProxyDepth:    proxyDepth,
		OnlineSeconds: online,
		MetricsDir:    filepath.Join(dataDir, "metrics"),
	}
}

func envMB(name string, def int64) int64 {
	v := strings.TrimSpace(os.Getenv(name))
	if v == "" {
		return def * 1024 * 1024
	}
	n, err := strconv.ParseInt(v, 10, 64)
	if err != nil || n < 1 {
		return def * 1024 * 1024
	}
	return n * 1024 * 1024
}

// DataPath 返回 data 目录下的文件路径。
func DataPath(parts ...string) string {
	return filepath.Join(append([]string{GetConfig().DataDir}, parts...)...)
}

// HostPath 把「宿主机视角」的绝对路径映射为容器内可访问路径。
// 远程 SSH 节点：路径即远端绝对路径，原样返回。
func HostPath(p string) string {
	if remoteActive() {
		return p
	}
	root := GetConfig().HostRoot
	if root == "" {
		return p
	}
	if p == "" || p == "/" {
		return root
	}
	return root + "/" + strings.TrimLeft(strings.ReplaceAll(p, "\\", "/"), "/")
}

// UnhostPath 把容器内实际路径还原为宿主机视角路径（供前端展示）。
func UnhostPath(p string) string {
	if remoteActive() {
		return p
	}
	root := GetConfig().HostRoot
	if root == "" {
		return p
	}
	if p == root {
		return "/"
	}
	prefix := root + "/"
	if strings.HasPrefix(p, prefix) {
		return "/" + p[len(prefix):]
	}
	return p
}

// HostVisiblePath 把 data 目录内的容器路径映射为宿主机 docker 可读路径。
func HostVisiblePath(containerPath string) string {
	cfg := GetConfig()
	hostData := cfg.HostDataDir
	if cfg.HostRoot == "" || hostData == "" {
		return HostPath(containerPath)
	}
	rel, err := filepath.Rel(cfg.DataDir, containerPath)
	if err != nil || strings.HasPrefix(rel, "..") || filepath.IsAbs(rel) {
		return containerPath
	}
	return hostData + "/" + strings.ReplaceAll(rel, "\\", "/")
}

// CmdResult 保存一次外部命令执行结果。
type CmdResult struct {
	Stdout string
	Stderr string
	Code   int
	Err    error
}

// HostCmd 在宿主机环境执行命令（容器模式经 chroot HOST_ROOT；远程节点经 SSH）。
func HostCmd(timeout time.Duration, name string, args ...string) CmdResult {
	if remoteActive() {
		return remoteNodeCmd(currentNode(), timeout, name, args...)
	}
	cmdArgs := append([]string{name}, args...)
	if GetConfig().HostRoot != "" {
		cmdArgs = append([]string{"chroot", GetConfig().HostRoot}, cmdArgs...)
	}
	return runCommand(timeout, cmdArgs[0], cmdArgs[1:]...)
}

// HostShell 以 shell 形式在宿主机环境执行命令字符串。
func HostShell(timeout time.Duration, script string) CmdResult {
	if remoteActive() {
		return remoteExec(currentNode(), timeout, script, nil)
	}
	if GetConfig().HostRoot != "" {
		return runCommand(timeout, "chroot", GetConfig().HostRoot, "/bin/sh", "-c", script)
	}
	if isWindows() {
		return runCommand(timeout, "powershell", "-NoProfile", "-NonInteractive", "-Command", script)
	}
	return runCommand(timeout, "/bin/sh", "-c", script)
}

// HostWhich 在宿主机环境查找命令绝对路径，返回空串表示不存在。
func HostWhich(name string) string {
	if remoteActive() {
		res := remoteExec(currentNode(), 15*time.Second,
			"command -v "+shellQuote(name)+" || true", nil)
		lines := strings.Split(strings.TrimSpace(res.Stdout), "\n")
		if len(lines) > 0 && strings.TrimSpace(lines[0]) != "" {
			return strings.TrimSpace(lines[0])
		}
		return ""
	}
	root := GetConfig().HostRoot
	if root == "" {
		p, err := exec.LookPath(name)
		if err != nil {
			return ""
		}
		return p
	}
	for _, base := range []string{"/usr/sbin", "/usr/bin", "/sbin", "/bin"} {
		p := root + base + "/" + name
		if st, err := os.Stat(p); err == nil && !st.IsDir() {
			return p
		}
	}
	return ""
}

func runCommand(timeout time.Duration, name string, args ...string) CmdResult {
	return runCommandInput(timeout, "", name, args...)
}

// HostCmdInput 在宿主机环境执行命令并向 stdin 写入内容。
func HostCmdInput(timeout time.Duration, input, name string, args ...string) CmdResult {
	if remoteActive() {
		parts := append([]string{name}, args...)
		quoted := make([]string, 0, len(parts))
		for _, p := range parts {
			quoted = append(quoted, shellQuote(p))
		}
		return remoteExec(currentNode(), timeout, strings.Join(quoted, " "), []byte(input))
	}
	cmdArgs := append([]string{name}, args...)
	if GetConfig().HostRoot != "" {
		cmdArgs = append([]string{"chroot", GetConfig().HostRoot}, cmdArgs...)
	}
	return runCommandInput(timeout, input, cmdArgs[0], cmdArgs[1:]...)
}

// runCommandEnv 以额外环境变量执行命令（如 mc 的 MC_HOST_<alias>）。
func runCommandEnv(timeout time.Duration, env []string, name string, args ...string) (CmdResult, error) {
	if timeout <= 0 {
		timeout = 60 * time.Second
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, name, args...)
	cmd.Env = append(os.Environ(), env...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	res := CmdResult{Stdout: stdout.String(), Stderr: stderr.String()}
	if err != nil {
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			res.Code = ee.ExitCode()
		} else {
			res.Code = 127
		}
		return res, err
	}
	return res, nil
}

func runCommandInput(timeout time.Duration, input, name string, args ...string) CmdResult {
	if timeout <= 0 {
		timeout = 60 * time.Second
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, name, args...)
	if input != "" {
		cmd.Stdin = strings.NewReader(input)
	}
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	res := CmdResult{Stdout: stdout.String(), Stderr: stderr.String(), Code: 0, Err: err}
	if err != nil {
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			res.Code = ee.ExitCode()
		} else {
			res.Code = 127
		}
	}
	if ctx.Err() == context.DeadlineExceeded {
		res.Err = ctx.Err()
	}
	return res
}

func isWindows() bool {
	return os.PathSeparator == '\\'
}

// TrimCommandOutput 去掉命令输出首尾空白。
func TrimCommandOutput(s string) string {
	return strings.TrimSpace(s)
}
