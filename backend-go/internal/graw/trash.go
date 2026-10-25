package graw

// 文件职责：回收站核心逻辑（对应 Python 旧版 app/trash.py），提供删除入站、
// 列表、恢复、彻底删除、清空与过期自动清理，并维护回收站内 .graw_trash.json 清单。
//
// 核心流程：MoveToTrash 先把文件/目录移动到回收站根目录（重名自动加序号），
// 再追加一条清单记录；恢复/删除/清空都以清单中的 Trash 路径为入口，校验归属后才执行。
//
// 安全约束与易踩坑：
//   - 任何删除/恢复前必须用 PathInTrash 校验路径确实位于回收站目录内，否则可越界删任意文件；
//   - 恢复时还要拒绝把条目还原到面板 data 目录（forbiddenDataPath），避免覆盖用户/密钥；
//   - 本地与远程 SSH 节点路径风格不同（filepath / path），所有路径操作必须走 Host* 适配层，
//     不能在远程节点上直接拼本地绝对路径，否则会操作到错误主机；
//   - 回收站清单与文件必须属于同一节点；切换节点（remoteActive）后不能混用本地清单；
//   - trashMu 串行化「清单读写 + 目标名探测 + 搬移」，保证同名文件不会互相覆盖。

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

// remoteTrashRoot 是回收站根目录的「宿主机视角」绝对路径。
// 远程 SSH 节点直接使用它；本地 Linux 由 HostPath 映射到容器内的 /host 之下。
const remoteTrashRoot = "/var/lib/graw-trash"

// TrashItem 回收站条目。Original 与 Trash 都是宿主机视角路径，
// DeletedAt 为 Unix 秒，DeletedBy 为执行删除的面板用户名（未知时写 unknown）。
type TrashItem struct {
	ID        string `json:"id"`
	Original  string `json:"original"`
	Trash     string `json:"trash"`
	DeletedAt int64  `json:"deleted_at"`
	DeletedBy string `json:"deleted_by"`
}

// trashMeta 是回收站清单文件的磁盘结构（当前 Version 固定为 1），
// Items 为空时也要落盘为 []，避免前端拿到 null。
type trashMeta struct {
	Version int         `json:"version"`
	Items   []TrashItem `json:"items"`
}

// RecycleConfig 回收站配置（data/recycle.json）。
// Enabled 控制文件删除是否先入回收站；AutoDelete 与 AutoDeleteDays 控制过期清理。
type RecycleConfig struct {
	Enabled        bool `json:"enabled"`
	AutoDelete     bool `json:"auto_delete"`
	AutoDeleteDays int  `json:"auto_delete_days"`
}

// trashMu 串行化回收站清单的「读-改-写」，防止并发移动/恢复互相覆盖清单。
// 注意它不覆盖文件搬移本身，也不覆盖 uniqueTrashDst 的目标名探测。
var trashMu sync.Mutex

// defaultRecycleConfig 返回回收站默认配置：启用、保留 30 天自动清理。
func defaultRecycleConfig() RecycleConfig {
	return RecycleConfig{Enabled: true, AutoDelete: true, AutoDeleteDays: 30}
}

// LoadRecycleConfig 读取回收站配置；文件缺失或 JSON 损坏时整体回退默认值，
// 单个字段类型不符则保留该字段的默认值。保留天数会被夹到 1-365。
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

// SaveRecycleConfig 原子保存回收站配置，并把保留天数夹到 1-365 后返回实际生效值。
// 失败时返回待写入的配置与写盘错误，调用方需检查 error。
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

// TrashRoot 返回当前节点回收站根目录的宿主机视角路径：
//   - 远程 SSH 节点：固定 /var/lib/graw-trash；
//   - 本地 Windows：%LOCALAPPDATA%\graw-trash（缺失时退回用户主目录）；
//   - 本地 Linux：同样返回 /var/lib/graw-trash，由 HostPath 映射进容器 /host。
//
// 该返回值只表示「视角路径」，真正读写务必再经 Host* 函数映射。
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

// PathInTrash 判断路径是否位于回收站目录内，且排除回收站根目录本身。
// 这是删除/恢复的越界防线：不在回收站内的路径一律拒绝操作。
// 按路径分段比较（本地用 filepath.Rel，远程用 POSIX 前缀），未解析符号链接；
// 删除侧走 Lstat + Remove，不会顺着链接删到回收站之外。
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

// trashMetaPath 返回回收站清单文件路径（隐藏文件名，避免在回收站界面里显示成普通条目）。
func trashMetaPath() string {
	return HostPathJoin(TrashRoot(), ".graw_trash.json")
}

// loadTrashItems 读取回收站清单：远程节点用 HostReadBytes（cat），本地直接读文件。
// 文件不存在、内容损坏或 Items 为 nil 时统一返回空切片（不返回 nil，方便直接序列化）。
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

// saveTrashItems 覆盖写回收站清单（失败仅忽略，属尽力而为）。
// 远程节点以 0600 写入远端文件；本地走 WriteJSONFile（原子替换 + 0600）。
// 注意：写失败不会回滚已移动的文件，条目会从清单中丢失。
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

// ensureTrashDir 确保回收站根目录存在（0700，只有面板进程可访问），返回该根目录。
// 失败时返回空串与错误，调用方不应继续搬移文件。
func ensureTrashDir() (string, error) {
	root := TrashRoot()
	if err := HostMkdirAll(root, 0o700); err != nil {
		return "", err
	}
	return root, nil
}

// uniqueTrashDst 在回收站根目录下为源文件名挑一个不冲突的目标路径：
// 原名被占用时依次尝试「name (2).ext」「name (3).ext」…；
// 序号用尽（1000 次）或目录探测异常时退回「name-<时间戳>.ext」。
// 调用方必须持有 trashMu，保证探测与随后的搬移不会被并发请求插入。
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

// moveHostPath 在宿主机视角上搬移文件/目录，入参与返回均使用视角路径。
// 远程节点先 mv，失败再 cp -a + rm（目标盘不支持 rename 时兜底）；
// 本地先 os.Rename，失败（典型是跨设备 EXDEV）再递归复制后删除源。
// 复制兜底不会保留属主/时间戳，且符号链接会被解引用成普通文件内容。
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

// removeHostPath 永久删除宿主机视角路径，路径不存在视为成功。
// 用 Lstat 判断类型：目录递归删除，符号链接只删链接本身（不跟随目标），
// 避免回收站里的链接把删除操作带到目录之外。
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

// MoveToTrash 把宿主机视角路径 srcView 移入当前节点的回收站，登记后返回条目。
//
// 参数：srcView 必须是已规范化的视角路径（调用方经 safeUserPath 校验）；username 仅用于记录。
// 失败行为：建目录或搬移失败时返回错误且不写清单，源文件保持原状（复制兜底若中途失败会留下
// 不完整的目标副本，但不会删除源文件）。
// 目标名唯一性检查（uniqueTrashDst）与搬移都在 trashMu 内完成，
// 避免并发请求搬移同名文件时选中同一目标（Linux 下 rename/mv 会覆盖同名文件）。
func MoveToTrash(srcView, username string) (TrashItem, error) {
	// 第一步：确保回收站目录存在（0700），不存在时直接失败
	root, err := ensureTrashDir()
	if err != nil {
		return TrashItem{}, err
	}
	// 第二步：锁内完成「选目标名 → 读清单 → 搬移 → 追加记录」。
	// 目标名探测必须在锁内，否则并发搬移同名文件会选中同一目标并相互覆盖。
	trashMu.Lock()
	defer trashMu.Unlock()
	dst := uniqueTrashDst(root, srcView)
	items := loadTrashItems()
	if err := moveHostPath(srcView, dst); err != nil {
		return TrashItem{}, err
	}
	item := TrashItem{
		ID: randomHex(32), Original: pathOrFileClean(srcView), Trash: pathOrFileClean(dst),
		DeletedAt: time.Now().Unix(), DeletedBy: defaultString(username, "unknown"),
	}
	items = append(items, item)
	// 第四步：清单落盘（尽力而为，写失败时文件已在回收站但无法在界面恢复）
	saveTrashItems(items)
	return item, nil
}

// defaultString 返回去空格后非空的 v，否则返回 def（用于补齐删除人）。
func defaultString(v, def string) string {
	if strings.TrimSpace(v) == "" {
		return def
	}
	return v
}

// ListTrashItems 返回当前节点回收站条目，按删除时间倒序（最新在前）。
// 读清单不加 trashMu，依赖底层原子写保证不会读到半截 JSON；损坏时返回空列表。
func ListTrashItems() []TrashItem {
	items := loadTrashItems()
	sort.Slice(items, func(i, j int) bool { return items[i].DeletedAt > items[j].DeletedAt })
	return items
}

// findTrashItem 按回收站路径精确匹配清单条目（两侧都按当前节点风格规范化）。
// 找不到时返回零值与 false。
func findTrashItem(items []TrashItem, trash string) (TrashItem, bool) {
	target := pathOrFileClean(trash)
	for _, it := range items {
		if pathOrFileClean(it.Trash) == target {
			return it, true
		}
	}
	return TrashItem{}, false
}

// RestoreTrashItem 把回收站条目还原到删除前的位置，返回 {ok, original}。
//
// 校验顺序：路径必须位于回收站内 → 必须存在于清单 → 原位置不能是面板 data 目录 →
// 原父目录必须存在 → 原位置不能被占用（避免覆盖同名文件）。
// 失败行为：返回 400（越界/受保护路径）、404（条目或父目录不存在）、409（原位置已占用）；
// 搬移失败时直接透传底层错误，条目仍留在清单中。
// 审计由调用方 handler 记录。
func RestoreTrashItem(trashPath string) (map[string]any, error) {
	// 第一步：确认操作目标确实在回收站内，杜绝借恢复接口移动任意路径
	trash := pathOrFileClean(trashPath)
	if !PathInTrash(trash) {
		return nil, NewHTTPError(400, "非法回收站路径")
	}
	trashMu.Lock()
	defer trashMu.Unlock()
	items := loadTrashItems()
	// 第二步：必须在清单里登记过，避免手工塞进回收站目录的文件被当作条目操作
	item, ok := findTrashItem(items, trash)
	if !ok {
		return nil, NewHTTPError(404, "回收站条目不存在")
	}
	original := pathOrFileClean(item.Original)
	// 第三步：禁止还原到面板 data 目录（用户表/密钥所在），防止覆盖凭据；远程节点不做本机 data 判断
	if original == "" || (!remoteActive() && forbiddenDataPath(original)) {
		return nil, NewHTTPError(400, "该条目无法恢复（原位置受保护）")
	}
	// 第四步：原父目录必须还在，且原位置在此期间不能被别人重新创建，避免静默覆盖
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
	// 第五步：搬移成功后才从清单删除该条目
	kept := []TrashItem{}
	for _, it := range items {
		if pathOrFileClean(it.Trash) != trash {
			kept = append(kept, it)
		}
	}
	saveTrashItems(kept)
	return map[string]any{"ok": true, "original": original}, nil
}

// DeleteTrashItem 彻底删除单个回收站条目（不可恢复）。
// 前置条件：路径在回收站内且存在于清单；删除文件失败会返回错误。
// 清单写失败不会返回错误（saveTrashItems 为尽力而为），此时条目已删但清单残留，
// 再次操作会得到 404，过期清理时也会因路径不存在而继续跳过。
func DeleteTrashItem(trashPath string) error {
	// 越界防线：只允许删除回收站目录内的路径
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

// EmptyTrash 清空回收站并返回「清单中的」条目数。
// 仅删除清单里登记且仍位于回收站内的路径；回收站目录下未登记的文件（历史残留）不会被清理。
// 删除失败的条目会保留在清单里，避免文件还在却从界面消失、之后无法再管理。
func EmptyTrash() int {
	trashMu.Lock()
	defer trashMu.Unlock()
	items := loadTrashItems()
	kept := make([]TrashItem, 0)
	for _, it := range items {
		if !PathInTrash(it.Trash) {
			continue
		}
		if err := removeHostPath(it.Trash); err != nil {
			kept = append(kept, it)
		}
	}
	saveTrashItems(kept)
	return len(items)
}

// PurgeExpiredTrash 清理删除时间早于保留期的条目，返回实际删除数。
// days<=0 时使用配置中的 AutoDeleteDays；AutoDelete 关闭时直接返回 0。
// 单条删除失败（权限、远程不可达）时保留该条目，等下一轮再试。
// 删除时间缺失（DeletedAt=0）的历史条目不会自动删除：宁可少清，也不误删无时间戳的数据。
func PurgeExpiredTrash(days int) int {
	cfg := LoadRecycleConfig()
	if !cfg.AutoDelete {
		return 0
	}
	if days <= 0 {
		days = cfg.AutoDeleteDays
	}
	// 截止时间：早于 now - days*86400 秒的条目视为过期
	deadline := time.Now().Unix() - int64(days)*86400
	trashMu.Lock()
	defer trashMu.Unlock()
	items := loadTrashItems()
	removed := 0
	kept := []TrashItem{}
	for _, it := range items {
		if it.DeletedAt > 0 && it.DeletedAt < deadline && PathInTrash(it.Trash) {
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

// StartTrashAutoPurge 启动常驻后台协程，启动即清理一次，之后每小时清理一次过期条目。
// 每轮用 defer recover 兜住 panic，避免单次异常终止整个清理循环；进程退出即回收该协程。
func StartTrashAutoPurge() {
	go func() {
		for {
			// 每轮独立 recover：单次清理 panic 不影响后续轮次
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

// copyRecursive 递归复制文件/目录，用于跨设备搬移与文件复制功能。
// 目录按源目录权限重建；普通文件按源权限写入。
// 注意：符号链接会被 os.Open/ReadDir 解引用，复制成普通文件/目录内容（不保留链接本身），
// 也不保留属主、时间戳与扩展属性。
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

// copyFileContents 把单个文件内容复制到 dst，自动创建父目录并按 perm 设置权限。
// 目标文件以截断方式覆盖写入；写入或关闭失败都会返回错误（可能留下不完整的 dst）。
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
