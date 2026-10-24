package graw

import (
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

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
		return strings.EqualFold(u.Host, r.Host)
	},
}

// wsConn 是 WebSocket 写入的最小封装（内部互斥，允许读写协程并发）。
type wsConn struct {
	c  *websocket.Conn
	mu sync.Mutex
}

func (w *wsConn) WriteText(data []byte) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	_ = w.c.SetWriteDeadline(time.Now().Add(10 * time.Second))
	return w.c.WriteMessage(websocket.TextMessage, data)
}

func (w *wsConn) WriteBinary(data []byte) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	_ = w.c.SetWriteDeadline(time.Now().Add(10 * time.Second))
	return w.c.WriteMessage(websocket.BinaryMessage, data)
}

func (w *wsConn) ReadMessage() (int, []byte, error) {
	return w.c.ReadMessage()
}

func (w *wsConn) Close() error {
	return w.c.Close()
}

func (w *wsConn) Underlying() *websocket.Conn { return w.c }

// upgradeWS 完成 WebSocket 握手。
func upgradeWS(w http.ResponseWriter, r *http.Request) (*wsConn, error) {
	c, err := wsUpgrader.Upgrade(w, r, nil)
	if err != nil {
		return nil, err
	}
	c.SetReadLimit(1 << 20)
	return &wsConn{c: c}, nil
}

// closeWS 发送自定义关闭码（供 ?token= 鉴权失败场景使用）。
func closeWS(w http.ResponseWriter, code int, reason string) {
	c, err := wsUpgrader.Upgrade(w, nil, nil)
	if err != nil {
		return
	}
	_ = c.WriteControl(websocket.CloseMessage,
		websocket.FormatCloseMessage(code, reason), time.Now().Add(3*time.Second))
	_ = c.Close()
}
