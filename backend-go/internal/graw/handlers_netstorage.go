package graw

import (
	"bufio"
	"bytes"
	"crypto/tls"
	"encoding/xml"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// 网络储存：FTP/FTPS（内置客户端）、WebDAV（HTTP）、S3（mc CLI）、SMB（smbclient CLI）。

func loadNetStorage() map[string]any {
	data := map[string]any{"connections": []any{}}
	_ = ReadJSON(DataPath("netstorage.json"), &data)
	if _, ok := data["connections"]; !ok {
		data["connections"] = []any{}
	}
	return data
}

func nsConnections(data map[string]any) []map[string]any {
	out := []map[string]any{}
	if arr, ok := data["connections"].([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok {
				out = append(out, m)
			}
		}
	}
	return out
}

func findNSConn(id string) (map[string]any, error) {
	for _, c := range nsConnections(loadNetStorage()) {
		if str(c["id"]) == id {
			return c, nil
		}
	}
	return nil, NewHTTPError(404, "连接不存在")
}

var (
	nsTypes     = map[string]bool{"ftp": true, "ftps": true, "smb": true, "webdav": true, "s3": true}
	nsNameRe    = regexp.MustCompile(`^[A-Za-z0-9_\-一-龥㐀-䶿 ]{1,64}$`)
	nsHostRe    = regexp.MustCompile(`^[A-Za-z0-9.\-]{1,255}$`)
	nsBucketRe  = regexp.MustCompile(`^[A-Za-z0-9._\-]{1,255}$`)
	nsShareRe   = regexp.MustCompile(`^[^/\\\r\n:]{1,255}$`)
	nsCtrlRe    = regexp.MustCompile("[\x00-\x1f\x7f]")
	nsDefaultPt = map[string]int{"ftp": 21, "ftps": 990, "smb": 445, "webdav": 443, "s3": 9000}
)

// nsBase 返回连接的基础路径；同时兼容前端/Python 的 base 与早期 Go 版本的 base_path。
func nsBase(conn map[string]any) string {
	return strings.TrimSpace(defaultString(str(conn["base"]), str(conn["base_path"])))
}

// nsParams 返回协议扩展参数（含 passive/secure/region）。
func nsParams(conn map[string]any) map[string]any { return mapOf(conn["params"]) }

func maskNSConn(c map[string]any) map[string]any {
	out := map[string]any{}
	for k, v := range c {
		switch k {
		case "password", "secret_key", "secret":
			// 凭据不回显，只给前端「已设置密码」标记（编辑时留空表示保持原值）。
			out["has_password"] = str(c["password"]) != ""
		default:
			out[k] = v
		}
	}
	if _, ok := out["has_password"]; !ok {
		out["has_password"] = str(c["password"]) != ""
	}
	return out
}

// nsValidateConn 校验连接配置（类型 / 主机 / 端口 / 凭据 / 基础路径 + SSRF 防护）。
// 与 Python netstorage.ConnectionIn.validate_rules 对齐；返回的 HTTPError 可直接上抛。
func nsValidateConn(conn map[string]any, isCreate bool) *HTTPError {
	typ := strings.TrimSpace(str(conn["type"]))
	if !nsTypes[typ] {
		return NewHTTPError(400, "不支持的存储类型")
	}
	conn["type"] = typ
	name := strings.TrimSpace(str(conn["name"]))
	if name == "" || !nsNameRe.MatchString(name) {
		return NewHTTPError(400, "名称包含非法字符或超过 64 字符")
	}
	conn["name"] = name
	if isCreate {
		conn["id"] = "ns_" + randomHex(10)
		conn["created_at"] = time.Now().Format("2006-01-02 15:04:05")
	}
	host := strings.TrimSpace(str(conn["host"]))
	base := strings.TrimSpace(nsBase(conn))
	// WebDAV 允许用完整 URL 作为 base（Python 兼容），此时 host 可为空；
	// 其余协议必须提供裸主机（字符白名单，防注入）。
	baseIsURL := strings.HasPrefix(base, "http://") || strings.HasPrefix(base, "https://")
	if !(typ == "webdav" && baseIsURL) {
		if host == "" || !nsHostRe.MatchString(host) {
			return NewHTTPError(400, "主机地址包含非法字符")
		}
	}
	conn["host"] = host
	port := int(int64Of(conn["port"]))
	if port == 0 {
		port = nsDefaultPt[typ]
	}
	if port < 1 || port > 65535 {
		return NewHTTPError(400, "端口必须在 1-65535")
	}
	conn["port"] = port
	username := str(conn["username"])
	password := str(conn["password"])
	if nsCtrlRe.MatchString(username) || len([]rune(username)) > 128 {
		return NewHTTPError(400, "用户名包含非法字符或超过 128 字符")
	}
	if nsCtrlRe.MatchString(password) || len([]rune(password)) > 256 {
		return NewHTTPError(400, "密码包含非法字符或超过 256 字符")
	}
	conn["username"], conn["password"] = username, password
	if len([]rune(base)) > 1024 || nsCtrlRe.MatchString(base) {
		return NewHTTPError(400, "基础路径包含非法字符或超过 1024 字符")
	}
	if _, ok := conn["params"]; !ok || conn["params"] == nil {
		conn["params"] = map[string]any{}
	}
	switch typ {
	case "smb":
		share := strings.Trim(base, "/")
		if share == "" || !nsShareRe.MatchString(share) || share == "." || share == ".." {
			return NewHTTPError(400, "SMB 共享名非法（base 必填且不能包含 / 或 :）")
		}
	case "s3":
		bucket := strings.Trim(base, "/")
		if bucket == "" || !nsBucketRe.MatchString(bucket) {
			return NewHTTPError(400, "S3 桶名非法（base 必填）")
		}
	case "webdav":
		if baseIsURL {
			if err := AssertSafeHTTPURL(base, true); err != nil {
				return NewHTTPError(400, "WebDAV 根地址校验失败："+err.Error())
			}
		} else if host != "" {
			if err := AssertSafeHost(host, true, false); err != nil {
				return NewHTTPError(400, err.Error())
			}
		}
	case "ftp", "ftps":
		if host == "" {
			return NewHTTPError(400, "主机不能为空")
		}
		if err := AssertSafeHost(host, true, false); err != nil {
			return NewHTTPError(400, err.Error())
		}
	}
	// 统一写入 base（并保留 base_path 兼容字段），避免内部读取时两套字段漂移。
	if base != "" {
		conn["base"] = base
	} else {
		delete(conn, "base")
	}
	conn["base_path"] = base
	return nil
}

// nsValidatePath 拒绝控制字符/换行（FTP 命令与 smbclient 命令串的注入源）。
func nsValidatePath(p string) error {
	if nsCtrlRe.MatchString(p) {
		return NewHTTPError(400, "路径包含非法字符")
	}
	return nil
}

func registerNetStorageRoutes(a *App) {
	a.Get("/api/netstorage/connections", perm("netstorage"), func(w http.ResponseWriter, r *http.Request) error {
		conns := []map[string]any{}
		for _, c := range nsConnections(loadNetStorage()) {
			conns = append(conns, maskNSConn(c))
		}
		return WriteJSON(w, 200, map[string]any{"connections": conns})
	})
	a.Post("/api/netstorage/connections", perm("netstorage"), func(w http.ResponseWriter, r *http.Request) error {
		var req map[string]any
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if p, ok := req["port"]; ok {
			req["port"] = int(num(p))
		}
		if err := nsValidateConn(req, true); err != nil {
			return err
		}
		data := loadNetStorage()
		data["connections"] = append(nsConnections(data), req)
		if err := WriteJSONFile(DataPath("netstorage.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, maskNSConn(req))
	})
	a.Put("/api/netstorage/connections/{cid}", perm("netstorage"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "cid")
		var req map[string]any
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if p, ok := req["port"]; ok {
			req["port"] = int(num(p))
		}
		data := loadNetStorage()
		conns := nsConnections(data)
		var conn map[string]any
		for _, c := range conns {
			if str(c["id"]) == id {
				conn = c
				for k, v := range req {
					if k == "password" || k == "secret_key" || k == "secret" {
						// 编辑时留空或占位符表示保持原凭据。
						if str(v) == "******" || strings.TrimSpace(str(v)) == "" {
							continue
						}
					}
					c[k] = v
				}
			}
		}
		if conn == nil {
			return NewHTTPError(404, "连接不存在")
		}
		if err := nsValidateConn(conn, false); err != nil {
			return err
		}
		data["connections"] = conns
		if err := WriteJSONFile(DataPath("netstorage.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, maskNSConn(conn))
	})
	a.Delete("/api/netstorage/connections/{cid}", perm("netstorage"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "cid")
		data := loadNetStorage()
		conns := nsConnections(data)
		kept := []map[string]any{}
		for _, c := range conns {
			if str(c["id"]) != id {
				kept = append(kept, c)
			}
		}
		data["connections"] = kept
		if err := WriteJSONFile(DataPath("netstorage.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/netstorage/connections/{cid}/test", perm("netstorage"), func(w http.ResponseWriter, r *http.Request) error {
		conn, err := findNSConn(PathParam(r, "cid"))
		if err != nil {
			return err
		}
		if err := nsTest(conn); err != nil {
			return NewHTTPError(502, err.Error())
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Get("/api/netstorage/connections/{cid}/list", perm("netstorage"), func(w http.ResponseWriter, r *http.Request) error {
		conn, err := findNSConn(PathParam(r, "cid"))
		if err != nil {
			return err
		}
		p := r.URL.Query().Get("path")
		items, err := nsList(conn, p)
		if err != nil {
			return NewHTTPError(502, err.Error())
		}
		return WriteJSON(w, 200, map[string]any{"path": p, "parent": parentOf(p), "items": items})
	})
	a.Get("/api/netstorage/connections/{cid}/read", perm("netstorage"), func(w http.ResponseWriter, r *http.Request) error {
		conn, err := findNSConn(PathParam(r, "cid"))
		if err != nil {
			return err
		}
		p := r.URL.Query().Get("path")
		data, err := nsRead(conn, p)
		if err != nil {
			return NewHTTPError(502, err.Error())
		}
		return WriteJSON(w, 200, map[string]any{"path": p, "content": string(data)})
	})
	a.Post("/api/netstorage/connections/{cid}/write", perm("netstorage"), func(w http.ResponseWriter, r *http.Request) error {
		conn, err := findNSConn(PathParam(r, "cid"))
		if err != nil {
			return err
		}
		var req struct {
			Path    string `json:"path"`
			Content string `json:"content"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if err := nsWrite(conn, req.Path, []byte(req.Content)); err != nil {
			return NewHTTPError(502, err.Error())
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/netstorage/connections/{cid}/mkdir", perm("netstorage"), func(w http.ResponseWriter, r *http.Request) error {
		conn, err := findNSConn(PathParam(r, "cid"))
		if err != nil {
			return err
		}
		var req struct {
			Path string `json:"path"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if err := nsMkdir(conn, req.Path); err != nil {
			return NewHTTPError(502, err.Error())
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/netstorage/connections/{cid}/delete", perm("netstorage"), func(w http.ResponseWriter, r *http.Request) error {
		conn, err := findNSConn(PathParam(r, "cid"))
		if err != nil {
			return err
		}
		var req struct {
			Path string `json:"path"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if err := nsDelete(conn, req.Path); err != nil {
			return NewHTTPError(502, err.Error())
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/netstorage/connections/{cid}/rename", perm("netstorage"), func(w http.ResponseWriter, r *http.Request) error {
		conn, err := findNSConn(PathParam(r, "cid"))
		if err != nil {
			return err
		}
		var req struct {
			Src string `json:"src"`
			Dst string `json:"dst"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if err := nsRename(conn, req.Src, req.Dst); err != nil {
			return NewHTTPError(502, err.Error())
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/netstorage/connections/{cid}/upload", perm("netstorage"), func(w http.ResponseWriter, r *http.Request) error {
		conn, err := findNSConn(PathParam(r, "cid"))
		if err != nil {
			return err
		}
		if err := r.ParseMultipartForm(32 << 20); err != nil {
			return NewHTTPError(400, "上传表单解析失败")
		}
		target := strings.TrimRight(r.FormValue("path"), "/") + "/" + filepath.Base(r.FormValue("name"))
		if r.FormValue("name") == "" {
			target = strings.TrimRight(r.FormValue("path"), "/") + "/" + filepath.Base(r.FormValue("file"))
		}
		file, header, err := r.FormFile("file")
		if err != nil {
			return NewHTTPError(400, "缺少上传文件")
		}
		defer file.Close()
		if r.FormValue("name") == "" && header != nil {
			target = strings.TrimRight(r.FormValue("path"), "/") + "/" + filepath.Base(header.Filename)
		}
		data, err := io.ReadAll(io.LimitReader(file, 64<<20))
		if err != nil {
			return NewHTTPError(400, "读取上传文件失败")
		}
		if err := nsWrite(conn, target, data); err != nil {
			return NewHTTPError(502, err.Error())
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "path": target})
	})
	a.Get("/api/netstorage/connections/{cid}/download", perm("netstorage"), func(w http.ResponseWriter, r *http.Request) error {
		conn, err := findNSConn(PathParam(r, "cid"))
		if err != nil {
			return err
		}
		p := r.URL.Query().Get("path")
		data, err := nsRead(conn, p)
		if err != nil {
			return NewHTTPError(502, err.Error())
		}
		w.Header().Set("Content-Disposition", "attachment; filename=\""+filepath.Base(p)+"\"")
		w.Header().Set("Content-Type", "application/octet-stream")
		_, err = w.Write(data)
		return err
	})
}

func parentOf(p string) any {
	p = strings.TrimRight(p, "/")
	if p == "" {
		return nil
	}
	d := path.Dir(p)
	if d == "." || d == "/" {
		return "/"
	}
	return d
}

func nsTest(conn map[string]any) error {
	switch str(conn["type"]) {
	case "ftp", "ftps":
		client, err := ftpDial(conn)
		if err != nil {
			return err
		}
		defer client.close()
		_, err = client.cmd("PWD")
		return err
	case "webdav":
		resp, err := webdavRequest(conn, "PROPFIND", "/", nil, map[string]string{"Depth": "0"})
		if err != nil {
			return err
		}
		defer resp.Body.Close()
		if resp.StatusCode >= 400 {
			return fmt.Errorf("WebDAV 返回 %d", resp.StatusCode)
		}
		return nil
	case "smb":
		addr := net.JoinHostPort(str(conn["host"]), itoa(int(int64Of(conn["port"]))))
		nc, err := net.DialTimeout("tcp", addr, 5*time.Second)
		if err != nil {
			return err
		}
		nc.Close()
		if HostWhich("smbclient") == "" {
			return fmt.Errorf("宿主机未安装 smbclient，无法执行 SMB 文件操作")
		}
		return nil
	case "s3":
		if HostWhich("mc") == "" {
			return fmt.Errorf("宿主机未安装 mc（MinIO Client），无法执行对象存储操作")
		}
		return nil
	}
	return fmt.Errorf("不支持的连接类型: %s", str(conn["type"]))
}

func nsList(conn map[string]any, p string) ([]map[string]any, error) {
	if err := nsValidatePath(p); err != nil {
		return nil, err
	}
	switch str(conn["type"]) {
	case "ftp", "ftps":
		client, err := ftpDial(conn)
		if err != nil {
			return nil, err
		}
		defer client.close()
		return client.list(p)
	case "webdav":
		return webdavList(conn, p)
	case "smb":
		return smbList(conn, p)
	case "s3":
		return s3List(conn, p)
	}
	return nil, fmt.Errorf("不支持的连接类型")
}

func nsRead(conn map[string]any, p string) ([]byte, error) {
	if err := nsValidatePath(p); err != nil {
		return nil, err
	}
	switch str(conn["type"]) {
	case "ftp", "ftps":
		client, err := ftpDial(conn)
		if err != nil {
			return nil, err
		}
		defer client.close()
		return client.read(p)
	case "webdav":
		resp, err := webdavRequest(conn, "GET", p, nil, nil)
		if err != nil {
			return nil, err
		}
		defer resp.Body.Close()
		if resp.StatusCode >= 400 {
			return nil, fmt.Errorf("读取失败: %d", resp.StatusCode)
		}
		return io.ReadAll(io.LimitReader(resp.Body, 64<<20))
	case "smb":
		quoted, err := smbQuote(p)
		if err != nil {
			return nil, err
		}
		return smbclient(conn, "get "+quoted+" -")
	case "s3":
		return mcRun(conn, "cat", s3Path(conn, p))
	}
	return nil, fmt.Errorf("不支持的连接类型")
}

func nsWrite(conn map[string]any, p string, data []byte) error {
	if err := nsValidatePath(p); err != nil {
		return err
	}
	switch str(conn["type"]) {
	case "ftp", "ftps":
		client, err := ftpDial(conn)
		if err != nil {
			return err
		}
		defer client.close()
		return client.write(p, data)
	case "webdav":
		resp, err := webdavRequest(conn, "PUT", p, bytes.NewReader(data), nil)
		if err != nil {
			return err
		}
		defer resp.Body.Close()
		if resp.StatusCode >= 400 {
			return fmt.Errorf("写入失败: %d", resp.StatusCode)
		}
		return nil
	case "smb":
		tmp, err := os.CreateTemp("", "graw-smb-*")
		if err != nil {
			return err
		}
		defer os.Remove(tmp.Name())
		_, _ = tmp.Write(data)
		tmp.Close()
		quoted, err := smbQuote(p)
		if err != nil {
			return err
		}
		_, err = smbclient(conn, "put "+strconv.Quote(tmp.Name())+" "+quoted)
		return err
	case "s3":
		tmp, err := os.CreateTemp("", "graw-s3-*")
		if err != nil {
			return err
		}
		defer os.Remove(tmp.Name())
		_, _ = tmp.Write(data)
		tmp.Close()
		_, err = mcRun(conn, "cp", tmp.Name(), s3Path(conn, p))
		return err
	}
	return fmt.Errorf("不支持的连接类型")
}

func nsMkdir(conn map[string]any, p string) error {
	if err := nsValidatePath(p); err != nil {
		return err
	}
	switch str(conn["type"]) {
	case "ftp", "ftps":
		client, err := ftpDial(conn)
		if err != nil {
			return err
		}
		defer client.close()
		_, err = client.cmd("MKD " + client.fullPath(p))
		return err
	case "webdav":
		resp, err := webdavRequest(conn, "MKCOL", p, nil, nil)
		if err != nil {
			return err
		}
		defer resp.Body.Close()
		if resp.StatusCode >= 400 && resp.StatusCode != 405 {
			return fmt.Errorf("创建目录失败: %d", resp.StatusCode)
		}
		return nil
	case "smb":
		quoted, err := smbQuote(p)
		if err != nil {
			return err
		}
		_, err = smbclient(conn, "mkdir "+quoted)
		return err
	case "s3":
		_, err := mcRun(conn, "mb", s3Path(conn, p))
		return err
	}
	return fmt.Errorf("不支持的连接类型")
}

func nsDelete(conn map[string]any, p string) error {
	if err := nsValidatePath(p); err != nil {
		return err
	}
	switch str(conn["type"]) {
	case "ftp", "ftps":
		client, err := ftpDial(conn)
		if err != nil {
			return err
		}
		defer client.close()
		if _, err := client.cmd("DELE " + client.fullPath(p)); err != nil {
			_, err2 := client.cmd("RMD " + client.fullPath(p))
			return err2
		}
		return nil
	case "webdav":
		resp, err := webdavRequest(conn, "DELETE", p, nil, nil)
		if err != nil {
			return err
		}
		defer resp.Body.Close()
		if resp.StatusCode >= 400 {
			return fmt.Errorf("删除失败: %d", resp.StatusCode)
		}
		return nil
	case "smb":
		quoted, err := smbQuote(p)
		if err != nil {
			return err
		}
		_, err = smbclient(conn, "del "+quoted)
		if err != nil {
			_, err = smbclient(conn, "rmdir "+quoted)
		}
		return err
	case "s3":
		_, err := mcRun(conn, "rm", "--recursive", "--force", s3Path(conn, p))
		return err
	}
	return fmt.Errorf("不支持的连接类型")
}

func nsRename(conn map[string]any, src, dst string) error {
	if err := nsValidatePath(src); err != nil {
		return err
	}
	if err := nsValidatePath(dst); err != nil {
		return err
	}
	switch str(conn["type"]) {
	case "ftp", "ftps":
		client, err := ftpDial(conn)
		if err != nil {
			return err
		}
		defer client.close()
		if _, err := client.cmd("RNFR " + client.fullPath(src)); err != nil {
			return err
		}
		_, err = client.cmd("RNTO " + client.fullPath(dst))
		return err
	case "webdav":
		resp, err := webdavRequest(conn, "MOVE", src, nil, map[string]string{"Destination": webdavURL(conn, dst)})
		if err != nil {
			return err
		}
		defer resp.Body.Close()
		if resp.StatusCode >= 400 {
			return fmt.Errorf("重命名失败: %d", resp.StatusCode)
		}
		return nil
	case "smb":
		qs, err := smbQuote(src)
		if err != nil {
			return err
		}
		qd, err := smbQuote(dst)
		if err != nil {
			return err
		}
		_, err = smbclient(conn, "rename "+qs+" "+qd)
		return err
	case "s3":
		_, err := mcRun(conn, "mv", s3Path(conn, src), s3Path(conn, dst))
		return err
	}
	return fmt.Errorf("不支持的连接类型")
}

// --- FTP 客户端 ---

type ftpClient struct {
	c    net.Conn
	r    *bufio.Reader
	sep  string
	host string
	base string
}

// ftpFullPath 把逻辑路径拼到连接的基础目录上（与 Python FTPAdapter._full 一致）。
func (f *ftpClient) fullPath(p string) string {
	p = strings.Trim(p, "/")
	b := strings.Trim(f.base, "/")
	if b == "" {
		return "/" + p
	}
	if p == "" {
		return "/" + b
	}
	return "/" + b + "/" + p
}

// ftpDial 建立 FTP/FTPS 连接。FTPS 采用显式 AUTH TLS（与 Python ftplib.FTP_TLS 一致）；
// 连接前重新校验主机（拒绝云元数据等受保护地址），并在发送命令前拒绝 CRLF 注入。
func ftpDial(conn map[string]any) (*ftpClient, error) {
	host := str(conn["host"])
	if err := AssertSafeHost(host, true, false); err != nil {
		return nil, err
	}
	user, pass := str(conn["username"]), str(conn["password"])
	if nsCtrlRe.MatchString(user) || nsCtrlRe.MatchString(pass) {
		return nil, NewHTTPError(400, "FTP 用户名/密码包含控制字符")
	}
	port := int(int64Of(conn["port"]))
	if port == 0 {
		if str(conn["type"]) == "ftps" {
			port = 990
		} else {
			port = 21
		}
	}
	addr := net.JoinHostPort(host, itoa(port))
	nc, err := net.DialTimeout("tcp", addr, 8*time.Second)
	if err != nil {
		return nil, err
	}
	fc := &ftpClient{c: nc, r: bufio.NewReader(nc), sep: "/", host: host, base: nsBase(conn)}
	if _, err := fc.readReply(); err != nil {
		nc.Close()
		return nil, err
	}
	if str(conn["type"]) == "ftps" {
		// 显式 TLS：AUTH TLS 成功后整条控制通道加密，再走 USER/PASS。
		if _, err := fc.cmd("AUTH TLS"); err != nil {
			nc.Close()
			return nil, err
		}
		tlsConn := tls.Client(nc, &tls.Config{ServerName: host, MinVersion: tls.VersionTLS12})
		if err := tlsConn.Handshake(); err != nil {
			nc.Close()
			return nil, err
		}
		fc.c = tlsConn
		fc.r = bufio.NewReader(tlsConn)
	}
	if user != "" {
		if _, err := fc.cmd("USER " + user); err != nil {
			// 部分服务器在 USER 成功时返回 230，无需 PASS；331 才继续
			if !strings.HasPrefix(err.Error(), "331") {
				nc.Close()
				return nil, err
			}
		}
		if _, err := fc.cmd("PASS " + pass); err != nil {
			nc.Close()
			return nil, err
		}
	}
	if str(conn["type"]) == "ftps" {
		_, _ = fc.cmd("PBSZ 0")
		_, _ = fc.cmd("PROT P")
	}
	_, _ = fc.cmd("TYPE I")
	return fc, nil
}

func (f *ftpClient) close() { _ = f.c.Close() }

func (f *ftpClient) readReply() (string, error) {
	line, err := f.r.ReadString('\n')
	if err != nil {
		return "", err
	}
	reply := strings.TrimRight(line, "\r\n")
	if len(reply) >= 4 && reply[3] == '-' {
		code := reply[:3]
		for {
			next, err := f.r.ReadString('\n')
			if err != nil {
				return "", err
			}
			reply += "\n" + strings.TrimRight(next, "\r\n")
			if strings.HasPrefix(next, code+" ") {
				break
			}
		}
	}
	if len(reply) < 3 {
		return reply, fmt.Errorf("FTP 响应非法")
	}
	code, _ := strconv.Atoi(reply[:3])
	if code >= 400 {
		return reply, fmt.Errorf("%s", reply)
	}
	return reply, nil
}

func (f *ftpClient) cmd(command string) (string, error) {
	if _, err := f.c.Write([]byte(command + "\r\n")); err != nil {
		return "", err
	}
	return f.readReply()
}

func (f *ftpClient) dataConn() (net.Conn, error) {
	reply, err := f.cmd("PASV")
	if err != nil {
		return nil, err
	}
	re := regexp.MustCompile(`\((\d+),(\d+),(\d+),(\d+),(\d+),(\d+)\)`)
	m := re.FindStringSubmatch(reply)
	if len(m) != 7 {
		return nil, fmt.Errorf("无法解析 PASV 响应")
	}
	h1, _ := strconv.Atoi(m[1])
	h2, _ := strconv.Atoi(m[2])
	h3, _ := strconv.Atoi(m[3])
	h4, _ := strconv.Atoi(m[4])
	p1, _ := strconv.Atoi(m[5])
	p2, _ := strconv.Atoi(m[6])
	host := fmt.Sprintf("%d.%d.%d.%d", h1, h2, h3, h4)
	port := p1*256 + p2
	// 忽略服务器在 PASV 响应里给出的地址，始终连回原主机：恶意 FTP 服务
	// 可借此把数据连接指向 169.254.169.254 等受保护地址（FTP bounce / SSRF）。
	if f.host != "" {
		host = strings.Trim(f.host, "[]")
	}
	return net.DialTimeout("tcp", net.JoinHostPort(host, itoa(port)), 8*time.Second)
}

func (f *ftpClient) list(p string) ([]map[string]any, error) {
	dc, err := f.dataConn()
	if err != nil {
		return nil, err
	}
	defer dc.Close()
	if _, err := f.cmd("LIST " + f.fullPath(p)); err != nil {
		return nil, err
	}
	data, _ := io.ReadAll(io.LimitReader(dc, 8<<20))
	items := []map[string]any{}
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimRight(line, "\r")
		if line == "" {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 9 {
			continue
		}
		name := strings.Join(fields[8:], " ")
		if name == "." || name == ".." {
			continue
		}
		size, _ := strconv.ParseInt(fields[4], 10, 64)
		items = append(items, map[string]any{
			"name": name, "path": strings.TrimRight(p, "/") + "/" + name,
			"is_dir": strings.HasPrefix(fields[0], "d"), "size": size, "modified": 0,
		})
	}
	return items, nil
}

func (f *ftpClient) read(p string) ([]byte, error) {
	dc, err := f.dataConn()
	if err != nil {
		return nil, err
	}
	defer dc.Close()
	if _, err := f.cmd("RETR " + f.fullPath(p)); err != nil {
		return nil, err
	}
	data, err := io.ReadAll(io.LimitReader(dc, 64<<20))
	if _, rerr := f.readReply(); rerr != nil && err == nil {
		return nil, rerr
	}
	return data, err
}

func (f *ftpClient) write(p string, data []byte) error {
	dc, err := f.dataConn()
	if err != nil {
		return err
	}
	if _, err := f.cmd("STOR " + f.fullPath(p)); err != nil {
		dc.Close()
		return err
	}
	if _, err := dc.Write(data); err != nil {
		dc.Close()
		return err
	}
	dc.Close()
	_, err = f.readReply()
	return err
}

// --- WebDAV ---

func webdavBase(conn map[string]any) string {
	base := nsBase(conn)
	if strings.HasPrefix(base, "http://") || strings.HasPrefix(base, "https://") {
		// 前端允许直接填写完整的 WebDAV 根地址（如 https://host/dav/user/）。
		return strings.TrimRight(base, "/")
	}
	scheme := "http"
	if boolOf(conn["secure"]) || boolOf(conn["tls"]) {
		scheme = "https"
	}
	host := str(conn["host"])
	port := int(int64Of(conn["port"]))
	if port > 0 && !((scheme == "http" && port == 80) || (scheme == "https" && port == 443)) {
		host = net.JoinHostPort(host, itoa(port))
	}
	return scheme + "://" + host + "/" + strings.TrimLeft(strings.Trim(base, "/"), "/")
}

func webdavURL(conn map[string]any, p string) string {
	u, _ := url.Parse(webdavBase(conn))
	u.Path = strings.TrimRight(u.Path, "/") + "/" + strings.TrimLeft(p, "/")
	return u.String()
}

// webdavRequest 执行 WebDAV 请求。
// SSRF 防护：每次请求前校验目标（内网 WebDAV 允许私网，但拒绝回环/链路本地/元数据）；
// 禁止跟随 3xx（重定向可绕过初始 URL 校验，直达受保护地址）。
func webdavRequest(conn map[string]any, method, p string, body io.Reader, headers map[string]string) (*http.Response, error) {
	target := webdavURL(conn, p)
	if err := AssertSafeHTTPURL(target, true); err != nil {
		return nil, err
	}
	req, err := http.NewRequest(method, target, body)
	if err != nil {
		return nil, err
	}
	if user := str(conn["username"]); user != "" {
		req.SetBasicAuth(user, str(conn["password"]))
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := NewSafeHTTPClient(30*time.Second, true, false, false).Do(req)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode >= 300 && resp.StatusCode < 400 {
		resp.Body.Close()
		return nil, NewHTTPError(400, "检测到重定向跳转，已拒绝（SSRF 防护）")
	}
	return resp, nil
}

type davMultistatus struct {
	Responses []struct {
		Href     string `xml:"href"`
		Propstat []struct {
			Prop struct {
				ResourceType struct {
					Collection *struct{} `xml:"collection"`
				} `xml:"resourcetype"`
				ContentLength string `xml:"getcontentlength"`
				LastModified  string `xml:"getlastmodified"`
			} `xml:"prop"`
			Status string `xml:"status"`
		} `xml:"propstat"`
	} `xml:"response"`
}

func webdavList(conn map[string]any, p string) ([]map[string]any, error) {
	body := `<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:getcontentlength/><d:getlastmodified/></d:prop></d:propfind>`
	resp, err := webdavRequest(conn, "PROPFIND", p, strings.NewReader(body), map[string]string{
		"Depth": "1", "Content-Type": "application/xml",
	})
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("WebDAV 列表失败: %d", resp.StatusCode)
	}
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	var ms davMultistatus
	if err := xml.Unmarshal(data, &ms); err != nil {
		return nil, fmt.Errorf("解析 WebDAV 响应失败")
	}
	basePath := strings.TrimRight(p, "/")
	items := []map[string]any{}
	for _, r := range ms.Responses {
		href, err := url.PathUnescape(r.Href)
		if err != nil {
			href = r.Href
		}
		name := path.Base(strings.TrimRight(href, "/"))
		if strings.TrimRight(href, "/") == strings.TrimRight(basePath, "/") || name == "" {
			continue
		}
		isDir := false
		size := int64(0)
		modified := ""
		for _, ps := range r.Propstat {
			if ps.Prop.ResourceType.Collection != nil {
				isDir = true
			}
			if ps.Prop.ContentLength != "" {
				size, _ = strconv.ParseInt(ps.Prop.ContentLength, 10, 64)
			}
			modified = ps.Prop.LastModified
		}
		itemPath := strings.TrimRight(p, "/") + "/" + name
		if isDir {
			itemPath += "/"
		}
		items = append(items, map[string]any{
			"name": name, "path": itemPath, "is_dir": isDir, "size": size, "modified": modified,
		})
	}
	return items, nil
}

// --- SMB / S3 CLI ---

// smbQuote 为 smbclient -c 命令串里的路径加双引号，并拒绝可逃逸命令语法的字符。
// smbclient 的命令串支持 ; 分隔命令与 ! 执行本地 shell，路径可控时必须在入口净化。
func smbQuote(p string) (string, error) {
	if nsCtrlRe.MatchString(p) || strings.ContainsAny(p, "\";!\\") {
		return "", NewHTTPError(400, "SMB 路径包含非法字符")
	}
	return "\"" + p + "\"", nil
}

func smbclient(conn map[string]any, command string) ([]byte, error) {
	host := str(conn["host"])
	if err := AssertSafeHost(host, true, false); err != nil {
		return nil, err
	}
	bin := HostWhich("smbclient")
	if bin == "" {
		return nil, fmt.Errorf("宿主机未安装 smbclient")
	}
	share := strings.Trim(nsBase(conn), "/")
	if share == "" {
		return nil, fmt.Errorf("SMB 连接需填写共享名（base）")
	}
	user, pass := str(conn["username"]), str(conn["password"])
	if nsCtrlRe.MatchString(user) || nsCtrlRe.MatchString(pass) {
		return nil, NewHTTPError(400, "SMB 用户名/密码包含控制字符")
	}
	args := []string{"//" + host + "/" + share, "-U", user + "%" + pass, "-c", command}
	res := runCommand(60*time.Second, bin, args...)
	if res.Code != 0 {
		return nil, fmt.Errorf("%s", strings.TrimSpace(res.Stderr+res.Stdout))
	}
	return []byte(res.Stdout), nil
}

func smbList(conn map[string]any, p string) ([]map[string]any, error) {
	// smbclient 无稳定机器可读列表格式：用 recurse + ls 解析，失败时返回空并提示。
	out, err := smbclient(conn, "ls \""+p+"\"")
	if err != nil {
		return nil, err
	}
	items := []map[string]any{}
	for _, line := range strings.Split(string(out), "\n") {
		line = strings.TrimSpace(line)
		fields := strings.Fields(line)
		if len(fields) < 1 {
			continue
		}
		name := fields[0]
		if name == "." || name == ".." {
			continue
		}
		isDir := strings.HasPrefix(line, "  ")
		items = append(items, map[string]any{
			"name": name, "path": strings.TrimRight(p, "/") + "/" + name,
			"is_dir": isDir, "size": 0, "modified": 0,
		})
	}
	return items, nil
}

func s3Alias(conn map[string]any) string {
	return "graw_ns_" + str(conn["id"])
}

func s3Path(conn map[string]any, p string) string {
	bucket := strings.Trim(nsBase(conn), "/")
	return s3Alias(conn) + "/" + bucket + "/" + strings.TrimLeft(p, "/")
}

func mcRun(conn map[string]any, args ...string) ([]byte, error) {
	host := str(conn["host"])
	if err := AssertSafeHost(host, true, false); err != nil {
		return nil, err
	}
	bin := HostWhich("mc")
	if bin == "" {
		return nil, fmt.Errorf("宿主机未安装 mc（MinIO Client）")
	}
	port := int(int64Of(conn["port"]))
	// secure 优先取 params.secure（前端表单字段）；缺省按端口 443 判定（与 Python 一致）。
	secure := port == 443
	if v, ok := nsParams(conn)["secure"].(bool); ok {
		secure = v
	}
	scheme := "http"
	if secure {
		scheme = "https"
	}
	if port > 0 {
		host = net.JoinHostPort(host, itoa(port))
	}
	// 前端用 username/password 填写 S3 AccessKey/SecretKey。
	aliasURL := scheme + "://" + url.QueryEscape(str(conn["username"])) + ":" + url.QueryEscape(str(conn["password"])) + "@" + host
	cmd := exec.Command(bin, args...)
	cmd.Env = append(os.Environ(), "MC_HOST_"+s3Alias(conn)+"="+aliasURL)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("%s", strings.TrimSpace(stderr.String()+stdout.String()))
	}
	return stdout.Bytes(), nil
}

func s3List(conn map[string]any, p string) ([]map[string]any, error) {
	out, err := mcRun(conn, "ls", s3Path(conn, p))
	if err != nil {
		return nil, err
	}
	items := []map[string]any{}
	for _, line := range strings.Split(string(out), "\n") {
		line = strings.TrimRight(line, "\r")
		if strings.TrimSpace(line) == "" {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 4 {
			continue
		}
		isDir := strings.HasPrefix(line, "[")
		name := fields[len(fields)-1]
		size, _ := strconv.ParseInt(fields[len(fields)-2], 10, 64)
		item := map[string]any{
			"name": name, "path": strings.TrimRight(p, "/") + "/" + name,
			"is_dir": isDir, "size": size, "modified": strings.Join(fields[:2], " "),
		}
		items = append(items, item)
	}
	return items, nil
}
