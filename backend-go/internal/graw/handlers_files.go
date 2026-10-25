package graw

import (
	"archive/tar"
	"archive/zip"
	"compress/gzip"
	"fmt"
	"io"
	"mime"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"time"
)

// forbiddenDataPath 判断宿主机视角路径是否落在面板 data 目录内。
func forbiddenDataPath(view string) bool {
	data := GetConfig().DataDir
	p := filepath.Clean(view)
	d := filepath.Clean(data)
	if isWindows() {
		p = strings.ToLower(p)
		d = strings.ToLower(d)
	}
	rel, err := filepath.Rel(d, p)
	if err != nil {
		return false
	}
	return rel == "." || (!strings.HasPrefix(rel, ".."+string(os.PathSeparator)) && rel != "..")
}

// safeUserPath 规范化用户提交的宿主机视角路径并拦截面板数据目录。
// 远程 SSH 节点按 POSIX 规则规范化（不访问控制器的本地文件系统）。
func safeUserPath(input string) (string, error) {
	if strings.TrimSpace(input) == "" {
		input = "/"
	}
	if strings.HasPrefix(input, `\\?\`) || strings.HasPrefix(input, `\\.\`) ||
		strings.HasPrefix(input, `\\`) || strings.HasPrefix(input, "//") && remoteActive() {
		return "", NewHTTPError(400, "非法路径（不支持设备命名空间路径）")
	}
	if remoteActive() {
		p := strings.ReplaceAll(input, "\\", "/")
		if !strings.HasPrefix(p, "/") {
			p = "/" + p
		}
		p = path.Clean(p)
		return p, nil
	}
	abs, err := filepath.Abs(input)
	if err != nil {
		return "", NewHTTPError(400, "非法路径")
	}
	if resolved, err := filepath.EvalSymlinks(abs); err == nil {
		abs = resolved
	}
	if forbiddenDataPath(abs) {
		return "", NewHTTPError(403, "无权访问面板数据目录")
	}
	return abs, nil
}

func registerFileRoutes(a *App) {
	a.Get("/api/files/list", perm("files"), filesListHandler)
	a.Get("/api/files/roots", perm("files"), filesRootsHandler)
	a.Get("/api/files/read", perm("files"), filesReadHandler)
	a.Post("/api/files/write", perm("files"), filesWriteHandler)
	a.Post("/api/files/delete", perm("files"), filesDeleteHandler)
	a.Post("/api/files/mkdir", perm("files"), filesMkdirHandler)
	a.Post("/api/files/rename", perm("files"), filesRenameHandler)
	a.Get("/api/files/download", perm("files"), filesDownloadHandler)
	a.Post("/api/files/upload", perm("files"), filesUploadHandler)
	a.Post("/api/files/chmod", perm("files"), filesChmodHandler)
	a.Post("/api/files/copy", perm("files"), filesCopyHandler)
	a.Post("/api/files/compress", perm("files"), filesCompressHandler)
	a.Post("/api/files/extract", perm("files"), filesExtractHandler)

	a.Get("/api/recycle/config", perm("files"), func(w http.ResponseWriter, r *http.Request) error {
		cfg := LoadRecycleConfig()
		return WriteJSON(w, 200, map[string]any{
			"enabled": cfg.Enabled, "auto_delete": cfg.AutoDelete,
			"auto_delete_days": cfg.AutoDeleteDays, "trash_root": TrashRoot(),
		})
	})
	a.Post("/api/recycle/config", perm("files"), func(w http.ResponseWriter, r *http.Request) error {
		u := CurrentUser(r)
		var req struct {
			Enabled        bool `json:"enabled"`
			AutoDelete     bool `json:"auto_delete"`
			AutoDeleteDays int  `json:"auto_delete_days"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if req.AutoDeleteDays < 1 || req.AutoDeleteDays > 365 {
			return NewHTTPError(400, "保留天数需在 1-365 之间")
		}
		cfg, err := SaveRecycleConfig(req.Enabled, req.AutoDelete, req.AutoDeleteDays)
		if err != nil {
			return err
		}
		AuditRecord("回收站配置", u.Username(), ClientIP(r), fmt.Sprintf("enabled=%v auto_delete=%v days=%d", cfg.Enabled, cfg.AutoDelete, cfg.AutoDeleteDays))
		return WriteJSON(w, 200, cfg)
	})
	a.Get("/api/recycle/list", perm("files"), func(w http.ResponseWriter, r *http.Request) error {
		PurgeExpiredTrash(0)
		return WriteJSON(w, 200, map[string]any{"items": ListTrashItems(), "trash_root": TrashRoot()})
	})
	a.Post("/api/recycle/restore", perm("files"), func(w http.ResponseWriter, r *http.Request) error {
		u := CurrentUser(r)
		var req struct {
			Path string `json:"path"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		res, err := RestoreTrashItem(req.Path)
		if err != nil {
			return err
		}
		AuditRecord("恢复回收站", u.Username(), ClientIP(r), toString(res["original"]))
		return WriteJSON(w, 200, res)
	})
	a.Post("/api/recycle/delete", perm("files"), func(w http.ResponseWriter, r *http.Request) error {
		u := CurrentUser(r)
		var req struct {
			Path string `json:"path"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if err := DeleteTrashItem(req.Path); err != nil {
			return err
		}
		AuditRecord("彻底删除", u.Username(), ClientIP(r), req.Path)
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/recycle/empty", perm("files"), func(w http.ResponseWriter, r *http.Request) error {
		u := CurrentUser(r)
		n := EmptyTrash()
		AuditRecord("清空回收站", u.Username(), ClientIP(r), "共 "+itoa(n)+" 条")
		return WriteJSON(w, 200, map[string]any{"ok": true, "count": n})
	})
}

// pathOrFileClean 按当前节点路径风格规范化（远程 POSIX / 本地 filepath）。
func pathOrFileClean(p string) string {
	if remoteActive() {
		return path.Clean(p)
	}
	return filepath.Clean(p)
}

func filesListHandler(w http.ResponseWriter, r *http.Request) error {
	sp, err := safeUserPath(r.URL.Query().Get("path"))
	if err != nil {
		return err
	}
	if !HostExists(sp) {
		return NewHTTPError(404, "Path not found")
	}
	if !HostIsDir(sp) {
		return NewHTTPError(400, "Not a directory")
	}
	entries, listErr := HostListDir(sp)
	if listErr != nil {
		return NewHTTPError(403, "Permission denied")
	}
	real := HostPath(sp)
	items := []map[string]any{}
	trashReal := pathOrFileClean(HostPath(TrashRoot()))
	for _, en := range entries {
		fullView := HostPathJoin(sp, en.Name)
		if pathOrFileClean(fullView) == trashReal {
			continue
		}
		items = append(items, map[string]any{
			"name": en.Name, "path": UnhostPath(HostPathJoin(real, en.Name)),
			"is_dir": en.IsDir, "size": en.Size, "modified": en.Modified,
		})
	}
	sort.Slice(items, func(i, j int) bool {
		di, dj := items[i]["is_dir"].(bool), items[j]["is_dir"].(bool)
		if di != dj {
			return di
		}
		return strings.ToLower(items[i]["name"].(string)) < strings.ToLower(items[j]["name"].(string))
	})
	var parent any = nil
	if pathOrFileClean(sp) != "/" && HostDir(sp) != sp {
		parent = UnhostPath(HostDir(sp))
	}
	return WriteJSON(w, 200, map[string]any{"path": sp, "parent": parent, "items": items})
}

func filesRootsHandler(w http.ResponseWriter, r *http.Request) error {
	roots := []string{"/"}
	if runtime.GOOS == "windows" && !remoteActive() {
		roots = []string{}
		for c := 'A'; c <= 'Z'; c++ {
			p := string(c) + ":\\"
			if st, err := os.Stat(p); err == nil && st.IsDir() {
				roots = append(roots, p)
			}
		}
	}
	return WriteJSON(w, 200, map[string]any{"roots": roots})
}

func filesReadHandler(w http.ResponseWriter, r *http.Request) error {
	sp, err := safeUserPath(r.URL.Query().Get("path"))
	if err != nil {
		return err
	}
	if !HostIsFile(sp) {
		return NewHTTPError(404, "File not found")
	}
	if HostSize(sp) > 2*1024*1024 {
		return NewHTTPError(413, "File too large (>2MB)")
	}
	data, readErr := HostReadBytes(sp)
	if readErr != nil {
		return filesOpError(readErr, "读取文件失败")
	}
	return WriteJSON(w, 200, map[string]any{"path": sp, "content": string(data)})
}

func filesOpError(err error, fallback string) error {
	if err == nil {
		return nil
	}
	if os.IsPermission(err) {
		return NewHTTPError(403, "权限不足："+err.Error())
	}
	if os.IsNotExist(err) {
		return NewHTTPError(404, "文件或目录不存在："+err.Error())
	}
	var he *HTTPError
	if ok := asHTTPError(err, &he); ok {
		return he
	}
	return NewHTTPError(500, fallback+"："+err.Error())
}

func asHTTPError(err error, target **HTTPError) bool {
	if he, ok := err.(*HTTPError); ok {
		*target = he
		return true
	}
	return false
}

func filesWriteHandler(w http.ResponseWriter, r *http.Request) error {
	u := CurrentUser(r)
	var req struct {
		Path    string `json:"path"`
		Content string `json:"content"`
	}
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	sp, err := safeUserPath(req.Path)
	if err != nil {
		return err
	}
	mode := os.FileMode(0o644)
	if !remoteActive() {
		if st, e := os.Stat(HostPath(sp)); e == nil {
			mode = st.Mode().Perm()
		}
	}
	if err := HostWriteBytes(sp, []byte(req.Content), mode); err != nil {
		return filesOpError(err, "写入文件失败")
	}
	AuditRecord("写文件", u.Username(), ClientIP(r), sp)
	return WriteJSON(w, 200, map[string]any{"ok": true})
}

func filesDeleteHandler(w http.ResponseWriter, r *http.Request) error {
	u := CurrentUser(r)
	var req struct {
		Path string `json:"path"`
	}
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	sp, err := safeUserPath(req.Path)
	if err != nil {
		return err
	}
	if !HostExists(sp) {
		return NewHTTPError(404, "Not found")
	}
	// 移入回收站同样要拦截整机级路径（搬移失败也不能把根目录当普通文件处理）。
	if err := refuseCriticalDelete(sp); err != nil {
		return err
	}
	if LoadRecycleConfig().Enabled && !PathInTrash(sp) {
		item, err := MoveToTrash(sp, u.Username())
		if err != nil {
			return filesOpError(err, "删除失败")
		}
		AuditRecord("删除(回收站)", u.Username(), ClientIP(r), sp+" -> "+item.Trash)
	} else {
		if err := HostRemove(sp); err != nil {
			return filesOpError(err, "删除失败")
		}
		AuditRecord("删除", u.Username(), ClientIP(r), sp)
	}
	return WriteJSON(w, 200, map[string]any{"ok": true})
}

func filesMkdirHandler(w http.ResponseWriter, r *http.Request) error {
	u := CurrentUser(r)
	var req struct {
		Path string `json:"path"`
	}
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	sp, err := safeUserPath(req.Path)
	if err != nil {
		return err
	}
	if err := HostMkdirAll(sp, 0o755); err != nil {
		return filesOpError(err, "新建目录失败")
	}
	AuditRecord("新建目录", u.Username(), ClientIP(r), sp)
	return WriteJSON(w, 200, map[string]any{"ok": true})
}

func filesRenameHandler(w http.ResponseWriter, r *http.Request) error {
	u := CurrentUser(r)
	var req struct {
		Src string `json:"src"`
		Dst string `json:"dst"`
	}
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	src, err := safeUserPath(req.Src)
	if err != nil {
		return err
	}
	dst, err := safeUserPath(req.Dst)
	if err != nil {
		return err
	}
	if !HostExists(src) {
		return NewHTTPError(404, "Source not found")
	}
	if err := HostRename(src, dst); err != nil {
		return filesOpError(err, "重命名失败")
	}
	AuditRecord("重命名", u.Username(), ClientIP(r), req.Src+" -> "+req.Dst)
	return WriteJSON(w, 200, map[string]any{"ok": true})
}

func filesDownloadHandler(w http.ResponseWriter, r *http.Request) error {
	sp, err := safeUserPath(r.URL.Query().Get("path"))
	if err != nil {
		return err
	}
	if !HostIsFile(sp) {
		return NewHTTPError(404, "File not found")
	}
	disposition := mime.FormatMediaType("attachment", map[string]string{"filename": HostBase(sp)})
	if disposition != "" {
		w.Header().Set("Content-Disposition", disposition)
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	if err := HostCopyTo(sp, w); err != nil {
		return filesOpError(err, "下载失败")
	}
	return nil
}

func filesUploadHandler(w http.ResponseWriter, r *http.Request) error {
	u := CurrentUser(r)
	if err := r.ParseMultipartForm(32 << 20); err != nil {
		return NewHTTPError(400, "上传表单解析失败")
	}
	targetView, err := safeUserPath(r.FormValue("path"))
	if err != nil {
		return err
	}
	if !HostIsDir(targetView) {
		return NewHTTPError(400, "Target directory not found")
	}
	file, header, err := r.FormFile("file")
	if err != nil {
		return NewHTTPError(400, "缺少上传文件")
	}
	defer file.Close()
	name := HostBase(strings.ReplaceAll(header.Filename, "\\", "/"))
	if name == "" || name == "." || name == ".." || name == "/" {
		return NewHTTPError(400, "非法文件名")
	}
	rel := strings.Trim(r.FormValue("relpath"), "/\\")
	rel = strings.ReplaceAll(rel, "\\", "/")
	if rel != "" {
		parts := strings.Split(rel, "/")
		if len(parts) > 1 {
			parts = parts[:len(parts)-1]
		} else {
			parts = nil
		}
		for _, seg := range parts {
			if seg == "" || seg == "." || seg == ".." {
				return NewHTTPError(400, "非法路径")
			}
			targetView = HostPathJoin(targetView, seg)
			if err := HostMkdirAll(targetView, 0o755); err != nil {
				return filesOpError(err, "上传失败")
			}
		}
	}
	target := HostPathJoin(targetView, name)
	if err := HostWriteFrom(target, file, 0o644); err != nil {
		return filesOpError(err, "上传失败")
	}
	AuditRecord("上传文件", u.Username(), ClientIP(r), UnhostPath(target))
	return WriteJSON(w, 200, map[string]any{"ok": true, "path": UnhostPath(target)})
}

func filesChmodHandler(w http.ResponseWriter, r *http.Request) error {
	u := CurrentUser(r)
	var req struct {
		Path string `json:"path"`
		Mode int    `json:"mode"`
	}
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	sp, err := safeUserPath(req.Path)
	if err != nil {
		return err
	}
	if !HostExists(sp) {
		return NewHTTPError(404, "Not found")
	}
	if err := HostChmod(sp, os.FileMode(req.Mode)); err != nil {
		return filesOpError(err, "修改权限失败")
	}
	AuditRecord("修改权限", u.Username(), ClientIP(r), fmt.Sprintf("%s -> %o", sp, req.Mode))
	return WriteJSON(w, 200, map[string]any{"ok": true})
}

func uniqueDst(dst string) string {
	if !HostExists(dst) {
		return dst
	}
	ext := path.Ext(dst)
	if !remoteActive() {
		ext = filepath.Ext(dst)
	}
	stem := strings.TrimSuffix(dst, ext)
	for i := 2; i < 1000; i++ {
		cand := fmt.Sprintf("%s (%d)%s", stem, i, ext)
		if !HostExists(cand) {
			return cand
		}
	}
	return fmt.Sprintf("%s-%d%s", stem, time.Now().Unix(), ext)
}

func filesCopyHandler(w http.ResponseWriter, r *http.Request) error {
	u := CurrentUser(r)
	var req struct {
		Src string `json:"src"`
		Dst string `json:"dst"`
	}
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	srcView, err := safeUserPath(req.Src)
	if err != nil {
		return err
	}
	dstView, err := safeUserPath(req.Dst)
	if err != nil {
		return err
	}
	src, dst := HostPath(srcView), HostPath(dstView)
	if !HostExists(srcView) {
		return NewHTTPError(404, "Source not found")
	}
	if HostIsDir(srcView) {
		sep := string(os.PathSeparator)
		if remoteActive() {
			sep = "/"
		}
		if dst == src || strings.HasPrefix(dst+sep, src+sep) {
			return NewHTTPError(400, "不能把目录复制到其自身内部")
		}
	}
	dst = uniqueDst(dst)
	if err := HostCopyPath(src, dst); err != nil {
		return filesOpError(err, "复制失败")
	}
	AuditRecord("复制", u.Username(), ClientIP(r), req.Src+" -> "+UnhostPath(dst))
	return WriteJSON(w, 200, map[string]any{"ok": true, "dst": UnhostPath(dst)})
}

func filesCompressHandler(w http.ResponseWriter, r *http.Request) error {
	u := CurrentUser(r)
	var req struct {
		Paths   []string `json:"paths"`
		Archive string   `json:"archive"`
		Fmt     string   `json:"fmt"`
	}
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	archiveView, err := safeUserPath(req.Archive)
	if err != nil {
		return err
	}
	archive := HostPath(archiveView)
	paths := []string{}
	for _, p := range req.Paths {
		v, err := safeUserPath(p)
		if err != nil {
			return err
		}
		real := HostPath(v)
		if !Exists(real) {
			return NewHTTPError(404, "Paths not found: "+p)
		}
		paths = append(paths, real)
	}
	if len(paths) == 0 {
		return NewHTTPError(400, "请选择要压缩的路径")
	}
	format := req.Fmt
	if format == "" {
		format = "zip"
	}
	var err2 error
	if remoteActive() {
		err2 = remoteCreateArchive(archive, paths, format)
	} else {
		err2 = createArchive(archive, paths, format)
	}
	if err2 != nil {
		return filesOpError(err2, "压缩失败")
	}
	AuditRecord("压缩", u.Username(), ClientIP(r), archiveView)
	return WriteJSON(w, 200, map[string]any{"ok": true, "archive": archiveView})
}

func createArchive(archive string, paths []string, format string) error {
	if err := os.MkdirAll(filepath.Dir(archive), 0o755); err != nil {
		return err
	}
	out, err := os.Create(archive)
	if err != nil {
		return err
	}
	defer out.Close()
	switch format {
	case "zip":
		zw := zip.NewWriter(out)
		for _, p := range paths {
			base := filepath.Base(p)
			parent := filepath.Dir(p)
			st, err := os.Stat(p)
			if err != nil {
				zw.Close()
				return err
			}
			if st.IsDir() {
				err = filepath.Walk(p, func(path string, info os.FileInfo, err error) error {
					if err != nil || info.IsDir() {
						return err
					}
					rel, err := filepath.Rel(parent, path)
					if err != nil {
						return err
					}
					hdr, err := zip.FileInfoHeader(info)
					if err != nil {
						return err
					}
					hdr.Name = filepath.ToSlash(rel)
					hdr.Method = zip.Deflate
					w, err := zw.CreateHeader(hdr)
					if err != nil {
						return err
					}
					f, err := os.Open(path)
					if err != nil {
						return err
					}
					_, err = io.Copy(w, f)
					f.Close()
					return err
				})
				if err != nil {
					zw.Close()
					return err
				}
			} else {
				hdr, err := zip.FileInfoHeader(st)
				if err != nil {
					zw.Close()
					return err
				}
				hdr.Name = filepath.ToSlash(base)
				hdr.Method = zip.Deflate
				w, err := zw.CreateHeader(hdr)
				if err != nil {
					zw.Close()
					return err
				}
				f, err := os.Open(p)
				if err != nil {
					zw.Close()
					return err
				}
				if _, err := io.Copy(w, f); err != nil {
					f.Close()
					zw.Close()
					return err
				}
				f.Close()
			}
		}
		return zw.Close()
	default:
		var tw *tar.Writer
		var gz *gzip.Writer
		if format == "tar.gz" {
			gz = gzip.NewWriter(out)
			tw = tar.NewWriter(gz)
		} else {
			tw = tar.NewWriter(out)
		}
		for _, p := range paths {
			base := filepath.Base(p)
			err := filepath.Walk(p, func(path string, info os.FileInfo, err error) error {
				if err != nil {
					return err
				}
				rel, err := filepath.Rel(filepath.Dir(p), path)
				if err != nil {
					return err
				}
				_ = base
				hdr, err := tar.FileInfoHeader(info, "")
				if err != nil {
					return err
				}
				hdr.Name = filepath.ToSlash(rel)
				if err := tw.WriteHeader(hdr); err != nil {
					return err
				}
				if info.IsDir() {
					return nil
				}
				f, err := os.Open(path)
				if err != nil {
					return err
				}
				_, err = io.Copy(tw, f)
				f.Close()
				return err
			})
			if err != nil {
				tw.Close()
				if gz != nil {
					gz.Close()
				}
				return err
			}
		}
		if err := tw.Close(); err != nil {
			return err
		}
		if gz != nil {
			return gz.Close()
		}
		return nil
	}
}

func withinBase(base, target string) bool {
	rel, err := filepath.Rel(base, target)
	if err != nil {
		return false
	}
	return rel == "." || (!strings.HasPrefix(rel, ".."+string(os.PathSeparator)) && rel != "..")
}

func filesExtractHandler(w http.ResponseWriter, r *http.Request) error {
	u := CurrentUser(r)
	var req struct {
		Archive string `json:"archive"`
		Dest    string `json:"dest"`
	}
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	archiveView, err := safeUserPath(req.Archive)
	if err != nil {
		return err
	}
	destView, err := safeUserPath(req.Dest)
	if err != nil {
		return err
	}
	archive, dest := HostPath(archiveView), HostPath(destView)
	if st, err := os.Stat(archive); err != nil || st.IsDir() {
		return NewHTTPError(404, "Archive not found")
	}
	if err := os.MkdirAll(dest, 0o755); err != nil {
		return filesOpError(err, "解压失败")
	}
	lower := strings.ToLower(archive)
	if remoteActive() {
		if err := remoteExtractArchive(archive, dest, lower); err != nil {
			return err
		}
		AuditRecord("解压", u.Username(), ClientIP(r), archiveView+" -> "+destView)
		return WriteJSON(w, 200, map[string]any{"ok": true})
	}
	if strings.HasSuffix(lower, ".zip") {
		err = extractZip(archive, dest)
	} else {
		err = extractTar(archive, dest)
	}
	if err != nil {
		return err
	}
	AuditRecord("解压", u.Username(), ClientIP(r), archiveView+" -> "+destView)
	return WriteJSON(w, 200, map[string]any{"ok": true})
}

func extractZip(archive, dest string) error {
	zr, err := zip.OpenReader(archive)
	if err != nil {
		return NewHTTPError(400, "压缩包格式错误")
	}
	defer zr.Close()
	base := filepath.Clean(dest)
	for _, f := range zr.File {
		name := filepath.FromSlash(f.Name)
		if !filepath.IsLocal(name) || filepath.IsAbs(name) {
			return NewHTTPError(400, "压缩包包含非法路径（Zip Slip）")
		}
		target := filepath.Join(base, name)
		if !withinBase(base, target) {
			return NewHTTPError(400, "压缩包包含非法路径（Zip Slip）")
		}
		if f.Mode()&os.ModeSymlink != 0 {
			return NewHTTPError(400, "压缩包包含特殊文件，已拒绝")
		}
		if f.FileInfo().IsDir() {
			if err := os.MkdirAll(target, f.Mode().Perm()); err != nil {
				return filesOpError(err, "解压失败")
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return filesOpError(err, "解压失败")
		}
		rc, err := f.Open()
		if err != nil {
			return filesOpError(err, "解压失败")
		}
		out, err := os.OpenFile(target, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, f.Mode().Perm())
		if err != nil {
			rc.Close()
			return filesOpError(err, "解压失败")
		}
		_, copyErr := io.Copy(out, rc)
		rc.Close()
		if copyErr != nil {
			_ = out.Close()
			return filesOpError(copyErr, "解压失败")
		}
		if err := out.Close(); err != nil {
			return filesOpError(err, "解压失败")
		}
	}
	return nil
}

func extractTar(archive, dest string) error {
	f, err := os.Open(archive)
	if err != nil {
		return filesOpError(err, "解压失败")
	}
	defer f.Close()
	var reader io.Reader = f
	if strings.HasSuffix(strings.ToLower(archive), ".gz") || strings.HasSuffix(strings.ToLower(archive), ".tgz") {
		gz, err := gzip.NewReader(f)
		if err != nil {
			return NewHTTPError(400, "压缩包格式错误")
		}
		defer gz.Close()
		reader = gz
	}
	tr := tar.NewReader(reader)
	base := filepath.Clean(dest)
	for {
		hdr, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return NewHTTPError(400, "压缩包格式错误")
		}
		name := filepath.FromSlash(hdr.Name)
		if !filepath.IsLocal(name) || filepath.IsAbs(name) {
			return NewHTTPError(400, "压缩包包含非法路径（Tar Slip）")
		}
		target := filepath.Join(base, name)
		if !withinBase(base, target) {
			return NewHTTPError(400, "压缩包包含非法路径（Tar Slip）")
		}
		if hdr.Typeflag == tar.TypeSymlink || hdr.Typeflag == tar.TypeLink {
			return NewHTTPError(400, "压缩包包含越界链接（Tar Slip）")
		}
		if hdr.Typeflag == tar.TypeChar || hdr.Typeflag == tar.TypeBlock || hdr.Typeflag == tar.TypeFifo {
			return NewHTTPError(400, "压缩包包含特殊文件，已拒绝")
		}
		if hdr.Typeflag == tar.TypeDir {
			if err := os.MkdirAll(target, 0o755); err != nil {
				return filesOpError(err, "解压失败")
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return filesOpError(err, "解压失败")
		}
		out, err := os.OpenFile(target, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, os.FileMode(hdr.Mode).Perm())
		if err != nil {
			return filesOpError(err, "解压失败")
		}
		if _, err := io.Copy(out, tr); err != nil {
			_ = out.Close()
			return filesOpError(err, "解压失败")
		}
		if err := out.Close(); err != nil {
			return filesOpError(err, "解压失败")
		}
	}
	return nil
}

// remoteCreateArchive 在裸 SSH 节点上用系统 tar/zip 生成压缩包。
func remoteCreateArchive(archive string, paths []string, format string) error {
	if err := HostMkdirAll(HostDir(archive), 0o755); err != nil {
		return err
	}
	var script strings.Builder
	// 覆盖写目标前先清掉旧压缩包；失败（含关键路径守卫拒绝）必须中止，不能继续写。
	if err := HostRemove(archive); err != nil {
		return err
	}
	switch format {
	case "zip":
		if HostWhich("zip") == "" {
			return NewHTTPError(400, "远程节点缺少 zip 命令")
		}
		for _, p := range paths {
			parent := path.Dir(p)
			base := path.Base(p)
			script.WriteString("cd " + shellQuote(parent) + " && zip -rq " + shellQuote(archive) + " " + shellQuote(base) + " || exit $?; ")
		}
	case "tar", "tar.gz":
		flag := "-cf"
		if format == "tar.gz" {
			flag = "-czf"
		}
		script.WriteString("tar " + flag + " " + shellQuote(archive))
		for _, p := range paths {
			script.WriteString(" -C " + shellQuote(path.Dir(p)) + " " + shellQuote(path.Base(p)))
		}
		script.WriteString(" || exit $?")
	default:
		return NewHTTPError(400, "不支持的压缩格式")
	}
	res := HostShell(600*time.Second, script.String())
	if res.Code != 0 {
		return fmt.Errorf("%s", strings.TrimSpace(res.Stderr+res.Stdout))
	}
	return nil
}

// remoteExtractArchive 在裸 SSH 节点解压，并先做 Zip Slip / Tar Slip 预检。
func remoteExtractArchive(archive, dest string, lower string) error {
	if err := HostMkdirAll(dest, 0o755); err != nil {
		return err
	}
	isZip := strings.HasSuffix(lower, ".zip")
	var listing CmdResult
	if isZip {
		if HostWhich("unzip") == "" {
			return NewHTTPError(400, "远程节点缺少 unzip 命令")
		}
		listing = HostCmd(60*time.Second, "unzip", "-Z1", archive)
	} else {
		listing = HostCmd(60*time.Second, "tar", "-tf", archive)
	}
	if listing.Code != 0 {
		return NewHTTPError(400, "压缩包格式错误")
	}
	for _, name := range strings.Split(listing.Stdout, "\n") {
		name = strings.TrimSpace(name)
		if name == "" {
			continue
		}
		clean := path.Clean("/" + strings.TrimLeft(name, "/"))
		if strings.HasPrefix(name, "/") || strings.Contains(name, "../") || clean == "/" {
			return NewHTTPError(400, "压缩包包含非法路径（Zip Slip）")
		}
	}
	var res CmdResult
	if isZip {
		res = HostCmd(600*time.Second, "unzip", "-o", archive, "-d", dest)
	} else {
		res = HostCmd(600*time.Second, "tar", "-xf", archive, "-C", dest, "--no-same-owner")
	}
	if res.Code != 0 {
		return NewHTTPError(500, "解压失败："+strings.TrimSpace(res.Stderr+res.Stdout))
	}
	return nil
}
