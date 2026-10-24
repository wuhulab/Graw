package graw

import (
	"bufio"
	"bytes"
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

func maskNSConn(c map[string]any) map[string]any {
	out := map[string]any{}
	for k, v := range c {
		if k == "password" || k == "secret_key" || k == "secret" {
			if str(v) != "" {
				out[k] = "******"
			} else {
				out[k] = ""
			}
		} else {
			out[k] = v
		}
	}
	return out
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
		if strings.TrimSpace(str(req["name"])) == "" || str(req["type"]) == "" {
			return NewHTTPError(400, "名称与类型不能为空")
		}
		req["id"] = "ns_" + randomHex(8)
		req["created_at"] = time.Now().Format("2006-01-02 15:04:05")
		if p, ok := req["port"]; ok {
			req["port"] = int(num(p))
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
		data := loadNetStorage()
		conns := nsConnections(data)
		var conn map[string]any
		for _, c := range conns {
			if str(c["id"]) == id {
				conn = c
				for k, v := range req {
					if k == "password" || k == "secret_key" || k == "secret" {
						if str(v) == "******" {
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
		out, err := smbclient(conn, "get \""+p+"\" -")
		return out, err
	case "s3":
		return mcRun(conn, "cat", s3Path(conn, p))
	}
	return nil, fmt.Errorf("不支持的连接类型")
}

func nsWrite(conn map[string]any, p string, data []byte) error {
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
		_, err = smbclient(conn, "put \""+tmp.Name()+"\" \""+p+"\"")
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
	switch str(conn["type"]) {
	case "ftp", "ftps":
		client, err := ftpDial(conn)
		if err != nil {
			return err
		}
		defer client.close()
		_, err = client.cmd("MKD " + p)
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
		_, err := smbclient(conn, "mkdir \""+p+"\"")
		return err
	case "s3":
		_, err := mcRun(conn, "mb", s3Path(conn, p))
		return err
	}
	return fmt.Errorf("不支持的连接类型")
}

func nsDelete(conn map[string]any, p string) error {
	switch str(conn["type"]) {
	case "ftp", "ftps":
		client, err := ftpDial(conn)
		if err != nil {
			return err
		}
		defer client.close()
		if _, err := client.cmd("DELE " + p); err != nil {
			_, err2 := client.cmd("RMD " + p)
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
		_, err := smbclient(conn, "del \""+p+"\"")
		if err != nil {
			_, err = smbclient(conn, "rmdir \""+p+"\"")
		}
		return err
	case "s3":
		_, err := mcRun(conn, "rm", "--recursive", "--force", s3Path(conn, p))
		return err
	}
	return fmt.Errorf("不支持的连接类型")
}

func nsRename(conn map[string]any, src, dst string) error {
	switch str(conn["type"]) {
	case "ftp", "ftps":
		client, err := ftpDial(conn)
		if err != nil {
			return err
		}
		defer client.close()
		if _, err := client.cmd("RNFR " + src); err != nil {
			return err
		}
		_, err = client.cmd("RNTO " + dst)
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
		_, err := smbclient(conn, "rename \""+src+"\" \""+dst+"\"")
		return err
	case "s3":
		_, err := mcRun(conn, "mv", s3Path(conn, src), s3Path(conn, dst))
		return err
	}
	return fmt.Errorf("不支持的连接类型")
}

// --- FTP 客户端 ---

type ftpClient struct {
	c   net.Conn
	r   *bufio.Reader
	sep string
}

func ftpDial(conn map[string]any) (*ftpClient, error) {
	port := int(int64Of(conn["port"]))
	if port == 0 {
		port = 21
	}
	addr := net.JoinHostPort(str(conn["host"]), itoa(port))
	nc, err := net.DialTimeout("tcp", addr, 8*time.Second)
	if err != nil {
		return nil, err
	}
	fc := &ftpClient{c: nc, r: bufio.NewReader(nc), sep: "/"}
	if _, err := fc.readReply(); err != nil {
		nc.Close()
		return nil, err
	}
	if _, err := fc.cmd("USER " + str(conn["username"])); err != nil {
		// 部分服务器在 USER 成功时返回 230，无需 PASS；331 才继续
		if !strings.HasPrefix(err.Error(), "331") {
			// 继续尝试 PASS
		}
	}
	if _, err := fc.cmd("PASS " + str(conn["password"])); err != nil {
		nc.Close()
		return nil, err
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
	return net.DialTimeout("tcp", net.JoinHostPort(host, itoa(port)), 8*time.Second)
}

func (f *ftpClient) list(p string) ([]map[string]any, error) {
	dc, err := f.dataConn()
	if err != nil {
		return nil, err
	}
	defer dc.Close()
	if _, err := f.cmd("LIST " + p); err != nil {
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
	if _, err := f.cmd("RETR " + p); err != nil {
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
	if _, err := f.cmd("STOR " + p); err != nil {
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
	scheme := "http"
	if boolOf(conn["secure"]) || boolOf(conn["tls"]) {
		scheme = "https"
	}
	host := str(conn["host"])
	port := int(int64Of(conn["port"]))
	if port > 0 && !((scheme == "http" && port == 80) || (scheme == "https" && port == 443)) {
		host = net.JoinHostPort(host, itoa(port))
	}
	base := strings.TrimRight(str(conn["base_path"]), "/")
	return scheme + "://" + host + "/" + strings.TrimLeft(base, "/")
}

func webdavURL(conn map[string]any, p string) string {
	u, _ := url.Parse(webdavBase(conn))
	u.Path = strings.TrimRight(u.Path, "/") + "/" + strings.TrimLeft(p, "/")
	return u.String()
}

func webdavRequest(conn map[string]any, method, p string, body io.Reader, headers map[string]string) (*http.Response, error) {
	req, err := http.NewRequest(method, webdavURL(conn, p), body)
	if err != nil {
		return nil, err
	}
	if user := str(conn["username"]); user != "" {
		req.SetBasicAuth(user, str(conn["password"]))
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	client := &http.Client{Timeout: 30 * time.Second}
	return client.Do(req)
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

func smbclient(conn map[string]any, command string) ([]byte, error) {
	bin := HostWhich("smbclient")
	if bin == "" {
		return nil, fmt.Errorf("宿主机未安装 smbclient")
	}
	share := strings.Trim(str(conn["base_path"]), "/")
	if share == "" {
		return nil, fmt.Errorf("SMB 连接需填写共享名（base_path）")
	}
	args := []string{"//" + str(conn["host"]) + "/" + share, "-U", str(conn["username"]) + "%" + str(conn["password"]), "-c", command}
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
	bucket := strings.Trim(str(conn["base_path"]), "/")
	return s3Alias(conn) + "/" + bucket + "/" + strings.TrimLeft(p, "/")
}

func mcRun(conn map[string]any, args ...string) ([]byte, error) {
	bin := HostWhich("mc")
	if bin == "" {
		return nil, fmt.Errorf("宿主机未安装 mc（MinIO Client）")
	}
	scheme := "https"
	if boolOf(conn["insecure"]) || !boolOf(conn["secure"]) {
		scheme = "http"
	}
	host := str(conn["host"])
	if port := int(int64Of(conn["port"])); port > 0 {
		host = net.JoinHostPort(host, itoa(port))
	}
	aliasURL := scheme + "://" + url.QueryEscape(str(conn["access_key"])) + ":" + url.QueryEscape(str(conn["secret_key"])) + "@" + host
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
