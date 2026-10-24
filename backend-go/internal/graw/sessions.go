package graw

import (
	"crypto/rand"
	"encoding/base64"
	"sort"
	"sync"
	"time"
)

// Session 在线会话记录（对应 data/sessions.json 的单个值）。
type Session struct {
	Username  string  `json:"username"`
	IP        string  `json:"ip"`
	Device    string  `json:"device"`
	CreatedAt float64 `json:"created_at"`
	LastSeen  float64 `json:"last_seen"`
}

var (
	sessMu      sync.Mutex
	touchMu     sync.Mutex
	touchCache  = map[string]float64{}
	ensureCache = map[string]float64{}
)

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

func saveSessions(sessions map[string]Session) {
	_ = WriteJSONFile(DataPath("sessions.json"), sessions)
}

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

func newSID() string {
	buf := make([]byte, 16)
	_, _ = rand.Read(buf)
	return base64.RawURLEncoding.EncodeToString(buf)
}

// CreateSession 创建会话记录并返回 sid；失败时返回空串（不阻断登录）。
func CreateSession(username, ip, device string) string {
	if len(device) > 120 {
		device = device[:120]
	}
	sessMu.Lock()
	defer sessMu.Unlock()
	sessions := pruneSessions(loadSessions(), float64(time.Now().Unix()))
	sid := newSID()
	now := float64(time.Now().Unix())
	sessions[sid] = Session{Username: username, IP: ip, Device: device, CreatedAt: now, LastSeen: now}
	saveSessions(sessions)
	return sid
}

// SessionActive 判断 sid 是否仍是有效会话（空 sid 视为旧令牌，向后兼容）。
func SessionActive(sid string) bool {
	if sid == "" {
		return true
	}
	sessions := loadSessions()
	_, ok := sessions[sid]
	return ok
}

// RevokeSession 吊销单个会话，返回是否真实删除。
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

// SessionItem 会话列表项。
type SessionItem struct {
	SID       string  `json:"sid"`
	Username  string  `json:"username"`
	IP        string  `json:"ip"`
	Device    string  `json:"device"`
	CreatedAt float64 `json:"created_at"`
	LastSeen  float64 `json:"last_seen"`
}

// ListSessions 列出在线会话（按最近活跃倒序）；username 为空表示全部。
func ListSessions(username string, limit int) []SessionItem {
	sessMu.Lock()
	sessions := loadSessions()
	pruned := pruneSessions(sessions, float64(time.Now().Unix()))
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
		sessions[sid] = Session{Username: username, CreatedAt: float64(now), LastSeen: float64(now)}
	}
	saveSessions(sessions)
}

// EnsureOnlineSession 确保当前账号在在线列表中有会话记录。
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
	sessions = pruneSessions(sessions, float64(now))
	sid2 := newSID()
	sessions[sid2] = Session{Username: username, CreatedAt: float64(now), LastSeen: float64(now)}
	saveSessions(sessions)
}
