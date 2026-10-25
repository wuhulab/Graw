package graw

// sessions.go —— 在线会话表（data/sessions.json）与「踢出设备」能力。
//
// 会话承担两件事：
//  1. 在线列表：登录时 CreateSession 生成随机 sid 写入 JWT，之后每次鉴权刷新 LastSeen；
//  2. 主动下线：删除 sid（RevokeSession）或递增 token_version（BumpTokenVersion），
//     即可让指定设备/该用户全部设备的旧令牌立即失效。
//
// 判定阈值与寿命：
//   - JWT 自身有效期 TokenTTL（7 天）是唯一的硬过期时间，pruneSessions 只按它删除记录；
//   - GRAW_SESSION_ONLINE_SECONDS（默认 2 小时）只决定「是否显示为在线」，
//     不影响令牌有效性，闲置设备不会被强制重新登录；
//   - 想让某台设备立即失效，必须 RevokeSession 踢出或递增 token_version（BumpTokenVersion）；
//   - 空 sid 的旧令牌一律拒绝（登录/改密都会签发带 sid 的新令牌）。
//
// 易踩坑：
//   - 全部读写基于 JSON 文件，sessMu 只保护单进程内的「读-改-写」；多实例部署时互不共享；
//   - 刷新写盘有 60 秒节流（touchSession），高并发下 LastSeen 允许滞后；
//   - 读路径用 mtime+size 缓存（loadSessionsCached），所有写盘必须经 saveSessions 失效缓存。

import (
	"crypto/rand"
	"encoding/base64"
	"log"
	"os"
	"sort"
	"sync"
	"time"
)

// Session 在线会话记录（对应 data/sessions.json 的单个值）。
// key 是随机 sid（写入 JWT 的 sid 声明），删除该 key 即让对应设备下线。
type Session struct {
	Username  string  `json:"username"`
	IP        string  `json:"ip"`
	Device    string  `json:"device"`
	CreatedAt float64 `json:"created_at"`
	LastSeen  float64 `json:"last_seen"`
}

var (
	sessMu     sync.Mutex             // 保护 sessions.json 的「读-改-写」，避免并发覆盖
	touchMu    sync.Mutex             // 保护下面两个节流缓存（与 sessMu 分离，缩短持锁时间）
	touchCache = map[string]float64{} // sid -> 上次刷新时间，60 秒内不重复写盘

	// 鉴权热路径的只读缓存：按文件 mtime+size 复用解析结果。
	// 所有写盘都经 saveSessions 失效缓存，外部改写由 mtime 兜底。
	sessCacheMu   sync.Mutex
	sessCache     map[string]Session
	sessCacheMod  time.Time
	sessCacheSize int64
)

// loadSessions 读取会话文件；文件缺失或损坏时返回空表（不向调用方报错）。
// 会话数据是可重建的，读失败时宁可当作「无在线会话」也不阻断鉴权流程。
func loadSessions() map[string]Session {
	var sessions map[string]Session
	if err := ReadJSON(DataPath("sessions.json"), &sessions); err != nil && !IsNotExist(err) {
		return map[string]Session{}
	}
	if sessions == nil {
		return map[string]Session{}
	}
	return sessions
}

// loadSessionsCached 返回供鉴权读取的会话快照（调用方不得修改）。
// SessionActive 在每个请求上调用，若每次解析 JSON 会放大磁盘与 CPU 开销。
func loadSessionsCached() map[string]Session {
	path := DataPath("sessions.json")
	st, statErr := os.Stat(path)
	if statErr == nil {
		sessCacheMu.Lock()
		if sessCache != nil && st.ModTime().Equal(sessCacheMod) && st.Size() == sessCacheSize {
			cached := sessCache
			sessCacheMu.Unlock()
			return cached
		}
		sessCacheMu.Unlock()
	}
	sessions := loadSessions()
	if statErr == nil {
		sessCacheMu.Lock()
		sessCache = sessions
		sessCacheMod = st.ModTime()
		sessCacheSize = st.Size()
		sessCacheMu.Unlock()
	}
	return sessions
}

// saveSessions 写回会话文件；写盘失败只影响在线列表，故仅留痕不回传。
// 调用方必须已持有 sessMu。
func saveSessions(sessions map[string]Session) {
	if err := WriteJSONFile(DataPath("sessions.json"), sessions); err != nil {
		log.Printf("graw: 写入在线会话失败: %v", err)
	}
	sessCacheMu.Lock()
	sessCache = nil
	sessCacheMod = time.Time{}
	sessCacheSize = 0
	sessCacheMu.Unlock()
}

// pruneSessions 清理已过 JWT 有效期的会话，返回新的会话表。
// 只按 TokenTTL 删除：「最近活跃超过 OnlineSeconds」仅用于在线列表展示，
// 不能据此删记录，否则闲置设备会被误判为「已被强制下线」而被迫重新登录。
func pruneSessions(sessions map[string]Session, now float64) map[string]Session {
	keep := map[string]Session{}
	ttl := TokenTTL.Seconds()
	for sid, s := range sessions {
		if now-s.CreatedAt > ttl {
			continue
		}
		keep[sid] = s
	}
	return keep
}

// newSID 生成 16 字节随机会话 ID（base64url，无填充）。
// 必须用 crypto/rand：sid 等同于「设备凭证」，可预测会导致会话劫持；
// 熵源失败时返回错误，不能退化成固定/可预测的 ID。
func newSID() (string, error) {
	buf := make([]byte, 16)
	if _, err := rand.Read(buf); err != nil {
		log.Printf("graw: 生成会话 ID 失败: %v", err)
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(buf), nil
}

// CreateSession 创建会话记录并返回新 sid；写盘失败只留痕（会话列表缺失，不阻断登录）。
// device 超过 120 字符会截断，避免异常 UA 撑大 sessions.json。
func CreateSession(username, ip, device string) (string, error) {
	if len(device) > 120 {
		device = device[:120]
	}
	sid, err := newSID()
	if err != nil {
		return "", err
	}
	sessMu.Lock()
	defer sessMu.Unlock()
	// 登录是低频操作，顺手清理已过期会话，避免文件无限增长。
	sessions := pruneSessions(loadSessions(), float64(time.Now().Unix()))
	now := float64(time.Now().Unix())
	sessions[sid] = Session{Username: username, IP: ip, Device: device, CreatedAt: now, LastSeen: now}
	saveSessions(sessions)
	return sid, nil
}

// SessionActive 判断 sid 是否仍是有效会话。
// 空 sid（本文升级前签发的旧令牌）一律拒绝：登录与改密都会签发带 sid 的新令牌，
// 允许空 sid 等于保留一批无法按设备吊销的永久凭证。
func SessionActive(sid string) bool {
	if sid == "" {
		return false
	}
	sessions := loadSessionsCached()
	_, ok := sessions[sid]
	return ok
}

// RevokeSession 吊销单个会话，返回是否真实删除。
// 返回 false 表示该 sid 已不存在（已过期或被踢），调用方通常转 404。
func RevokeSession(sid string) bool {
	if sid == "" {
		return false
	}
	sessMu.Lock()
	defer sessMu.Unlock()
	sessions := loadSessions()
	if _, ok := sessions[sid]; !ok {
		return false
	}
	delete(sessions, sid)
	saveSessions(sessions)
	return true
}

// RevokeUserSessions 吊销某用户全部会话。
// 由 BumpTokenVersion（登出/踢人/改密）调用；无匹配会话时不写盘。
func RevokeUserSessions(username string) {
	sessMu.Lock()
	defer sessMu.Unlock()
	sessions := loadSessions()
	changed := false
	for sid, s := range sessions {
		if s.Username == username {
			delete(sessions, sid)
			changed = true
		}
	}
	if changed {
		saveSessions(sessions)
	}
}

// SessionItem 会话列表项。sid 只用于「踢出该设备」，不包含任何令牌或凭据。
type SessionItem struct {
	SID       string  `json:"sid"`
	Username  string  `json:"username"`
	IP        string  `json:"ip"`
	Device    string  `json:"device"`
	CreatedAt float64 `json:"created_at"`
	LastSeen  float64 `json:"last_seen"`
}

// ListSessions 列出在线会话（按最近活跃倒序）；username 为空表示全部。
// limit 默认 100、封顶 500；返回的是快照，踢人请用 SID 调 RevokeSession。
// 在线列表只展示 OnlineSeconds 内活跃的设备，但不会因此删除会话记录。
func ListSessions(username string, limit int) []SessionItem {
	now := float64(time.Now().Unix())
	sessMu.Lock()
	sessions := loadSessions()
	pruned := pruneSessions(sessions, now)
	// 只有确实清理掉过期条目时才写盘，避免纯读请求频繁落盘。
	if len(pruned) != len(sessions) {
		saveSessions(pruned)
	}
	sessMu.Unlock()

	online := float64(GetConfig().OnlineSeconds)
	items := make([]SessionItem, 0, len(pruned))
	for sid, s := range pruned {
		if username != "" && s.Username != username {
			continue
		}
		// 超过在线阈值只是「不显示为在线」，令牌在 JWT 过期前仍然有效。
		last := s.LastSeen
		if last == 0 {
			last = s.CreatedAt
		}
		if now-last > online {
			continue
		}
		items = append(items, SessionItem{
			SID: sid, Username: s.Username, IP: s.IP, Device: s.Device,
			CreatedAt: s.CreatedAt, LastSeen: s.LastSeen,
		})
	}
	// LastSeen 可能为 0（历史数据），排序时回退用 CreatedAt。
	sort.Slice(items, func(i, j int) bool {
		li, lj := items[i].LastSeen, items[j].LastSeen
		if li == 0 {
			li = items[i].CreatedAt
		}
		if lj == 0 {
			lj = items[j].CreatedAt
		}
		return li > lj
	})
	if limit <= 0 {
		limit = 100
	}
	if limit > 500 {
		limit = 500
	}
	if len(items) > limit {
		items = items[:limit]
	}
	return items
}

// touchSession 刷新指定会话的 LastSeen。
// 写入有 60 秒节流（touchCache），鉴权热路径上不会每次请求都落盘。
// 记录已被踢出/过期时只跳过，不补建：补建会让刚被吊销的设备重新变为有效。
func touchSession(sid, username string) {
	if sid == "" {
		return
	}
	now := time.Now().Unix()
	touchMu.Lock()
	if now-int64(touchCache[sid]) < 60 {
		touchMu.Unlock()
		return
	}
	touchCache[sid] = float64(now)
	// 节流表按 sid 增长；超过阈值时清掉已过节的旧记录，避免长期运行后无界膨胀。
	if len(touchCache) > 4096 {
		for k, ts := range touchCache {
			if int64(ts) < now-60 {
				delete(touchCache, k)
			}
		}
	}
	touchMu.Unlock()

	sessMu.Lock()
	defer sessMu.Unlock()
	sessions := loadSessions()
	s, ok := sessions[sid]
	if !ok {
		return
	}
	s.LastSeen = float64(now)
	if username != "" {
		s.Username = username
	}
	sessions[sid] = s
	saveSessions(sessions)
}
