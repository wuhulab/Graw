package graw

// sessions.go —— 在线会话表（data/sessions.json）与「踢出设备」能力。
//
// 会话承担两件事：
//  1. 在线列表：登录时 CreateSession 生成随机 sid 写入 JWT，之后每次鉴权刷新 LastSeen；
//  2. 主动下线：删除 sid（RevokeSession）或递增 token_version（BumpTokenVersion），
//     即可让指定设备/该用户全部设备的旧令牌立即失效。
//
// 判定阈值与寿命：
//   - JWT 自身有效期 TokenTTL（7 天）是唯一的硬过期时间；
//   - GRAW_SESSION_ONLINE_SECONDS（默认 2 小时）只决定「是否还出现在在线列表」：
//     超时条目会在下一次写入型操作（CreateSession/ListSessions/EnsureOnlineSession）时被 prune，
//     但若该 sid 之后再次发请求，touchSession 会把记录补建回来，并不会强制重新登录；
//   - 想让某台设备立即失效，必须 RevokeSession 踢出或递增 token_version（BumpTokenVersion）。
//
// 易踩坑：
//   - 全部读写基于 JSON 文件，sessMu 只保护单进程内的「读-改-写」；多实例部署时互不共享；
//   - 刷新写盘有 60 秒节流（touchSession/EnsureOnlineSession），高并发下 LastSeen 允许滞后；
//   - SessionActive 仅查存在性、不做过期判断（清理依赖写入路径的 pruneSessions），
//     详见函数注释中的说明，改动前先确认这一语义是否仍满足需求。

import (
	"crypto/rand"
	"encoding/base64"
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
	sessMu      sync.Mutex             // 保护 sessions.json 的「读-改-写」，避免并发覆盖
	touchMu     sync.Mutex             // 保护下面两个节流缓存（与 sessMu 分离，缩短持锁时间）
	touchCache  = map[string]float64{} // sid -> 上次刷新时间，60 秒内不重复写盘
	ensureCache = map[string]float64{} // username -> 上次刷新时间（无 sid 的旧令牌场景）
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

// saveSessions 写回会话文件；写盘失败被忽略（会话丢失只影响在线列表）。
// 调用方必须已持有 sessMu。
func saveSessions(sessions map[string]Session) {
	_ = WriteJSONFile(DataPath("sessions.json"), sessions)
}

// pruneSessions 清理过期会话，返回新的会话表。
// 过期条件（满足任一）：
//   - 创建至今超过 JWT 有效期（TokenTTL）；
//   - 最近活跃时间距 now 超过在线阈值（OnlineSeconds，默认 2 小时）。
//
// LastSeen 为 0（历史数据/无 sid 的旧记录）时回退用 CreatedAt 判断。
func pruneSessions(sessions map[string]Session, now float64) map[string]Session {
	keep := map[string]Session{}
	online := float64(GetConfig().OnlineSeconds)
	ttl := TokenTTL.Seconds()
	for sid, s := range sessions {
		last := s.LastSeen
		if last == 0 {
			last = s.CreatedAt
		}
		if now-s.CreatedAt > ttl || now-last > online {
			continue
		}
		keep[sid] = s
	}
	return keep
}

// newSID 生成 16 字节随机会话 ID（base64url，无填充）。
// 必须用 crypto/rand：sid 等同于「设备凭证」，可预测会导致会话劫持。
func newSID() string {
	buf := make([]byte, 16)
	_, _ = rand.Read(buf)
	return base64.RawURLEncoding.EncodeToString(buf)
}

// CreateSession 创建会话记录并返回新 sid；写盘失败也照常返回（不阻断登录）。
// device 超过 120 字符会截断，避免异常 UA 撑大 sessions.json。
// 注意：saveSessions 忽略错误，因此「落盘失败」不会体现在返回值上，
// 会话随后可被 EnsureOnlineSession 补建，但该设备的在线记录可能短暂缺失。
func CreateSession(username, ip, device string) string {
	if len(device) > 120 {
		device = device[:120]
	}
	sessMu.Lock()
	defer sessMu.Unlock()
	// 登录是低频操作，顺手清理过期会话，避免文件无限增长。
	sessions := pruneSessions(loadSessions(), float64(time.Now().Unix()))
	sid := newSID()
	now := float64(time.Now().Unix())
	sessions[sid] = Session{Username: username, IP: ip, Device: device, CreatedAt: now, LastSeen: now}
	saveSessions(sessions)
	return sid
}

// SessionActive 判断 sid 是否仍是有效会话（空 sid 视为旧令牌，向后兼容）。
// 只做「存在性」判断，不校验 OnlineSeconds 过期：过期会话要等写入路径 prune 后才会消失。
// 因此在线阈值是「宽松上限」，真正的硬失效依赖 token_version / 显式踢出。
func SessionActive(sid string) bool {
	if sid == "" {
		return true
	}
	sessions := loadSessions()
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
func ListSessions(username string, limit int) []SessionItem {
	sessMu.Lock()
	sessions := loadSessions()
	pruned := pruneSessions(sessions, float64(time.Now().Unix()))
	// 只有确实清理掉条目时才写盘，避免纯读请求频繁落盘。
	if len(pruned) != len(sessions) {
		saveSessions(pruned)
	}
	sessMu.Unlock()

	items := make([]SessionItem, 0, len(pruned))
	for sid, s := range pruned {
		if username != "" && s.Username != username {
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

// touchSession 刷新指定会话的 LastSeen，并在缺失时补建记录。
// 写入有 60 秒节流（touchCache），鉴权热路径上不会每次请求都落盘。
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
	touchMu.Unlock()

	sessMu.Lock()
	defer sessMu.Unlock()
	sessions := loadSessions()
	if s, ok := sessions[sid]; ok {
		s.LastSeen = float64(now)
		if username != "" {
			s.Username = username
		}
		sessions[sid] = s
	} else {
		// 会话已被 prune/踢出但请求仍带着 sid：补建记录。
		// 注意这会让「刚被清理」的会话重新变为 active（见 SessionActive 的语义说明）。
		sessions[sid] = Session{Username: username, CreatedAt: float64(now), LastSeen: float64(now)}
	}
	saveSessions(sessions)
}

// EnsureOnlineSession 确保当前账号在在线列表中有会话记录。
// 有 sid 时走 touchSession；无 sid（旧版令牌，sid 为空）时按用户名刷新或补建一条记录，
// 保证「在线列表」在兼容模式下仍有数据。同样有 60 秒节流。
func EnsureOnlineSession(username, sid string) {
	if sid != "" {
		touchSession(sid, username)
		return
	}
	now := time.Now().Unix()
	touchMu.Lock()
	if now-int64(ensureCache[username]) < 60 {
		touchMu.Unlock()
		return
	}
	ensureCache[username] = float64(now)
	touchMu.Unlock()

	sessMu.Lock()
	defer sessMu.Unlock()
	sessions := loadSessions()
	online := GetConfig().OnlineSeconds
	// 优先刷新该用户最近仍在线的一条会话，避免旧令牌每次请求都新建记录。
	for sid2, s := range sessions {
		last := s.LastSeen
		if last == 0 {
			last = s.CreatedAt
		}
		if s.Username == username && int64(now)-int64(last) <= online {
			s.LastSeen = float64(now)
			sessions[sid2] = s
			saveSessions(sessions)
			return
		}
	}
	// 没有可用记录：先清理过期项，再为兼容模式补建一条新 sid。
	sessions = pruneSessions(sessions, float64(now))
	sid2 := newSID()
	sessions[sid2] = Session{Username: username, CreatedAt: float64(now), LastSeen: float64(now)}
	saveSessions(sessions)
}
