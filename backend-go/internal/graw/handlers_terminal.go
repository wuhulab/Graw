package graw

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"time"

	"golang.org/x/crypto/ssh"
)

// Web 终端：本地 shell / 容器 exec；支持「持久化终端」（连接断开后进程常驻、
// 重连回放最近输出），协议与 Python 版一致（文本帧 = 原始输入输出）。

const (
	heartbeatPing = `{"type":"ping"}`
	heartbeatPong = `{"type":"pong"}`
	resizePrefix  = "\x1bRESIZE:"
)

var containerIDRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`)

type termClient struct {
	id int
	ch chan []byte
}

type termSession struct {
	key        string
	title      string
	cmd        *exec.Cmd
	stdin      io.WriteCloser
	pty        *os.File
	sshSession *ssh.Session
	mu         sync.Mutex
	buf        []byte
	clients    map[int]*termClient
	nextID     int
	alive      bool
	createdAt  float64
	lastActive float64
}

const termBufferLimit = 256 * 1024

var (
	termMu       sync.Mutex
	termSessions = map[string]*termSession{}
)

func termSessionInfo(s *termSession) map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	return map[string]any{
		"key": s.key, "title": s.title, "created_at": s.createdAt,
		"last_active": s.lastActive, "clients": len(s.clients),
		"buffer": len(s.buf), "alive": s.alive,
	}
}

// termSink 把进程输出写入会话缓冲并广播给所有接入客户端。
type termSink struct{ s *termSession }

func (w termSink) Write(p []byte) (int, error) {
	s := w.s
	s.mu.Lock()
	s.buf = append(s.buf, p...)
	if len(s.buf) > termBufferLimit {
		cut := len(s.buf) - termBufferLimit
		for i := cut; i < cut+4096 && i < len(s.buf); i++ {
			if s.buf[i] == '\n' {
				cut = i + 1
				break
			}
		}
		s.buf = s.buf[cut:]
	}
	// 关键：进程 stdout 的 p 由 io.Copy 复用底层缓冲，入队前必须拷贝，
	// 否则客户端稍后读取时内容已被下一次写入覆盖（表现为每帧丢开头字节）。
	chunk := append([]byte(nil), p...)
	for _, c := range s.clients {
		select {
		case c.ch <- chunk:
		default:
			// 客户端消费过慢：丢弃该帧，避免拖住 shell 输出
		}
	}
	s.lastActive = float64(time.Now().Unix())
	s.mu.Unlock()
	return len(p), nil
}

func (s *termSession) writeInput(data []byte) {
	s.mu.Lock()
	alive := s.alive
	s.mu.Unlock()
	if !alive {
		return
	}
	if s.pty != nil {
		_, _ = s.pty.Write(data)
		return
	}
	if runtime.GOOS == "windows" {
		// 管道模式下 Windows 程序通常按 CRLF 识别回车。
		data = bytes.ReplaceAll(data, []byte("\r"), []byte("\r\n"))
	}
	_, _ = s.stdin.Write(data)
}

func (s *termSession) resize(rows, cols int) {
	if s.pty != nil && rows > 0 && cols > 0 {
		termResize(s.pty, rows, cols)
		return
	}
	if s.sshSession != nil && rows > 0 && cols > 0 {
		_ = s.sshSession.WindowChange(rows, cols)
	}
}

func (s *termSession) attach() (int, chan []byte, []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.nextID++
	id := s.nextID
	ch := make(chan []byte, 256)
	s.clients[id] = &termClient{id: id, ch: ch}
	s.lastActive = float64(time.Now().Unix())
	return id, ch, append([]byte(nil), s.buf...)
}

func (s *termSession) detach(id int) {
	s.mu.Lock()
	if c, ok := s.clients[id]; ok {
		delete(s.clients, id)
		close(c.ch)
	}
	s.lastActive = float64(time.Now().Unix())
	s.mu.Unlock()
}

func (s *termSession) markDead() {
	s.mu.Lock()
	s.alive = false
	for id, c := range s.clients {
		close(c.ch)
		delete(s.clients, id)
	}
	s.mu.Unlock()
}

func createTermSession(key, title string, args []string) (*termSession, error) {
	if len(args) == 0 {
		return nil, fmt.Errorf("未找到可用的 shell")
	}
	cmd := exec.Command(args[0], args[1:]...)
	cmd.Env = append(os.Environ(), "TERM=xterm-256color")
	cmd.Dir = "/"
	if runtime.GOOS == "windows" {
		cmd.Dir = os.Getenv("USERPROFILE")
	}
	session := &termSession{
		key: key, title: title, cmd: cmd,
		clients: map[int]*termClient{}, alive: true,
		createdAt: float64(time.Now().Unix()), lastActive: float64(time.Now().Unix()),
	}
	sink := termSink{s: session}
	// 优先使用真正的 PTY（Unix）：交互式 shell、行编辑、TUI 均正常。
	if ptyFile, err := startTermProcess(cmd); err == nil {
		session.pty = ptyFile
		go func() {
			buf := make([]byte, 32768)
			for {
				n, err := ptyFile.Read(buf)
				if n > 0 {
					_, _ = sink.Write(buf[:n])
				}
				if err != nil {
					break
				}
			}
			session.markDead()
		}()
		go func() { _ = cmd.Wait() }()
		return session, nil
	}
	// 回退：管道模式（Windows / 无 PTY 环境），功能受限但不阻断终端窗口。
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	session.stdin = stdin
	cmd.Stdout = sink
	cmd.Stderr = sink
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	go func() {
		_ = cmd.Wait()
		session.markDead()
	}()
	return session, nil
}

func localShellArgs() []string {
	if runtime.GOOS == "windows" {
		// 管道模式下必须使用 `-Command -`，PowerShell 才会把 stdin 当作命令逐行执行。
		if p := HostWhich("pwsh"); p != "" {
			return []string{p, "-NoLogo", "-NoProfile", "-Command", "-"}
		}
		if p := HostWhich("powershell"); p != "" {
			return []string{p, "-NoLogo", "-NoProfile", "-Command", "-"}
		}
		return []string{"cmd.exe", "/Q"}
	}
	shell := strings.TrimSpace(os.Getenv("SHELL"))
	if shell == "" {
		for _, cand := range []string{"/bin/bash", "/bin/sh"} {
			if Exists(cand) {
				shell = cand
				break
			}
		}
	}
	if shell == "" {
		return nil
	}
	return []string{shell}
}

func containerExecArgs(container string) ([]string, error) {
	if !containerIDRe.MatchString(container) {
		return nil, fmt.Errorf("非法的容器标识")
	}
	bin := dockerCLI()
	if bin == "" {
		return nil, fmt.Errorf("未检测到可用的容器引擎")
	}
	return []string{bin, "exec", "-i", container, "/bin/sh"}, nil
}

func getOrCreateTermSession(key, title, node string, persist bool, container string) (*termSession, bool, error) {
	termMu.Lock()
	defer termMu.Unlock()
	if s, ok := termSessions[key]; ok {
		s.mu.Lock()
		alive := s.alive
		s.mu.Unlock()
		if alive {
			return s, false, nil
		}
		delete(termSessions, key)
	}
	var s *termSession
	var err error
	if node != "" && node != "local" {
		n, ok := loadNodeStore().Nodes[node]
		if !ok || str(n["type"]) != "ssh" {
			return nil, false, fmt.Errorf("节点不存在或不是 SSH 节点")
		}
		command := ""
		if container != "" {
			if !containerIDRe.MatchString(container) {
				return nil, false, fmt.Errorf("非法的容器标识")
			}
			bin := remoteDockerBin(n)
			if bin == "" {
				return nil, false, fmt.Errorf("远程节点未检测到可用的容器引擎")
			}
			command = shellQuote(bin) + " exec -it " + shellQuote(container) + " /bin/sh"
		}
		s, err = createSSHTermSession(key, title, n, command)
	} else if container != "" {
		args, e := containerExecArgs(container)
		if e != nil {
			return nil, false, e
		}
		s, err = createTermSession(key, title, args)
	} else {
		s, err = createTermSession(key, title, localShellArgs())
	}
	if err != nil {
		return nil, false, err
	}
	termSessions[key] = s
	return s, true, nil
}

// createSSHTermSession 在裸 SSH 节点开启交互式 PTY shell（或 exec 指定命令）。
func createSSHTermSession(key, title string, node map[string]any, command string) (*termSession, error) {
	client, err := pooledSSH(node, 15*time.Second)
	if err != nil {
		return nil, err
	}
	session, err := client.NewSession()
	if err != nil {
		return nil, err
	}
	stdin, err := session.StdinPipe()
	if err != nil {
		session.Close()
		return nil, err
	}
	stdout, err := session.StdoutPipe()
	if err != nil {
		session.Close()
		return nil, err
	}
	termSessionObj := &termSession{
		key: key, title: title, stdin: stdin, sshSession: session,
		clients: map[int]*termClient{}, alive: true,
		createdAt: float64(time.Now().Unix()), lastActive: float64(time.Now().Unix()),
	}
	sink := termSink{s: termSessionObj}
	session.Stderr = sink
	if err := session.RequestPty("xterm-256color", 24, 80, ssh.TerminalModes{
		ssh.ECHO: 1, ssh.TTY_OP_ISPEED: 14400, ssh.TTY_OP_OSPEED: 14400,
	}); err != nil {
		session.Close()
		return nil, err
	}
	if command == "" {
		err = session.Shell()
	} else {
		err = session.Start(command)
	}
	if err != nil {
		session.Close()
		return nil, err
	}
	go func() {
		_, _ = io.Copy(sink, stdout)
		termSessionObj.markDead()
	}()
	go func() { _ = session.Wait() }()
	return termSessionObj, nil
}

func destroyTermSession(key string) bool {
	termMu.Lock()
	s, ok := termSessions[key]
	if ok {
		delete(termSessions, key)
	}
	termMu.Unlock()
	if !ok {
		return false
	}
	if s.pty != nil {
		_ = s.pty.Close()
	}
	if s.stdin != nil {
		_ = s.stdin.Close()
	}
	if s.sshSession != nil {
		_ = s.sshSession.Close()
	}
	if s.cmd != nil && s.cmd.Process != nil {
		_ = s.cmd.Process.Kill()
	}
	s.markDead()
	return true
}

func registerTerminalRoutes(a *App) {
	a.Get("/api/terminal/mouse-capability", loginOnly(), func(w http.ResponseWriter, r *http.Request) error {
		supported := runtime.GOOS != "windows"
		reason := ""
		if !supported {
			reason = "当前 Windows 的 ConPTY 不支持 TUI 鼠标（需 Windows 11 22H2+ 或 Linux 节点）"
		}
		return WriteJSON(w, 200, map[string]any{"supported": supported, "reason": reason})
	})
	a.Get("/api/terminal/persist", perm("terminal"), func(w http.ResponseWriter, r *http.Request) error {
		termMu.Lock()
		list := []map[string]any{}
		for _, s := range termSessions {
			list = append(list, termSessionInfo(s))
		}
		termMu.Unlock()
		return WriteJSON(w, 200, map[string]any{"sessions": list})
	})
	a.Delete("/api/terminal/persist", perm("terminal"), func(w http.ResponseWriter, r *http.Request) error {
		u := CurrentUser(r)
		node := strings.TrimSpace(r.URL.Query().Get("node"))
		if node == "" {
			node = "local"
		}
		key := node + "|shell"
		destroyed := destroyTermSession(key)
		AuditRecord("关闭持久终端", u.Username(), SessionIP(r), node)
		return WriteJSON(w, 200, map[string]any{"ok": destroyed, "node": node})
	})
	a.wsRoute("/api/terminal/ws/container", containerTerminalWS)
	a.wsRoute("/api/terminal/ws", terminalWS)
}

// SessionIP 在 WS 场景下取客户端地址。
func SessionIP(r *http.Request) string {
	return ClientIP(r)
}

func terminalWS(w http.ResponseWriter, r *http.Request) {
	u := AuthenticateWS(r)
	if u == nil {
		WriteJSON(w, 401, map[string]any{"detail": "未认证"})
		return
	}
	if IsDefaultPassword(u.PasswordHash()) || !HasPerm(u, "terminal") {
		WriteJSON(w, 403, map[string]any{"detail": "无权限"})
		return
	}
	node := strings.TrimSpace(r.URL.Query().Get("node"))
	persist := QueryBool(r, "persist", false)
	if node == "" {
		if remoteActive() {
			node = CurrentNodeID()
		} else {
			node = "local"
		}
	}
	// 远端 Agent 子节点：桥接子节点的终端 WS（SSH 隧道 + agent JWT）。
	if node != "local" {
		if n, ok := loadNodeStore().Nodes[node]; ok && str(n["type"]) == "ssh" && agentReady(n) {
			target := "/api/terminal/ws"
			if persist {
				target += "?persist=1"
			}
			bridgeAgentWS(w, r, n, target)
			return
		}
	}
	key := node + "|shell"
	// 非持久终端使用独立 key，断开即销毁，避免与持久会话串线。
	if !persist {
		oneShot, err := newSID()
		if err != nil {
			WriteJSON(w, 500, map[string]any{"detail": "创建终端会话失败"})
			return
		}
		key = node + "|shell|oneshot|" + oneShot
	}
	title := "本机终端"
	if node != "local" {
		title = node + " 终端"
	}
	session, fresh, err := getOrCreateTermSession(key, title, node, persist, "")
	conn, upErr := upgradeWS(w, r)
	if upErr != nil {
		if !persist {
			destroyTermSession(key)
		}
		return
	}
	if err != nil {
		_ = conn.WriteText([]byte("\r\n[terminal error] " + err.Error() + "\r\n"))
		_ = conn.Close()
		return
	}
	serveTerminal(conn, session, persist, fresh)
	if persist {
		AuditRecord("接入持久终端", u.Username(), ClientIP(r), key)
	} else {
		target := "本机终端"
		if node != "local" {
			target = "远程节点终端"
		}
		AuditRecord(target, u.Username(), ClientIP(r), "")
	}
	if !persist {
		destroyTermSession(key)
	}
}

func containerTerminalWS(w http.ResponseWriter, r *http.Request) {
	u := AuthenticateWS(r)
	if u == nil {
		WriteJSON(w, 401, map[string]any{"detail": "未认证"})
		return
	}
	if IsDefaultPassword(u.PasswordHash()) || !HasPerm(u, "terminal") {
		WriteJSON(w, 403, map[string]any{"detail": "无权限"})
		return
	}
	container := strings.TrimSpace(r.URL.Query().Get("container"))
	nodeID := ""
	// 远端节点：Agent 子节点桥接；裸 SSH 节点在远端开 docker exec PTY。
	if n := currentNode(); str(n["type"]) == "ssh" {
		if agentReady(n) {
			bridgeAgentWS(w, r, n, "/api/terminal/ws/container?container="+url.QueryEscape(container))
			return
		}
		nodeID = str(n["id"])
	}
	// 先拿会话 ID 再升级连接：升级之后已无法再返回 HTTP 错误。
	oneShot, err := newSID()
	if err != nil {
		WriteJSON(w, 500, map[string]any{"detail": "创建容器终端会话失败"})
		return
	}
	conn, err := upgradeWS(w, r)
	if err != nil {
		return
	}
	key := "container|" + container + "|" + oneShot
	session, _, createErr := getOrCreateTermSession(key, "容器终端", nodeID, false, container)
	if createErr != nil {
		_ = conn.WriteText([]byte("\r\n[container terminal] " + createErr.Error() + "\r\n"))
		_ = conn.Close()
		return
	}
	AuditRecord("进入容器终端", u.Username(), ClientIP(r), container)
	serveTerminal(conn, session, false, true)
	destroyTermSession(key)
}

func serveTerminal(conn *wsConn, session *termSession, persist, fresh bool) {
	if persist {
		session.mu.Lock()
		clients := len(session.clients)
		session.mu.Unlock()
		ctrl, _ := json.Marshal(map[string]any{
			"type": "persist", "key": session.key, "fresh": fresh,
			"replay": len(session.buf), "clients": clients,
		})
		_ = conn.WriteText(ctrl)
	}
	id, outCh, replay := session.attach()
	defer session.detach(id)
	if len(replay) > 0 {
		_ = conn.WriteText(replay)
	}
	done := make(chan struct{})
	var once sync.Once
	closeDone := func() { once.Do(func() { close(done) }) }

	// 输出泵：会话输出 -> 浏览器。
	go func() {
		defer closeDone()
		for chunk := range outCh {
			if err := conn.WriteText(chunk); err != nil {
				return
			}
		}
		session.mu.Lock()
		alive := session.alive
		session.mu.Unlock()
		if !alive {
			_ = conn.WriteText([]byte("\r\n[进程已退出]\r\n"))
		}
		_ = conn.Close()
	}()

	// 输入循环：浏览器 -> 会话。
	for {
		select {
		case <-done:
			return
		default:
		}
		_, data, err := conn.ReadMessage()
		if err != nil {
			closeDone()
			return
		}
		msg := string(data)
		if msg == heartbeatPing {
			_ = conn.WriteText([]byte(heartbeatPong))
			continue
		}
		if strings.HasPrefix(msg, resizePrefix) {
			parts := strings.SplitN(strings.TrimPrefix(msg, resizePrefix), ",", 2)
			if len(parts) == 2 {
				var rows, cols int
				_, _ = fmt.Sscanf(parts[0], "%d", &rows)
				_, _ = fmt.Sscanf(parts[1], "%d", &cols)
				session.resize(rows, cols)
			}
			continue
		}
		session.writeInput(data)
	}
}
