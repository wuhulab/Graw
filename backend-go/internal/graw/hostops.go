package graw

// hostops.go — 「当前节点感知」的宿主操作层：同一套文件/路径/命令原语，按当前节点
// 自动在本地（os + HOST_ROOT chroot）与远程 SSH 节点之间切换。
//
// 为什么需要它：主面板可能正管理一台裸 SSH 子节点（未配 Agent），文件管理、进程、磁盘、
// 日志、终端、防火墙等模块不能只操作本机。本文件把 Python 旧版 node_manager / hostfs 的
// 原语（exists/isdir/isfile/getsize/read_text/write_text/listdir_detail/remove）统一成
// HostXxx 函数，业务代码无需自己判断本地还是远程。
//
// 节点上下文：
//   - 全局当前节点持久化在 data/nodes.json 的 Current（见 agent.go）；
//   - 请求级覆盖由 X-Graw-Node 头触发 httpx.go 的 withNodeScope，把请求包在 nodeOverride
//     里串行执行（nodeScopeMu 防并发串扰）；后台任务用 readNodeScope 只读进入。
//
// 安全边界 / 易踩坑：
//   - 远程命令一律先 shellQuote 转义再拼接；remoteExec 额外拒绝含 NUL 的命令
//     （remoteCmdRe 是 CodeQL 认可的命令注入净化点，别删）。
//   - 远程节点一律按 Linux/POSIX 处理（windowsTarget 返回 false），路径用 path 而非 filepath。
//   - 远程执行没有 chroot：传的是远端绝对路径；只有本地容器部署才需要 HostPath 映射 HOST_ROOT。
//   - 超时用 ssh.Session.Signal(SIGKILL)+Close 兜底，退出码 124 表示超时。

import (
	"bytes"
	"fmt"
	"io"
	"net"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"golang.org/x/crypto/ssh"
)

// HostEntry 目录条目（与 Python listdir_detail 返回结构一致）。
type HostEntry struct {
	Name     string
	IsDir    bool
	Size     int64
	Modified int64
}

// remoteActive 判断当前宿主操作目标是否为远程 SSH 节点（含请求级覆盖后的节点）。
// 所有 HostXxx 原语都用它选择 os 还是 SSH 分支。
func remoteActive() bool {
	return IsRemoteCurrent()
}

// nodeScopeMu 保护 nodeOverride：请求级节点覆盖是进程级变量，必须整段互斥，
// 否则并发请求会互相把操作目标串到别的节点上。
var (
	nodeScopeMu  sync.RWMutex
	nodeOverride atomic.Value // string；原子读写，后台协程读取时无需再加锁也不会与 withNodeScope 竞争
)

// resolvedNodeID 返回当前宿主操作的目标节点：withNodeScope 设置的覆盖值优先，
// 否则用全局当前节点。调用方无需自己判断请求级覆盖。
func resolvedNodeID() string {
	// 用原子读而不是普通变量：后台协程可能在 withNodeScope 持有写锁时调用本函数，
	// 再取 nodeScopeMu 会自锁，不加锁又会构成数据竞争。
	if v, ok := nodeOverride.Load().(string); ok && v != "" {
		return v
	}
	return loadNodeStore().Current
}

// opsNode 返回当前生效的节点结构；节点 ID 不存在时回落到本机，
// 保证调用方永远拿到可用节点而不用到处判空。
func opsNode() map[string]any {
	store := loadNodeStore()
	id := resolvedNodeID()
	if n, ok := store.Nodes[id]; ok {
		return n
	}
	return store.Nodes[localNodeID]
}

// withNodeScope 在指定节点上下文内执行 fn：设置 nodeOverride 后串行执行，
// 结束即恢复旧值（等价于 Python 版 node_manager 的请求级 contextvars）。
// 注意它持有写锁执行 fn，因此 fn 内不能再嵌套 withNodeScope（会自锁）。
func withNodeScope(nodeID string, fn func()) {
	nodeScopeMu.Lock()
	old, _ := nodeOverride.Load().(string)
	nodeOverride.Store(nodeID)
	defer func() {
		// 无论 fn 是否 panic 都恢复旧覆盖，避免节点上下文泄漏到后续请求。
		nodeOverride.Store(old)
		nodeScopeMu.Unlock()
	}()
	fn()
}

// readNodeScope 在只读锁下执行 fn：后台任务 / WS 采集用它进入「不被并发覆盖改动」的
// 节点上下文；不修改 nodeOverride，因此可与其他读请求并发。
func readNodeScope(fn func()) {
	nodeScopeMu.RLock()
	defer nodeScopeMu.RUnlock()
	fn()
}

// windowsTarget 当前管理主机是否为 Windows（远程 SSH 节点一律按 Linux 处理）。
func windowsTarget() bool {
	return isWindows() && !remoteActive()
}

// remoteCmdRe 拒绝 NUL（argv / shell 都无法表示）。
// 该正则守卫同时被 CodeQL 识别为命令注入净化点。
var remoteCmdRe = regexp.MustCompile(`^[^\x00]*$`)

// remoteExec 在 node 上执行一条 shell 命令（可选 stdin），并受 timeout 约束。
// 返回 CmdResult：Code 为远端退出码（126=命令含 NUL，255=连接/会话失败，124=超时）；
// Err 保留底层错误供日志排查；调用方通常只看 Code/Stdout/Stderr。
// node 为 nil 或非 ssh 类型时返回 Code=1。timeout<=0 时取默认 30 秒。
func remoteExec(node map[string]any, timeout time.Duration, command string, stdin []byte) CmdResult {
	// NUL 无法在 argv/shell 中表示，直接拒绝（同时是注入净化点）。
	if !remoteCmdRe.MatchString(command) {
		return CmdResult{Code: 126, Stderr: "非法的命令内容"}
	}
	if node == nil || str(node["type"]) != "ssh" {
		return CmdResult{Code: 1, Stderr: "不是 SSH 节点"}
	}
	// 默认 30 秒，防止调用方误传 0 导致永久阻塞。
	if timeout <= 0 {
		timeout = 30 * time.Second
	}
	// 复用池内连接；SSH 握手超时 15 秒。
	client, err := pooledSSH(node, 15*time.Second)
	if err != nil {
		return CmdResult{Code: 255, Err: err, Stderr: err.Error()}
	}
	// 每次执行开独立 session，可安全并发。
	session, err := client.NewSession()
	if err != nil {
		return CmdResult{Code: 255, Err: err, Stderr: err.Error()}
	}
	defer session.Close()
	var stdout, stderr bytes.Buffer
	session.Stdout = &stdout
	session.Stderr = &stderr
	if stdin != nil {
		// 仅在需要喂数据（如写文件）时绑定 stdin。
		session.Stdin = bytes.NewReader(stdin)
	}
	// Run 放 goroutine，select 实现可中断超时。
	done := make(chan error, 1)
	go func(cmd string) { done <- session.Run(cmd) }(command)
	select {
	case runErr := <-done:
		code := 0
		if runErr != nil {
			code = 1
			var exitErr *ssh.ExitError
			// 退出码按 ssh.ExitError 提取，便于上层区分「命令失败」与「执行失败」。
			if ok := asExitError(runErr, &exitErr); ok {
				code = exitErr.ExitStatus()
			}
		}
		return CmdResult{Stdout: stdout.String(), Stderr: stderr.String(), Code: code, Err: runErr}
	case <-time.After(timeout):
		// 超时：先 SIGKILL 远端进程再关会话，返回 124 并带上已产生的输出。
		_ = session.Signal(ssh.SIGKILL)
		_ = session.Close()
		return CmdResult{Stdout: stdout.String(), Stderr: stderr.String(), Code: 124, Err: fmt.Errorf("远程命令超时")}
	}
}

// asExitError 把 err 断言为 *ssh.ExitError；单独成函数是为了让 select 分支保持简洁。
func asExitError(err error, target **ssh.ExitError) bool {
	if e, ok := err.(*ssh.ExitError); ok {
		*target = e
		return true
	}
	return false
}

// remoteNodeCmd 以 argv 形式在节点执行命令：每个参数（含命令名）都先经 shellQuote 转义，
// 杜绝参数内容被拼进命令语法；命令名允许路径形式（如 /usr/bin/foo）。
func remoteNodeCmd(node map[string]any, timeout time.Duration, name string, args ...string) CmdResult {
	parts := make([]string, 0, len(args)+1)
	parts = append(parts, shellQuote(name))
	for _, a := range args {
		parts = append(parts, shellQuote(a))
	}
	return remoteExec(node, timeout, strings.Join(parts, " "), nil)
}

// remoteTest 执行 test <flag> <path> 并用退出码判断；15 秒超时，失败按 false 处理。
func remoteTest(node map[string]any, flag, p string) bool {
	res := remoteExec(node, 15*time.Second, "test "+flag+" "+shellQuote(p)+" && echo 1 || echo 0", nil)
	return strings.TrimSpace(res.Stdout) == "1"
}

// HostExists 判断当前节点路径是否存在（远程 test -e，本地 os.Stat）。
func HostExists(p string) bool {
	if remoteActive() {
		// 远端路径直接传给 test，不做 HOST_ROOT 映射。
		return remoteTest(currentNode(), "-e", p)
	}
	return Exists(HostPath(p))
}

// HostIsFile 判断当前节点路径是否为文件。
func HostIsFile(p string) bool {
	if remoteActive() {
		return remoteTest(currentNode(), "-f", p)
	}
	st, err := os.Stat(HostPath(p))
	return err == nil && !st.IsDir()
}

// HostIsDir 判断当前节点路径是否为目录。
func HostIsDir(p string) bool {
	if remoteActive() {
		return remoteTest(currentNode(), "-d", p)
	}
	return IsDir(HostPath(p))
}

// HostSize 返回当前节点文件大小（不存在返回 0）。
func HostSize(p string) int64 {
	if remoteActive() {
		// stat 失败时输出 0，避免上游对缺失文件额外判错。
		res := remoteExec(currentNode(), 15*time.Second,
			"stat -c %s "+shellQuote(p)+" 2>/dev/null || echo 0", nil)
		lines := strings.Fields(res.Stdout)
		if len(lines) > 0 {
			// 取最后一行：登录 banner 或 shell 提示可能出现在前面。
			n, _ := strconv.ParseInt(lines[len(lines)-1], 10, 64)
			return n
		}
		return 0
	}
	st, err := os.Stat(HostPath(p))
	if err != nil {
		return 0
	}
	return st.Size()
}

// HostReadBytes 读取当前节点文件内容；失败返回错误（远程错误取远端 stderr）。
func HostReadBytes(p string) ([]byte, error) {
	if remoteActive() {
		// cat 的 stdout 即文件内容，二进制安全（SSH 通道不做换行转换）。
		res := remoteExec(currentNode(), 30*time.Second, "cat "+shellQuote(p)+" 2>/dev/null", nil)
		if res.Code != 0 {
			return nil, fmt.Errorf("%s", strings.TrimSpace(res.Stderr))
		}
		return []byte(res.Stdout), nil
	}
	return os.ReadFile(HostPath(p))
}

// HostWriteBytes 写入当前节点文件（自动创建父目录）；perm 为 0 表示沿用系统默认。
// 远程用 mkdir -p + cat >（数据走 stdin，避免超长命令）；本地先建父目录再写。
func HostWriteBytes(p string, data []byte, perm os.FileMode) error {
	if remoteActive() {
		script := "mkdir -p " + shellQuote(path.Dir(p)) + " && cat > " + shellQuote(p)
		res := remoteExec(currentNode(), 30*time.Second, script, data)
		if res.Code != 0 {
			return fmt.Errorf("%s", strings.TrimSpace(res.Stderr))
		}
		if perm != 0 {
			// 权限单独设置：cat 重定向无法指定 mode；chmod 失败必须上报，
			// 否则调用方会以为 0600 已生效，凭据文件可能仍是宽松权限。
			res = remoteExec(currentNode(), 10*time.Second,
				"chmod "+strconv.FormatInt(int64(perm.Perm()), 8)+" "+shellQuote(p), nil)
			if res.Code != 0 {
				return fmt.Errorf("chmod 失败: %s", strings.TrimSpace(res.Stderr))
			}
		}
		return nil
	}
	real := HostPath(p)
	if err := os.MkdirAll(filepath.Dir(real), 0o755); err != nil {
		return err
	}
	return os.WriteFile(real, data, perm)
}

// HostMkdirAll 创建当前节点目录（远程 mkdir -p，本地 os.MkdirAll）。
func HostMkdirAll(p string, perm os.FileMode) error {
	if remoteActive() {
		res := remoteExec(currentNode(), 15*time.Second, "mkdir -p "+shellQuote(p), nil)
		if res.Code != 0 {
			return fmt.Errorf("%s", strings.TrimSpace(res.Stderr))
		}
		return nil
	}
	return os.MkdirAll(HostPath(p), perm)
}

// criticalDeletePaths 是绝不允许「整体删除」的 POSIX 系统目录（精确匹配，不含子目录）。
// 删除其中的子目录仍然允许；这里拦的是会让整机或系统组件直接消失的目标。
var criticalDeletePaths = []string{
	"/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib32", "/lib64",
	"/libx32", "/media", "/mnt", "/opt", "/proc", "/root", "/run", "/sbin",
	"/srv", "/sys", "/usr", "/var",
}

// windowsCriticalDirs 是被拦截的 Windows 系统目录名（任意盘符下的一级目录）。
var windowsCriticalDirs = []string{
	"windows", "program files", "program files (x86)", "programdata",
	"users", "system volume information",
}

// hostPathEqual 按当前节点路径风格比较两个路径是否相同（Windows 本地忽略大小写）。
func hostPathEqual(a, b string) bool {
	a, b = pathOrFileClean(a), pathOrFileClean(b)
	if isWindows() && !remoteActive() {
		return strings.EqualFold(a, b)
	}
	return a == b
}

// hostPathContains 判断 parent 是否为 child 的祖先目录（含相等）。
// 远程节点按 POSIX 分段比较；本地用 filepath.Rel，避免 "/var" 误匹配 "/varx"。
func hostPathContains(parent, child string) bool {
	parent, child = pathOrFileClean(parent), pathOrFileClean(child)
	if remoteActive() {
		return child == parent || strings.HasPrefix(child, strings.TrimRight(parent, "/")+"/")
	}
	if isWindows() {
		parent, child = strings.ToLower(parent), strings.ToLower(child)
	}
	rel, err := filepath.Rel(parent, child)
	if err != nil {
		return false
	}
	return rel == "." || (rel != ".." && !strings.HasPrefix(rel, ".."+string(os.PathSeparator)))
}

// refuseCriticalDelete 拦截会导致宿主机不可用的删除目标：根目录、系统目录、
// 回收站根目录，以及包含面板 data 目录的祖先路径。
// 文件管理允许管理员操作任意路径，但整机级误删必须无条件拒绝。
func refuseCriticalDelete(view string) error {
	clean := pathOrFileClean(view)
	// hostPathEqual 兼容 Windows 上 "/" 被规范化为 "\" 的情况。
	if clean == "." || clean == "" || hostPathEqual(clean, "/") {
		return NewHTTPError(403, "禁止删除根目录")
	}
	// Windows 盘符根（C:\、D:\）：pathOrFileClean 已按本地规则规范化。
	if vol := filepath.VolumeName(clean); vol != "" &&
		strings.EqualFold(filepath.Clean(clean), filepath.Clean(vol+`\`)) {
		return NewHTTPError(403, "禁止删除磁盘根目录")
	}
	for _, p := range criticalDeletePaths {
		if hostPathEqual(clean, p) {
			return NewHTTPError(403, "禁止删除系统目录："+p)
		}
	}
	if isWindows() && !remoteActive() {
		parent := filepath.Dir(clean)
		if strings.EqualFold(parent, filepath.VolumeName(clean)+`\`) {
			base := strings.ToLower(filepath.Base(clean))
			for _, name := range windowsCriticalDirs {
				if base == name {
					return NewHTTPError(403, "禁止删除系统目录："+clean)
				}
			}
		}
	}
	if hostPathEqual(clean, TrashRoot()) {
		return NewHTTPError(403, "回收站目录请使用「清空回收站」")
	}
	// 包含面板 data 目录的祖先路径一律拒绝（例如删除 /opt 会连带删掉面板数据）。
	if hostPathContains(clean, GetConfig().DataDir) {
		return NewHTTPError(403, "不能删除包含面板数据目录的路径")
	}
	return nil
}

// HostRemove 删除当前节点文件/目录（远程 rm -rf，本地 removeHostPath）。
// 关键路径保护在这里统一兜底，任何调用方都无法删除整机级目录。
func HostRemove(p string) error {
	if err := refuseCriticalDelete(p); err != nil {
		return err
	}
	if remoteActive() {
		res := remoteExec(currentNode(), 60*time.Second, "rm -rf "+shellQuote(p), nil)
		if res.Code != 0 {
			return fmt.Errorf("%s", strings.TrimSpace(res.Stderr))
		}
		return nil
	}
	return removeHostPath(p)
}

// HostRename 重命名当前节点路径（远程 mv，本地 os.Rename）。
func HostRename(src, dst string) error {
	if remoteActive() {
		res := remoteExec(currentNode(), 30*time.Second,
			"mv "+shellQuote(src)+" "+shellQuote(dst), nil)
		if res.Code != 0 {
			return fmt.Errorf("%s", strings.TrimSpace(res.Stderr))
		}
		return nil
	}
	return os.Rename(HostPath(src), HostPath(dst))
}

// HostChmod 修改当前节点权限（远程 chmod，本地 os.Chmod）。
func HostChmod(p string, mode os.FileMode) error {
	if remoteActive() {
		res := remoteExec(currentNode(), 15*time.Second,
			"chmod "+strconv.FormatInt(int64(mode.Perm()), 8)+" "+shellQuote(p), nil)
		if res.Code != 0 {
			return fmt.Errorf("%s", strings.TrimSpace(res.Stderr))
		}
		return nil
	}
	return os.Chmod(HostPath(p), mode)
}

// HostListDir 列出当前节点目录条目（远程单次 find，本地 ReadDir）。
// 远程一次取回 name/type/size/mtime，避免逐条再发 stat。
func HostListDir(p string) ([]HostEntry, error) {
	if remoteActive() {
		// %y 为 d 表示目录；%T@ 为 mtime（秒，含小数），%s 为字节数。
		// 不吞 stderr：find 不支持 -printf（BusyBox）或目录不可读时必须让调用方看到错误，
		// 否则会静默返回空目录，前端表现为「目录里什么都没有」。
		script := "find " + shellQuote(p) + " -mindepth 1 -maxdepth 1 -printf '%f\\t%y\\t%s\\t%T@\\n'"
		res := remoteExec(currentNode(), 30*time.Second, script, nil)
		if res.Code != 0 {
			msg := strings.TrimSpace(res.Stderr)
			if msg == "" {
				msg = "find 执行失败（退出码 " + itoa(res.Code) + "）"
			}
			return nil, fmt.Errorf("%s", msg)
		}
		entries := []HostEntry{}
		for _, line := range strings.Split(res.Stdout, "\n") {
			if line == "" {
				continue
			}
			fields := strings.Split(line, "\t")
			if len(fields) < 4 {
				continue
			}
			size, _ := strconv.ParseInt(fields[2], 10, 64)
			mt, _ := strconv.ParseFloat(fields[3], 64)
			entries = append(entries, HostEntry{
				Name: fields[0], IsDir: fields[1] == "d", Size: size, Modified: int64(mt),
			})
		}
		return entries, nil
	}
	real := HostPath(p)
	items, err := os.ReadDir(real)
	if err != nil {
		return nil, err
	}
	entries := make([]HostEntry, 0, len(items))
	for _, it := range items {
		info, err := it.Info()
		if err != nil {
			// 条目在读取过程中被删/无权限：跳过而不是让整个目录列表失败。
			continue
		}
		entries = append(entries, HostEntry{
			Name: it.Name(), IsDir: it.IsDir(), Size: info.Size(), Modified: info.ModTime().Unix(),
		})
	}
	return entries, nil
}

// HostCopyPath 复制文件/目录到目标（远程 cp -a 保留属性，本地 copyRecursive）。
func HostCopyPath(src, dst string) error {
	if remoteActive() {
		// 600 秒超时：给大目录复制留余量。
		res := remoteExec(currentNode(), 600*time.Second,
			"cp -a "+shellQuote(src)+" "+shellQuote(dst), nil)
		if res.Code != 0 {
			return fmt.Errorf("%s", strings.TrimSpace(res.Stderr))
		}
		return nil
	}
	return copyRecursive(HostPath(src), HostPath(dst))
}

// HostWriteFrom 从流写入当前节点文件（上传大文件不整块驻留内存）。
// 远程把 r 直接接到 SSH 会话 stdin，边读边传；本地 OpenFile + io.Copy。
// perm 为 0 表示不额外 chmod（远程）或沿用系统默认（本地）。
func HostWriteFrom(p string, r io.Reader, perm os.FileMode) error {
	if remoteActive() {
		client, err := pooledSSH(currentNode(), 15*time.Second)
		if err != nil {
			return err
		}
		session, err := client.NewSession()
		if err != nil {
			return err
		}
		defer session.Close()
		// 流式写入：内存占用恒定，不受文件大小影响。
		session.Stdin = r
		cmd := "mkdir -p " + shellQuote(path.Dir(p)) + " && cat > " + shellQuote(p)
		if perm != 0 {
			cmd += " && chmod " + strconv.FormatInt(int64(perm.Perm()), 8) + " " + shellQuote(p)
		}
		return session.Run(cmd)
	}
	real := HostPath(p)
	if err := os.MkdirAll(filepath.Dir(real), 0o755); err != nil {
		return err
	}
	f, err := os.OpenFile(real, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, perm)
	if err != nil {
		return err
	}
	// 先报复制错误再报关闭错误，避免掩盖写入失败。
	_, cpErr := io.Copy(f, r)
	closeErr := f.Close()
	if cpErr != nil {
		return cpErr
	}
	return closeErr
}

// HostCopyTo 把当前节点文件流式写入 w（下载大文件不整块驻留内存）。
// 远程用会话 stdout 管道边读边写，最后 Wait 取远端退出码；本地直接 io.Copy。
func HostCopyTo(p string, w io.Writer) error {
	if remoteActive() {
		client, err := pooledSSH(currentNode(), 15*time.Second)
		if err != nil {
			return err
		}
		session, err := client.NewSession()
		if err != nil {
			return err
		}
		defer session.Close()
		stdout, err := session.StdoutPipe()
		if err != nil {
			return err
		}
		// Start 而非 Run：需要先并行读管道，否则大文件会互相等待。
		if err := session.Start("cat " + shellQuote(p)); err != nil {
			return err
		}
		if _, err := io.Copy(w, stdout); err != nil {
			return err
		}
		// Wait 返回远端退出码（文件不存在 / 无权限会在这里暴露）。
		return session.Wait()
	}
	f, err := os.Open(HostPath(p))
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = io.Copy(w, f)
	return err
}

// HostPathJoin 按当前节点路径风格拼接：远程 POSIX（path.Join），本地按 OS 规则。
func HostPathJoin(parts ...string) string {
	if remoteActive() {
		return path.Join(parts...)
	}
	return filepath.Join(parts...)
}

// HostDir 返回当前节点路径的父目录：远程一律 POSIX 语义。
func HostDir(p string) string {
	if remoteActive() {
		return path.Dir(p)
	}
	return filepath.Dir(p)
}

// HostBase 返回当前节点路径的文件名部分：远程一律 POSIX 语义。
func HostBase(p string) string {
	if remoteActive() {
		return path.Base(p)
	}
	return filepath.Base(p)
}

// HostCleanPath 规范化用户提交的路径：空串视为根；远程把反斜杠归一为正斜杠
// 并强制绝对路径（POSIX），本地回落 filepath.Abs。
func HostCleanPath(p string) string {
	if strings.TrimSpace(p) == "" {
		p = "/"
	}
	if remoteActive() {
		p = strings.ReplaceAll(p, "\\", "/")
		if !strings.HasPrefix(p, "/") {
			p = "/" + p
		}
		return path.Clean(p)
	}
	abs, err := filepath.Abs(p)
	if err != nil {
		return p
	}
	return abs
}

// NodeSSHClient 返回节点的池内 SSH 客户端（供终端等长连接使用）。
// 非 SSH 节点返回错误；返回的连接归连接池所有，调用方不要 Close。
func NodeSSHClient(node map[string]any) (*ssh.Client, error) {
	if node == nil || str(node["type"]) != "ssh" {
		return nil, fmt.Errorf("不是 SSH 节点")
	}
	return pooledSSH(node, 15*time.Second)
}

// NodeAddr 返回节点 host:port（端口缺省 22），仅用于日志/展示，不参与连接身份判断。
func NodeAddr(node map[string]any) string {
	port := int(int64Of(node["port"]))
	if port == 0 {
		port = 22
	}
	return net.JoinHostPort(str(node["host"]), itoa(port))
}
