// Package graw 实现 Graw 面板的 Go 后端。
//
// 本包是 backend/（Python/FastAPI）的完整重构版：接口路径、请求/响应
// 结构与数据文件格式保持兼容，前端无需改动即可切换。
package graw

// 本文件（cfg.go）负责进程级运行期配置与「宿主环境执行层」：
//   - 解析 GRAW_DATA_DIR / GRAW_FRONTEND_DIST / GRAW_MAX_BODY_MB / GRAW_PUBLIC_URL 等环境变量，
//     推导 data、前端产物、指标目录等路径（GetConfig 懒加载，只解析一次）；
//   - 提供宿主机视角与容器内路径的互转（HostPath / UnhostPath / HostVisiblePath），
//     远程 SSH 节点上路径原样透传；
//   - 统一封装外部命令执行（HostCmd / HostShell / HostWhich），本地走 os/exec
//     （容器模式套 chroot HOST_ROOT），远程节点经 SSH；
//   - 维护面板版本号 AppVersion（升级版本时唯一的修改点）。
//
// 安全约束：
//   - data 目录含 SSH/数据库凭据与 JWT 密钥，创建时即收紧为 0700，不要放宽；
//   - 可执行文件名先过 execNameRe 正则（同时是 CodeQL 认可的注入净化点）；
//   - HostCmd / HostCmdInput 以 argv 传参，不经过 shell；HostShell 与远端
//     remoteExec 接收的是 shell 字符串，调用方必须自行 shellQuote 拼接参数。

import (
	"bytes"
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
)

// AppVersion 面板版本号（Go 后端的唯一来源，/api/health 返回该值；
// Python 旧版已冻结，不再要求同步修改）。
const AppVersion = "1.8.1"

// Config 汇总运行期配置（路径、限额、环境变量派生项）。
//
// GetConfig 返回的指针是进程内共享的只读对象，调用方不要修改其字段。
type Config struct {
	DataDir       string // data 目录（users.json / nodes.json 等敏感配置）
	FrontendDist  string // 前端构建产物目录（存在时由本服务托管；空表示不托管）
	HostRoot      string // 容器内宿主机根目录挂载点（HOST_ROOT），空表示本机直跑
	HostDataDir   string // 宿主机上对应 data 目录的路径（GRAW_HOST_DATA，供 docker 读取）
	MaxBodyBytes  int64  // 普通请求体上限（默认 16MB）
	MaxUploadByte int64  // multipart 上传上限（默认 2GB）
	ProxyDepth    int    // 可信反向代理层数（TRUSTED_PROXY_DEPTH；0 表示不信任任何代理头）
	OnlineSeconds int64  // 会话在线判定阈值（GRAW_SESSION_ONLINE_SECONDS，默认 7200）
	MetricsDir    string // 指标历史采样目录（data/metrics，NDJSON 按天分文件）
	// PublicURL 是面板对外访问的固定地址（如 https://panel.example.com），
	// 供 OAuth2 回调等必须与第三方登记值一致的场景使用；为空表示按请求头推导。
	PublicURL string
}

// cfgOnce / cfgVal 保证配置只解析一次（sync.Once），
// 避免并发请求重复读环境变量、也避免运行期环境变化导致前后行为不一致。
var (
	cfgOnce sync.Once
	cfgVal  *Config
)

// GetConfig 返回进程级配置（首次调用时解析环境变量与目录）。
//
// 并发安全：由 sync.Once 串行化首次初始化；返回值为只读共享对象。
func GetConfig() *Config {
	cfgOnce.Do(func() { cfgVal = loadConfig() })
	return cfgVal
}

// loadConfig 解析环境变量并推导运行期配置（仅由 GetConfig 调用一次）。
//
// 目录解析优先级：
//   - data：GRAW_DATA_DIR > ../backend/data（与 Python 旧版共享账号/节点/密钥）
//     > ./data；
//   - 前端产物：GRAW_FRONTEND_DIST > ../frontend/dist > ./frontend/dist，
//     都找不到时留空，表示本进程不托管静态资源（纯 API 部署）。
//
// 失败行为：本函数不返回 error；目录绝对化/创建失败被忽略，
// 交由后续真正的读写操作暴露（此时报错信息里带有实际路径，更好定位）。
func loadConfig() *Config {
	dataDir := strings.TrimSpace(os.Getenv("GRAW_DATA_DIR"))
	if dataDir == "" {
		// 优先复用 Python 版的 backend/data，便于两者共享账号/节点/密钥；
		// 找不到时退回当前目录下的 data（独立部署场景）。
		candidates := []string{
			filepath.Join("..", "backend", "data"),
			"data",
		}
		// 按顺序取第一个已存在的目录，候选顺序即优先级。
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
	// 统一转绝对路径：后端进程的 cwd 可能被启动脚本改变，相对路径会漂移。
	dataDir, _ = filepath.Abs(dataDir)
	// 提前建目录并直接给 0700：内含 SSH/数据库凭据与 JWT 密钥，不能等写入时再放宽。
	_ = os.MkdirAll(dataDir, 0o700)

	frontend := strings.TrimSpace(os.Getenv("GRAW_FRONTEND_DIST"))
	if frontend == "" {
		// 兼容两种启动方式：在 backend-go/ 下 go run（../frontend/dist），
		// 或在仓库根目录运行二进制（frontend/dist）。
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

	// 解析失败即 0：表示不信任任何反向代理头，按直连对端 IP 取客户端地址。
	proxyDepth, _ := strconv.Atoi(strings.TrimSpace(os.Getenv("TRUSTED_PROXY_DEPTH")))
	online, _ := strconv.ParseInt(strings.TrimSpace(os.Getenv("GRAW_SESSION_ONLINE_SECONDS")), 10, 64)
	if online <= 0 {
		// 默认 2 小时：与会话在线判定的既有口径保持一致，避免首页把正常会话标成离线。
		online = 2 * 3600
	}

	// 面板对外地址：配置后 OAuth 回调等场景不再依赖可被伪造的 Host 头。
	// 去掉尾部斜杠便于直接拼接路径；非 http(s) 的非法值忽略，退回按请求头推导。
	publicURL := strings.TrimRight(strings.TrimSpace(os.Getenv("GRAW_PUBLIC_URL")), "/")
	if publicURL != "" && !strings.HasPrefix(publicURL, "http://") && !strings.HasPrefix(publicURL, "https://") {
		publicURL = ""
	}

	return &Config{
		DataDir:       dataDir,
		FrontendDist:  frontend,
		HostRoot:      strings.TrimRight(strings.TrimSpace(os.Getenv("HOST_ROOT")), "/\\"),
		HostDataDir:   strings.TrimRight(strings.TrimSpace(os.Getenv("GRAW_HOST_DATA")), "/\\"),
		MaxBodyBytes:  envMB("GRAW_MAX_BODY_MB", 16),     // 普通 JSON 请求默认 16MB
		MaxUploadByte: envMB("GRAW_MAX_UPLOAD_MB", 2048), // 文件上传默认 2GB
		ProxyDepth:    proxyDepth,
		OnlineSeconds: online,
		MetricsDir:    filepath.Join(dataDir, "metrics"),
		PublicURL:     publicURL,
	}
}

// envMB 读取以 MB 为单位的环境变量并换算为字节数。
//
// 未设置、解析失败或小于 1 时返回 def*1MiB；该函数只用于请求体/上传限额，
// 因此不做错误返回，坏值静默回退到安全默认值。
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
//
// 注意：本函数只做拼接，不做路径净化；parts 若来自用户输入，调用方需先校验。
func DataPath(parts ...string) string {
	return filepath.Join(append([]string{GetConfig().DataDir}, parts...)...)
}

// HostPath 把「宿主机视角」的绝对路径映射为容器内可访问路径。
// 远程 SSH 节点：路径即远端绝对路径，原样返回。
//
// cleanHostView 是路径穿越净化点（以根目录为锚点做 Clean），不要换成别的归一化方式。
func HostPath(p string) string {
	clean := cleanHostView(p)
	if remoteActive() {
		// 远程节点没有「容器内挂载」概念，路径本身就是远端绝对路径。
		return clean
	}
	root := GetConfig().HostRoot
	if root == "" {
		// 非容器部署：宿主机视角路径可直接使用。
		return clean
	}
	if clean == "/" {
		// 避免拼出 "//" 或 "root/" 之外的形态。
		return root
	}
	return root + clean
}

// UnhostPath 把容器内实际路径还原为宿主机视角路径（供前端展示）。
//
// 路径不在 HOST_ROOT 之下（例如面板自读 data 目录）时原样返回，不做猜测。
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
//
// 用于把「容器内看到的文件」交给宿主机上的 docker/certbot 等进程使用。
// 未配置 HOST_ROOT 或 GRAW_HOST_DATA 时退化为 HostPath；
// 路径不在 data 目录内（含 ../ 越界、绝对路径）则原样返回，避免误映射。
func HostVisiblePath(containerPath string) string {
	cfg := GetConfig()
	hostData := cfg.HostDataDir
	if cfg.HostRoot == "" || hostData == "" {
		return HostPath(containerPath)
	}
	rel, err := filepath.Rel(cfg.DataDir, containerPath)
	if err != nil || strings.HasPrefix(rel, "..") || filepath.IsAbs(rel) {
		// 不在 data 目录下：无法确定宿主机位置，原样返回让调用方自行处理。
		return containerPath
	}
	return hostData + "/" + strings.ReplaceAll(rel, "\\", "/")
}

// CmdResult 保存一次外部命令执行结果。
//
// Code 约定：0 成功；>0 为进程真实退出码；127 可执行文件不存在或文件名非法；
// 126 远端命令含非法字符；124 远程命令超时；255 SSH 连接/会话失败。
type CmdResult struct {
	Stdout string // 标准输出（未裁剪，需要时用 TrimCommandOutput）
	Stderr string // 标准错误（未裁剪，可直接回传前端便于排查）
	Code   int    // 退出码，见类型注释
	Err    error  // 原始错误（超时/连接失败等）；非 nil 时 Code 仍可能有意义
}

// HostCmd 在宿主机环境执行命令（容器模式经 chroot HOST_ROOT；远程节点经 SSH）。
//
// name 与 args 以 argv 形式传递，不经过 shell，因此参数里的空格/引号无需转义。
// timeout <= 0 时按 60 秒处理；超时后子进程会被杀掉。
func HostCmd(timeout time.Duration, name string, args ...string) CmdResult {
	if remoteActive() {
		return remoteNodeCmd(currentNode(), timeout, name, args...)
	}
	cmdArgs := append([]string{name}, args...)
	if GetConfig().HostRoot != "" {
		// 容器模式：先 chroot 到宿主机根，再执行宿主命令（argv 直传）。
		cmdArgs = append([]string{"chroot", GetConfig().HostRoot}, cmdArgs...)
	}
	return runCommand(timeout, cmdArgs[0], cmdArgs[1:]...)
}

// HostShell 以 shell 形式在宿主机环境执行命令字符串。
//
// script 是 shell 字符串，本函数不做任何转义；拼接外部输入时调用方必须先 shellQuote，
// 否则会形成命令注入。Windows 本机改用 PowerShell（chroot/远端路径仍走 sh）。
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
//
// 远端节点用 `command -v` 探测；容器模式不查 PATH（容器 PATH 与宿主不同），
// 而是按固定 sbin/bin 目录拼 HOST_ROOT 检查文件是否存在。
func HostWhich(name string) string {
	if remoteActive() {
		// 远端探测失败与「命令不存在」都归为空串，调用方按「不可用」处理。
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
	// 探测顺序与常见发行版的 PATH 顺序一致，先 sbin 后 bin。
	for _, base := range []string{"/usr/sbin", "/usr/bin", "/sbin", "/bin"} {
		p := root + base + "/" + name
		if st, err := os.Stat(p); err == nil && !st.IsDir() {
			return p
		}
	}
	return ""
}

// runCommand 在本地执行命令（不写 stdin），等价于 runCommandInput 的空输入版本。
func runCommand(timeout time.Duration, name string, args ...string) CmdResult {
	return runCommandInput(timeout, "", name, args...)
}

// HostCmdInput 在宿主机环境执行命令并向 stdin 写入内容。
//
// 远端节点没有 argv 通道，只能逐参数 shellQuote 后拼成一条命令；
// input 可能含密钥/脚本文本，调用方不要把它打进日志。
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
		// 容器模式同样先 chroot 到宿主机根。
		cmdArgs = append([]string{"chroot", GetConfig().HostRoot}, cmdArgs...)
	}
	return runCommandInput(timeout, input, cmdArgs[0], cmdArgs[1:]...)
}

// execNameRe 限制可执行文件名不含 NUL/换行等控制字符（CodeQL 认可的正则守卫，
// 同时防止畸形配置把换行注入到命令参数里）。
//
// 允许空格等普通字符，因此不能把它当「安全命令白名单」使用。
var execNameRe = regexp.MustCompile(`^[^\x00\r\n]+$`)

// runCommandEnv 以额外环境变量执行命令（如 mc 的 MC_HOST_<alias>）。
//
// 与 runCommandInput 的差异：原始 error 单独返回，便于调用方区分
// 「命令不存在」(Code=127) 与「命令执行了但非零退出」。
func runCommandEnv(timeout time.Duration, env []string, name string, args ...string) (CmdResult, error) {
	if !execNameRe.MatchString(name) {
		// 注入净化点：调用方传入的名称可能来自配置，先拒绝控制字符。
		return CmdResult{Code: 127, Stderr: "非法的可执行文件名"}, errors.New("非法的可执行文件名")
	}
	if timeout <= 0 {
		// 统一兜底 60 秒，避免配置缺失时命令无限挂住。
		timeout = 60 * time.Second
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, name, args...)
	cmd.Env = append(os.Environ(), env...) // 继承服务进程环境，再追加调用方变量
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	res := CmdResult{Stdout: stdout.String(), Stderr: stderr.String()}
	if err != nil {
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			res.Code = ee.ExitCode() // 命令确实执行了，取真实退出码
		} else {
			res.Code = 127 // 多为 ENOENT（命令不存在）等启动失败
		}
		return res, err
	}
	return res, nil
}

// runCommandInput 在本地以 argv 执行命令，可选写入 stdin；带超时与退出码归一化。
//
// 失败行为：命令非零退出时 Err 非 nil 且 Code 为真实退出码；
// 超时被 kill 时 Code 通常为 -1，但 Err 会被还原为 context.DeadlineExceeded，
// 调用方判断超时应看 Err 而不是 Code。
func runCommandInput(timeout time.Duration, input, name string, args ...string) CmdResult {
	if !execNameRe.MatchString(name) {
		return CmdResult{Code: 127, Stderr: "非法的可执行文件名"}
	}
	if timeout <= 0 {
		timeout = 60 * time.Second
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, name, args...)
	if input != "" {
		// 只有非空才挂 stdin，避免子进程等待永远不会到来的输入而阻塞。
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
		// 超时被 kill 时 Run 返回的是 signal 错误，这里换成可判定的超时错误。
		res.Err = ctx.Err()
	}
	return res
}

// isWindows 判断本进程是否运行在 Windows（以路径分隔符判定，不引入额外依赖）。
func isWindows() bool {
	return os.PathSeparator == '\\'
}

// TrimCommandOutput 去掉命令输出首尾空白（含 \r\n），便于直接比较/展示。
func TrimCommandOutput(s string) string {
	return strings.TrimSpace(s)
}
