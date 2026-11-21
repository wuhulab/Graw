package graw

import (
	"crypto/md5"
	"crypto/sha1"
	"crypto/sha256"
	"encoding/hex"
	"hash"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"
)

// 文件管理增强（P1）：收藏夹、移动、哈希、备注、上传下载记录。
// 所有路径仍走 safeUserPath / Host* 抽象，保证本地与远端节点语义一致。

type fileFavorite struct {
	ID        string `json:"id"`
	Path      string `json:"path"`
	Name      string `json:"name"`
	CreatedAt string `json:"created_at"`
}

type fileTransfer struct {
	Direction string `json:"direction"`
	Path      string `json:"path"`
	Size      int64  `json:"size"`
	Status    string `json:"status"`
	Time      string `json:"time"`
}

var fileTransfersMu sync.Mutex
var fileTransfers []fileTransfer

func filesFavoritesPath() string { return DataPath("files_favorites.json") }
func fileNotesPath() string      { return DataPath("file_notes.json") }

func loadFileFavorites() []fileFavorite {
	items := []fileFavorite{}
	_ = ReadJSON(filesFavoritesPath(), &items)
	return items
}

func saveFileFavorites(items []fileFavorite) error {
	CaptureSnapshot("files-favorites", "files", filesFavoritesPath())
	return WriteJSONFile(filesFavoritesPath(), items)
}

func loadFileNotes() map[string]map[string]any {
	data := map[string]map[string]any{}
	_ = ReadJSON(fileNotesPath(), &data)
	return data
}

func saveFileNotes(data map[string]map[string]any) error {
	CaptureSnapshot("file-notes", "files", fileNotesPath())
	return WriteJSONFile(fileNotesPath(), data)
}

// recordTransfer 记录最近一次上传/下载，供「传输记录」面板展示。
func recordTransfer(direction, path string, size int64, status string) {
	fileTransfersMu.Lock()
	defer fileTransfersMu.Unlock()
	fileTransfers = append(fileTransfers, fileTransfer{
		Direction: direction, Path: path, Size: size, Status: status,
		Time: time.Now().Format("2006-01-02 15:04:05"),
	})
	if len(fileTransfers) > 200 {
		fileTransfers = fileTransfers[len(fileTransfers)-200:]
	}
}

func listTransfers() []fileTransfer {
	fileTransfersMu.Lock()
	defer fileTransfersMu.Unlock()
	out := make([]fileTransfer, len(fileTransfers))
	copy(out, fileTransfers)
	sort.Slice(out, func(i, j int) bool { return out[i].Time > out[j].Time })
	if len(out) > 100 {
		out = out[:100]
	}
	return out
}

func fileHash(path, algo string) (map[string]any, error) {
	p, err := safeUserPath(path)
	if err != nil {
		return nil, err
	}
	if !HostIsFile(p) {
		return nil, NewHTTPError(404, "文件不存在")
	}
	size := HostSize(p)
	if size > 512<<20 {
		return nil, NewHTTPError(400, "文件超过 512MB，暂不支持在线哈希")
	}
	data, err := HostReadBytes(p)
	if err != nil {
		return nil, filesOpError(err, "读取文件失败")
	}
	var h hash.Hash
	switch strings.ToLower(algo) {
	case "", "sha256":
		h = sha256.New()
	case "md5":
		h = md5.New()
	case "sha1":
		h = sha1.New()
	default:
		return nil, NewHTTPError(400, "不支持的算法（sha256/md5/sha1）")
	}
	_, _ = h.Write(data)
	return map[string]any{
		"path": UnhostPath(p), "algo": strings.ToLower(algo), "size": size,
		"hash": hex.EncodeToString(h.Sum(nil)),
	}, nil
}

func moveFile(src, dstDir string) (string, error) {
	srcPath, err := safeUserPath(src)
	if err != nil {
		return "", err
	}
	dirPath, err := safeUserPath(dstDir)
	if err != nil {
		return "", err
	}
	if !HostExists(srcPath) {
		return "", NewHTTPError(404, "源文件不存在")
	}
	if !HostIsDir(dirPath) {
		return "", NewHTTPError(400, "目标目录不存在")
	}
	if HostDir(srcPath) == dirPath {
		return "", NewHTTPError(400, "源与目标目录相同")
	}
	dst := HostPathJoin(dirPath, HostBase(srcPath))
	if HostExists(dst) {
		return "", NewHTTPError(409, "目标目录已存在同名文件")
	}
	if err := HostRename(srcPath, dst); err != nil {
		return "", filesOpError(err, "移动失败")
	}
	return UnhostPath(dst), nil
}

func registerFileEnhancedRoutes(a *App) {
	a.Get("/api/files/favorites", perm("files"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"favorites": loadFileFavorites()})
	})
	a.Post("/api/files/favorites", perm("files"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Path string `json:"path"`
			Name string `json:"name"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		p, err := safeUserPath(req.Path)
		if err != nil {
			return err
		}
		items := loadFileFavorites()
		view := UnhostPath(p)
		for _, item := range items {
			if item.Path == view {
				return WriteJSON(w, 200, map[string]any{"ok": true, "favorites": items})
			}
		}
		name := strings.TrimSpace(req.Name)
		if name == "" {
			name = HostBase(p)
		}
		items = append(items, fileFavorite{
			ID: "fav_" + randomHex(6), Path: view, Name: name,
			CreatedAt: time.Now().Format("2006-01-02 15:04:05"),
		})
		if err := saveFileFavorites(items); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "favorites": items})
	})
	a.Post("/api/files/favorites/{fav_id}/delete", perm("files"), func(w http.ResponseWriter, r *http.Request) error {
		id := strings.TrimSpace(PathParam(r, "fav_id"))
		items := loadFileFavorites()
		out := items[:0]
		for _, item := range items {
			if item.ID != id {
				out = append(out, item)
			}
		}
		if err := saveFileFavorites(out); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "favorites": out})
	})
	a.Get("/api/files/notes", perm("files"), func(w http.ResponseWriter, r *http.Request) error {
		data := loadFileNotes()
		path := strings.TrimSpace(r.URL.Query().Get("path"))
		if path != "" {
			p, err := safeUserPath(path)
			if err != nil {
				return err
			}
			view := UnhostPath(p)
			return WriteJSON(w, 200, map[string]any{"path": view, "note": data[view]})
		}
		return WriteJSON(w, 200, map[string]any{"notes": data})
	})
	a.Post("/api/files/notes", perm("files"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Path string `json:"path"`
			Note string `json:"note"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		p, err := safeUserPath(req.Path)
		if err != nil {
			return err
		}
		view := UnhostPath(p)
		data := loadFileNotes()
		note := strings.TrimSpace(req.Note)
		if note == "" {
			delete(data, view)
		} else {
			if len(note) > 4000 {
				return NewHTTPError(400, "备注不能超过 4000 字")
			}
			data[view] = map[string]any{"note": note, "updated_at": time.Now().Format("2006-01-02 15:04:05")}
		}
		if err := saveFileNotes(data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "note": data[view]})
	})
	a.Post("/api/files/move", perm("files"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Src    string `json:"src"`
			DstDir string `json:"dst_dir"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		dst, err := moveFile(req.Src, req.DstDir)
		if err != nil {
			return err
		}
		AuditRecord("移动文件", CurrentUser(r).Username(), ClientIP(r), req.Src+" -> "+dst)
		return WriteJSON(w, 200, map[string]any{"ok": true, "path": dst})
	})
	a.Get("/api/files/hash", perm("files"), func(w http.ResponseWriter, r *http.Request) error {
		res, err := fileHash(r.URL.Query().Get("path"), r.URL.Query().Get("algo"))
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, res)
	})
	a.Get("/api/files/transfers", perm("files"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"transfers": listTransfers()})
	})
}
