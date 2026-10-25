package graw

// ws.go —— WebSocket 基础设施：升级器、并发安全写封装与关闭约定。
//
// 关键点：
//   - 鉴权不在这里：浏览器 WebSocket API 无法携带 Authorization 头，端点内部用 ?token=
//     调 AuthenticateWS；失败时回 401/403 或直接关闭连接（见 handlers_system / handlers_terminal）；
//   - CheckOrigin 只允许同源（Origin.Host == r.Host）；无 Origin 视为非浏览器客户端放行，
//     真正的身份校验仍靠 token，不能把 Origin 当鉴权手段；
//   - gorilla/websocket 不允许并发写，wsConn.WriteText/WriteBinary 内部加锁并设 10s 写超时；
//   - 单条消息读取上限 1MB（upgradeWS 里 SetReadLimit），避免恶意端撑爆内存；
//   - 长连接只在建立时鉴权一次，处理器必须周期性调用 WSStillValid(token)，
//     否则改密/踢出生效后旧连接仍然可用。

import (
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

// wsUpgrader 是全局 WebSocket 升级配置（读写缓冲 4096、开启压缩）。
// CheckOrigin 是 CSRF/跨站劫持的第一道防线：只接受同源握手。
var wsUpgrader = websocket.Upgrader{
	ReadBufferSize:    4096,
	WriteBufferSize:   4096,
	EnableCompression: true,
	CheckOrigin: func(r *http.Request) bool {
		origin := strings.TrimSpace(r.Header.Get("Origin"))
		if origin == "" {
			return true // 非浏览器客户端（脚本/CLI）无 Origin
		}
		u, err := url.Parse(origin)
		if err != nil {
			return false
		}
		// 只比对 Host（含端口），不比对 scheme：反代场景下 https 终止后 Host 仍一致。
		return strings.EqualFold(u.Host, r.Host)
	},
}

// wsConn 是 WebSocket 写入的最小封装（内部互斥，允许读写协程并发）。
type wsConn struct {
	c  *websocket.Conn
	mu sync.Mutex // 串行化写入：gorilla 明确禁止并发写，否则会丢帧/破坏连接
}

// WriteText 发送文本消息；内部加锁并设 10s 写超时，避免慢客户端永久阻塞推送协程。
func (w *wsConn) WriteText(data []byte) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	_ = w.c.SetWriteDeadline(time.Now().Add(10 * time.Second))
	return w.c.WriteMessage(websocket.TextMessage, data)
}

// WriteBinary 发送二进制消息；超时与写锁语义同 WriteText。
func (w *wsConn) WriteBinary(data []byte) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	_ = w.c.SetWriteDeadline(time.Now().Add(10 * time.Second))
	return w.c.WriteMessage(websocket.BinaryMessage, data)
}

// ReadMessage 读取一条消息（读取本身由单个协程独占，无需加锁）。
// 返回的 error 通常意味着连接已断开，调用方应结束该连接的读循环。
func (w *wsConn) ReadMessage() (int, []byte, error) {
	return w.c.ReadMessage()
}

// Close 关闭底层连接；重复调用会返回错误但无副作用，调用方通常忽略返回值。
func (w *wsConn) Close() error {
	return w.c.Close()
}

// Underlying 暴露底层连接，供需要设置 ping/pong 或自定义读超时的处理器使用。
func (w *wsConn) Underlying() *websocket.Conn { return w.c }

// upgradeWS 完成 WebSocket 握手。
// 握手失败时 Upgrade 已写出错误响应，调用方直接 return 即可（不要重复写响应）。
// 成功后限制单条消息 1MB，防止恶意端发送超大帧耗尽内存。
func upgradeWS(w http.ResponseWriter, r *http.Request) (*wsConn, error) {
	c, err := wsUpgrader.Upgrade(w, r, nil)
	if err != nil {
		return nil, err
	}
	c.SetReadLimit(1 << 20)
	return &wsConn{c: c}, nil
}
