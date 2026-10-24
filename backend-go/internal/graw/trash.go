package graw

import (
	"encoding/json"
	"io"
	"log"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

// 回收站核心逻辑（对应 Python app/trash.py）。
const remoteTrashRoot = "/var/lib/graw-trash"

// TrashItem 回收站条目。
type TrashItem struct {
	ID        string `json:"id"`
	Original  string `json:"original"`
	Trash     string `json:"trash"`
	DeletedAt int64  `json:"deleted_at"`
	DeletedBy string `json:"deleted_by"`
}

type trashMeta struct {
	Version int         `json:"version"`
	Items   []TrashItem `json:"items"`
}

// RecycleConfig 回收站配置（data/recycle.json）。
type RecycleConfig struct {
	Enabled        bool `json:"enabled"`
	AutoDelete     bool `json:"auto_delete"`
	AutoDeleteDays int  `json:"auto_delete_days"`
}

var trashMu sync.Mutex

func defaultRecycleConfig() RecycleConfig {
	return RecycleConfig{Enabled: true, AutoDelete: true, AutoDeleteDays: 30}
}

// LoadRecycleConfig 读取回收站配置（缺失/损坏回退默认值）。
func LoadRecycleConfig() RecycleConfig {
	var raw map[string]any
	if err := ReadJSON(DataPath("recycle.json"), &raw); err != nil {
		return defaultRecycleConfig()
	}
	cfg := defaultRecycleConfig()
	if b, ok := raw["enabled"].(bool); ok {
		cfg.Enabled = b
	}
	if b, ok := raw["auto_delete"].(bool); ok {
		cfg.AutoDelete = b
	}
	if v, ok := raw["auto_delete_days"]; ok {
		days := int(num(v))
		if days < 1 {
			days = 1
		}
		if days > 365 {
			days = 365
		}
		cfg.AutoDeleteDays = days
	}
	return cfg
}

// SaveRecycleConfig 原子保存回收站配置。
func SaveRecycleConfig(enabled, autoDelete bool, days int) (RecycleConfig, error) {
	if days < 1 {
		days = 1
	}
	if days > 365 {
		days = 365
	}
	cfg := RecycleConfig{Enabled: enabled, AutoDelete: autoDelete, AutoDeleteDays: days}
	trashMu.Lock()
	defer trashMu.Unlock()
	return cfg, WriteJSONFile(DataPath("recycle.json"), cfg)
}

// TrashRoot 返回当前主机回收站目录（宿主机视角）。
func TrashRoot() string {
	if remoteActive() {
		return remoteTrashRoot
	}
	if isWindows() {
		base := os.Getenv("LOCALAPPDATA")
		if base == "" {
			base, _ = os.UserHomeDir()
		}
		return filepath.Join(base, "graw-trash")
	}
	return remoteTrashRoot
}

// PathInTrash 判断路径是否位于回收站目录内（不含根目录本身）。
func PathInTrash(p string) bool {
	if !remoteActive() {
		base := filepath.Clean(TrashRoot())
		abs, err := filepath.Abs(p)
		if err != nil {
			return false
		}
		rel, err := filepath.Rel(base, abs)
		return err == nil && rel != "." && !strings.HasPrefix(rel, "..")
	}
	base := path.Clean(TrashRoot())
	abs := path.Clean(p)
	if abs == base {
		return false
	}
	return strings.HasPrefix(abs, base+"/")
}

func trashMetaPath() string {
	return HostPathJoin(TrashRoot(), ".graw_trash.json")
}

func loadTrashItems() []TrashItem {
	var meta trashMeta
	if remoteActive() {
		b, err := HostReadBytes(trashMetaPath())
		if err != nil {
			return []TrashItem{}
		}
		if json.Unmarshal(b, &meta) != nil {
			return []TrashItem{}
		}
	} else if err := ReadJSON(trashMetaPath(), &meta); err != nil {
		return []TrashItem{}
	}
	if meta.Items == nil {
		return []TrashItem{}
	}
	return meta.Items
}

func saveTrashItems(items []TrashItem) {
	if items == nil {
		items = []TrashItem{}
	}
	if remoteActive() {
		b, _ := json.Marshal(trashMeta{Version: 1, Items: items})
		_ = HostWriteBytes(trashMetaPath(), b, 0o600)
		return
	}
	_ = WriteJSONFile(trashMetaPath(), trashMeta{Version: 1, Items: items})
}

func ensureTrashDir() (string, error) {
	root := TrashRoot()
	if err := HostMkdirAll(root, 0o700); err != nil {
		return "", err
	}
	return root, nil
}

func uniqueTrashDst(root, src string) string {
	name := HostBase(HostCleanPath(src))
	if strings.TrimSpace(name) == "" || name == "." || name == "/" {
		name = "untitled"
	}
	ext := path.Ext(name)
	if !remoteActive() {
		ext = filepath.Ext(name)
	}
	stem := strings.TrimSuffix(name, ext)
	cand := HostPathJoin(root, name)
	if !HostExists(cand) {
		return cand
	}
	for i := 2; i < 1000; i++ {
		cand = HostPathJoin(root, stem+" ("+itoa(i)+")"+ext)
		if !HostExists(cand) {
			return cand
		}
	}
	return HostPathJoin(root, stem+"-"+itoa(int(time.Now().Unix()))+ext)
}

// moveHostPath 在宿主机视角上移动文件（远程 mv，本地 rename，跨设备退化拷贝+删除）。
func moveHostPath(srcView, dstView string) error {
	if remoteActive() {
		if err := HostRename(srcView, dstView); err == nil {
			return nil
		}
		if err := HostCopyPath(srcView, dstView); err != nil {
			return err
		}
		return HostRemove(srcView)
	}
	src := HostPath(srcView)
	dst := HostPath(dstView)
	if err := os.Rename(src, dst); err == nil {
		return nil
	}
	if err := copyRecursive(src, dst); err != nil {
		return err
	}
	return removeHostPath(srcView)
}

// removeHostPath 永久删除宿主机视角路径。
func removeHostPath(view string) error {
	if remoteActive() {
		return HostRemove(view)
	}
	real := HostPath(view)
	st, err := os.Lstat(real)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	if st.IsDir() && st.Mode()&os.ModeSymlink == 0 {
		return os.RemoveAll(real)
	}
	return os.Remove(real)
}

// MoveToTrash 把文件/目录移入回收站，返回条目。
func MoveToTrash(srcView, username string) (TrashItem, error) {
	root, err := ensureTrashDir()
	if err != nil {
		return TrashItem{}, err
	}
	dst := uniqueTrashDst(root, srcView)
	trashMu.Lock()
	defer trashMu.Unlock()
	items := loadTrashItems()
	if err := moveHostPath(srcView, dst); err != nil {
		return TrashItem{}, err
	}
	item := TrashItem{
		ID: randomHex(32), Original: pathOrFileClean(srcView), Trash: pathOrFileClean(dst),
		DeletedAt: time.Now().Unix(), DeletedBy: defaultString(username, "unknown"),
	}
	items = append(items, item)
	saveTrashItems(items)
	return item, nil
}

func defaultString(v, def string) string {
	if strings.TrimSpace(v) == "" {
		return def
	}
	return v
}

// ListTrashItems 返回回收站条目（按删除时间倒序）。
func ListTrashItems() []TrashItem {
	items := loadTrashItems()
	sort.Slice(items, func(i, j int) bool { return items[i].DeletedAt > items[j].DeletedAt })
	return items
}

func findTrashItem(items []TrashItem, trash string) (TrashItem, bool) {
	target := pathOrFileClean(trash)
	for _, it := range items {
		if pathOrFileClean(it.Trash) == target {
			return it, true
		}
	}
	return TrashItem{}, false
}

// RestoreTrashItem 恢复条目到原位置。
func RestoreTrashItem(trashPath, username string) (map[string]any, error) {
	trash := pathOrFileClean(trashPath)
	if !PathInTrash(trash) {
		return nil, NewHTTPError(400, "非法回收站路径")
	}
	trashMu.Lock()
	defer trashMu.Unlock()
	items := loadTrashItems()
	item, ok := findTrashItem(items, trash)
	if !ok {
		return nil, NewHTTPError(404, "回收站条目不存在")
	}
	original := pathOrFileClean(item.Original)
	if original == "" || (!remoteActive() && forbiddenDataPath(original)) {
		return nil, NewHTTPError(400, "该条目无法恢复（原位置受保护）")
	}
	parent := HostDir(original)
	if !HostExists(parent) {
		return nil, NewHTTPError(404, "原位置目录不存在")
	}
	if HostExists(original) {
		return nil, NewHTTPError(409, "原位置已存在同名文件")
	}
	if err := moveHostPath(trash, original); err != nil {
		return nil, err
	}
	kept := []TrashItem{}
	for _, it := range items {
		if pathOrFileClean(it.Trash) != trash {
			kept = append(kept, it)
		}
	}
	saveTrashItems(kept)
	return map[string]any{"ok": true, "original": original}, nil
}

// DeleteTrashItem 彻底删除单个条目。
func DeleteTrashItem(trashPath string) error {
	trash := pathOrFileClean(trashPath)
	if !PathInTrash(trash) {
		return NewHTTPError(400, "非法回收站路径")
	}
	trashMu.Lock()
	defer trashMu.Unlock()
	items := loadTrashItems()
	if _, ok := findTrashItem(items, trash); !ok {
		return NewHTTPError(404, "回收站条目不存在")
	}
	if err := removeHostPath(trash); err != nil {
		return err
	}
	kept := []TrashItem{}
	for _, it := range items {
		if pathOrFileClean(it.Trash) != trash {
			kept = append(kept, it)
		}
	}
	saveTrashItems(kept)
	return nil
}

// EmptyTrash 清空回收站，返回清理条目数。
func EmptyTrash() int {
	trashMu.Lock()
	defer trashMu.Unlock()
	items := loadTrashItems()
	for _, it := range items {
		if PathInTrash(it.Trash) {
			_ = removeHostPath(it.Trash)
		}
	}
	saveTrashItems([]TrashItem{})
	return len(items)
}

// PurgeExpiredTrash 清理过期条目，返回清理数。
func PurgeExpiredTrash(days int) int {
	cfg := LoadRecycleConfig()
	if !cfg.AutoDelete {
		return 0
	}
	if days <= 0 {
		days = cfg.AutoDeleteDays
	}
	deadline := time.Now().Unix() - int64(days)*86400
	trashMu.Lock()
	defer trashMu.Unlock()
	items := loadTrashItems()
	removed := 0
	kept := []TrashItem{}
	for _, it := range items {
		if it.DeletedAt < deadline && PathInTrash(it.Trash) {
			if err := removeHostPath(it.Trash); err == nil {
				removed++
				continue
			}
		}
		kept = append(kept, it)
	}
	if removed > 0 {
		saveTrashItems(kept)
	}
	return removed
}

// StartTrashAutoPurge 启动每小时一次的后台过期清理。
func StartTrashAutoPurge() {
	go func() {
		for {
			func() {
				defer func() { _ = recover() }()
				if n := PurgeExpiredTrash(0); n > 0 {
					log.Printf("回收站过期清理: 删除 %d 条", n)
				}
			}()
			time.Sleep(time.Hour)
		}
	}()
}

// copyRecursive 递归复制文件/目录（供复制、跨盘移动使用）。
func copyRecursive(src, dst string) error {
	st, err := os.Lstat(src)
	if err != nil {
		return err
	}
	if st.IsDir() {
		if err := os.MkdirAll(dst, st.Mode().Perm()); err != nil {
			return err
		}
		entries, err := os.ReadDir(src)
		if err != nil {
			return err
		}
		for _, e := range entries {
			if err := copyRecursive(filepath.Join(src, e.Name()), filepath.Join(dst, e.Name())); err != nil {
				return err
			}
		}
		return nil
	}
	return copyFileContents(src, dst, st.Mode().Perm())
}

func copyFileContents(src, dst string, perm os.FileMode) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	if err := os.MkdirAll(filepath.Dir(dst), 0o700); err != nil {
		return err
	}
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, perm)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		_ = out.Close()
		return err
	}
	return out.Close()
}
