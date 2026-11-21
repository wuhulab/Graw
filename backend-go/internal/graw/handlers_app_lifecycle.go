package graw

import (
	"archive/tar"
	"compress/gzip"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

// 应用生命周期（P1）：把应用商店安装出来的 compose 应用纳入可运维闭环。
//
// 应用项目目录固定为 data/appstore/<appName>/docker-compose.yml（安装时写入），
// 因此这里以「目录名」作为应用标识；容器通过 compose 标签
// com.docker.compose.project=<appName> 关联。
//
// 安全约束：
//   - 应用名必须匹配 appIDRe，任何路径拼接前先校验，杜绝路径穿越；
//   - 备份归档只写入 data/appstore_backups，解包时拒绝绝对路径与 ..；
//   - compose 内容先解析 YAML 且必须包含 services，写前存快照，应用失败回滚。

var appBackupNameRe = regexp.MustCompile(`^\d{8}_\d{6}\.tar\.gz$`)
var composeImageRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,254}$`)

func installedAppRoot() string {
	return DataPath("appstore")
}

func appBackupRoot() string {
	return DataPath("appstore_backups")
}

// installedAppDir 校验应用名并返回项目目录（不保证存在）。
func installedAppDir(name string) (string, error) {
	if !appIDRe.MatchString(name) {
		return "", NewHTTPError(400, "应用名称非法")
	}
	root := installedAppRoot()
	dir := filepath.Join(root, name)
	absRoot, err := filepath.Abs(root)
	if err != nil {
		return "", err
	}
	absDir, err := filepath.Abs(dir)
	if err != nil {
		return "", err
	}
	if absDir != filepath.Join(absRoot, name) {
		return "", NewHTTPError(400, "应用路径非法")
	}
	return absDir, nil
}

func installedComposePath(name string) (string, error) {
	dir, err := installedAppDir(name)
	if err != nil {
		return "", err
	}
	path := filepath.Join(dir, "docker-compose.yml")
	if !fileExists(path) {
		return "", NewHTTPError(404, "应用不存在或缺少 docker-compose.yml")
	}
	return path, nil
}

func fileExists(path string) bool {
	st, err := os.Stat(path)
	return err == nil && !st.IsDir()
}

// composeServiceImages 从 compose YAML 中提取服务名与镜像。
func composeServiceImages(text string) ([]map[string]any, error) {
	var data map[string]any
	if err := yaml.Unmarshal([]byte(text), &data); err != nil {
		return nil, err
	}
	services := mapOf(data["services"])
	if len(services) == 0 {
		return nil, fmt.Errorf("缺少 services 定义")
	}
	names := make([]string, 0, len(services))
	for name := range services {
		names = append(names, name)
	}
	sort.Strings(names)
	out := []map[string]any{}
	for _, name := range names {
		svc := mapOf(services[name])
		out = append(out, map[string]any{
			"name":  name,
			"image": str(svc["image"]),
		})
	}
	return out, nil
}

// composeProjectContainers 查询属于该 compose 项目的容器。
func composeProjectContainers(name string) []map[string]any {
	items, res := dockerJSONLines("ps", "-a",
		"--filter", "label=com.docker.compose.project="+name,
		"--format", "json")
	if res.Code != 0 {
		return []map[string]any{}
	}
	out := []map[string]any{}
	for _, item := range items {
		out = append(out, map[string]any{
			"name":   firstNonEmpty(str(item["Names"]), str(item["Name"])),
			"image":  str(item["Image"]),
			"state":  str(item["State"]),
			"status": str(item["Status"]),
			"ports":  str(item["Ports"]),
		})
	}
	return out
}

var publishedPortRe = regexp.MustCompile(`(?:0\.0\.0\.0:|\[::\]:|:::)?(\d+)->(\d+)/(tcp|udp)`)

// composePublishedPorts 汇总容器发布到宿主机的端口。
func composePublishedPorts(containers []map[string]any) []map[string]any {
	seen := map[string]bool{}
	out := []map[string]any{}
	for _, c := range containers {
		for _, m := range publishedPortRe.FindAllStringSubmatch(str(c["ports"]), -1) {
			key := m[1] + "/" + m[3]
			if seen[key] {
				continue
			}
			seen[key] = true
			out = append(out, map[string]any{
				"external": int64Of(m[1]),
				"internal": int64Of(m[2]),
				"protocol": m[3],
			})
		}
	}
	sort.Slice(out, func(i, j int) bool {
		return int64Of(out[i]["external"]) < int64Of(out[j]["external"])
	})
	return out
}

func installedAppSummary(name, dir, composePath string) map[string]any {
	text, _ := os.ReadFile(composePath)
	services, _ := composeServiceImages(string(text))
	containers := composeProjectContainers(name)
	running := 0
	for _, c := range containers {
		if strings.EqualFold(str(c["state"]), "running") {
			running++
		}
	}
	status := "stopped"
	if len(containers) > 0 && running == len(containers) {
		status = "running"
	} else if running > 0 {
		status = "partial"
	}
	st, _ := os.Stat(composePath)
	updated := ""
	if st != nil {
		updated = st.ModTime().Format("2006-01-02 15:04:05")
	}
	return map[string]any{
		"name":       name,
		"dir":        dir,
		"status":     status,
		"services":   services,
		"containers": containers,
		"ports":      composePublishedPorts(containers),
		"updated_at": updated,
	}
}

func listInstalledApps() []map[string]any {
	entries, err := os.ReadDir(installedAppRoot())
	if err != nil {
		return []map[string]any{}
	}
	out := []map[string]any{}
	for _, e := range entries {
		if !e.IsDir() || !appIDRe.MatchString(e.Name()) {
			continue
		}
		dir := filepath.Join(installedAppRoot(), e.Name())
		composePath := filepath.Join(dir, "docker-compose.yml")
		if !fileExists(composePath) {
			continue
		}
		out = append(out, installedAppSummary(e.Name(), dir, composePath))
	}
	sort.Slice(out, func(i, j int) bool { return str(out[i]["name"]) < str(out[j]["name"]) })
	return out
}

func installedAppDetail(name string) (map[string]any, error) {
	dir, err := installedAppDir(name)
	if err != nil {
		return nil, err
	}
	composePath, err := installedComposePath(name)
	if err != nil {
		return nil, err
	}
	summary := installedAppSummary(name, dir, composePath)
	text, _ := os.ReadFile(composePath)
	summary["compose"] = string(text)
	summary["backups"] = listAppBackups(name)
	return summary, nil
}

// appComposeAction 执行 compose 动作；rebuild 会强制重建容器但保留数据卷。
func appComposeAction(name, action string) (map[string]any, error) {
	composePath, err := installedComposePath(name)
	if err != nil {
		return nil, err
	}
	args := []string{}
	timeout := 600
	switch action {
	case "start":
		args = []string{"start"}
	case "stop":
		args = []string{"stop"}
	case "restart":
		args = []string{"restart"}
	case "rebuild":
		args = []string{"up", "-d", "--force-recreate", "--remove-orphans"}
	case "down":
		args = []string{"down"}
	case "upgrade":
		args = []string{"pull"}
	default:
		return nil, NewHTTPError(400, "不支持的动作: " + action)
	}
	emit := func(string) {}
	rc, out := composeStream(composeEnginePath(composePath), args, emit)
	if action == "upgrade" && rc == 0 {
		var out2 string
		rc, out2 = composeStream(composeEnginePath(composePath), []string{"up", "-d", "--remove-orphans"}, emit)
		out += "\n" + out2
	}
	if rc != 0 {
		if len(out) > 4000 {
			out = out[len(out)-4000:]
		}
		return nil, NewHTTPError(500, "compose 操作失败: "+out)
	}
	summary, _ := installedAppDetail(name)
	summary["output"] = tailText(out, 2000)
	summary["action"] = action
	summary["timeout"] = timeout
	return summary, nil
}

// appUpdateState 通过本地 RepoDigests 与远端 manifest 摘要对比，判断镜像是否有更新。
// 网络或仓库不可达时返回 unknown，不阻塞列表展示。
func appUpdateState(name string) ([]map[string]any, error) {
	composePath, err := installedComposePath(name)
	if err != nil {
		return nil, err
	}
	text, _ := os.ReadFile(composePath)
	services, err := composeServiceImages(string(text))
	if err != nil {
		return nil, NewHTTPError(400, "compose 解析失败: "+err.Error())
	}
	out := []map[string]any{}
	for _, svc := range services {
		image := str(svc["image"])
		item := map[string]any{"service": svc["name"], "image": image, "status": "unknown", "local": "", "remote": ""}
		if image == "" || !composeImageRe.MatchString(image) {
			out = append(out, item)
			continue
		}
		local := dockerRun(20*time.Second, "image", "inspect", image, "--format", "{{json .RepoDigests}}")
		localDigest := ""
		if local.Code == 0 {
			var digests []string
			_ = json.Unmarshal([]byte(strings.TrimSpace(local.Stdout)), &digests)
			if len(digests) > 0 {
				localDigest = digests[0]
			}
		}
		item["local"] = localDigest
		remoteRes := dockerRun(30*time.Second, "manifest", "inspect", image)
		if remoteRes.Code == 0 {
			remoteDigest := ""
			var payload map[string]any
			if json.Unmarshal([]byte(remoteRes.Stdout), &payload) == nil {
				remoteDigest = firstNonEmpty(str(payload["digest"]), str(mapOf(payload["descriptor"])["digest"]))
				if remoteDigest == "" {
					if manifests, ok := payload["manifests"].([]any); ok && len(manifests) > 0 {
						remoteDigest = str(mapOf(mapOf(manifests[0])["descriptor"])["digest"])
					}
				}
			}
			item["remote"] = remoteDigest
			switch {
			case remoteDigest == "":
				item["status"] = "unknown"
			case localDigest == "":
				item["status"] = "not-pulled"
			case strings.HasSuffix(localDigest, remoteDigest):
				item["status"] = "latest"
			default:
				item["status"] = "update"
			}
		}
		out = append(out, item)
	}
	return out, nil
}

// --- 备份 / 恢复 / 导入 ---

func listAppBackups(name string) []map[string]any {
	out := []map[string]any{}
	dir := filepath.Join(appBackupRoot(), name)
	entries, err := os.ReadDir(dir)
	if err != nil {
		return out
	}
	for _, e := range entries {
		if e.IsDir() || !appBackupNameRe.MatchString(e.Name()) {
			continue
		}
		info, ierr := e.Info()
		if ierr != nil {
			continue
		}
		out = append(out, map[string]any{
			"name": e.Name(), "size": info.Size(), "created_at": info.ModTime().Format("2006-01-02 15:04:05"),
		})
	}
	sort.Slice(out, func(i, j int) bool { return str(out[i]["name"]) > str(out[j]["name"]) })
	return out
}

// archiveDir 把目录打包为 tar.gz；跳过符号链接与 socket/device，避免归档逃逸。
func archiveDir(srcDir, dest string) error {
	if err := EnsureDir(filepath.Dir(dest)); err != nil {
		return err
	}
	tmp := dest + ".tmp"
	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer func() {
		_ = f.Close()
		_ = os.Remove(tmp)
	}()
	gz := gzip.NewWriter(f)
	tw := tar.NewWriter(gz)
	base := filepath.Clean(srcDir)
	walkErr := filepath.Walk(base, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, rerr := filepath.Rel(base, path)
		if rerr != nil || rel == "." {
			return nil
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return nil
		}
		if !info.Mode().IsRegular() && !info.IsDir() {
			return nil
		}
		hdr, herr := tar.FileInfoHeader(info, "")
		if herr != nil {
			return herr
		}
		hdr.Name = filepath.ToSlash(rel)
		if info.IsDir() {
			hdr.Name += "/"
		}
		if werr := tw.WriteHeader(hdr); werr != nil {
			return werr
		}
		if info.IsDir() {
			return nil
		}
		in, oerr := os.Open(path)
		if oerr != nil {
			return oerr
		}
		defer in.Close()
		_, cerr := io.Copy(tw, in)
		return cerr
	})
	if walkErr != nil {
		_ = tw.Close()
		_ = gz.Close()
		return walkErr
	}
	if err := tw.Close(); err != nil {
		_ = gz.Close()
		return err
	}
	if err := gz.Close(); err != nil {
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	return os.Rename(tmp, dest)
}

// extractTarGz 解包到目标目录；拒绝绝对路径、.. 与越界链接。
func extractTarGz(archivePath, destDir string) error {
	f, err := os.Open(archivePath)
	if err != nil {
		return err
	}
	defer f.Close()
	gz, err := gzip.NewReader(f)
	if err != nil {
		return err
	}
	defer gz.Close()
	tr := tar.NewReader(gz)
	root, err := filepath.Abs(destDir)
	if err != nil {
		return err
	}
	for {
		hdr, nerr := tr.Next()
		if nerr == io.EOF {
			break
		}
		if nerr != nil {
			return nerr
		}
		name := filepath.Clean(strings.ReplaceAll(hdr.Name, "\\", "/"))
		if name == "." || strings.HasPrefix(name, ".."+string(filepath.Separator)) || filepath.IsAbs(name) {
			return fmt.Errorf("归档包含非法路径: %s", hdr.Name)
		}
		target := filepath.Join(root, name)
		absTarget, aerr := filepath.Abs(target)
		if aerr != nil {
			return aerr
		}
		if absTarget != root && !strings.HasPrefix(absTarget, root+string(filepath.Separator)) {
			return fmt.Errorf("归档路径越界: %s", hdr.Name)
		}
		switch hdr.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
		case tar.TypeReg:
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return err
			}
			out, oerr := os.OpenFile(target, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o644)
			if oerr != nil {
				return oerr
			}
			if _, cerr := io.Copy(out, tr); cerr != nil {
				_ = out.Close()
				return cerr
			}
			_ = out.Close()
		default:
			// 忽略链接与设备文件
		}
	}
	return nil
}

func backupApp(name string) (map[string]any, error) {
	dir, err := installedAppDir(name)
	if err != nil {
		return nil, err
	}
	if _, err := installedComposePath(name); err != nil {
		return nil, err
	}
	fileName := time.Now().Format("20060102_150405") + ".tar.gz"
	dest := filepath.Join(appBackupRoot(), name, fileName)
	if err := archiveDir(dir, dest); err != nil {
		return nil, NewHTTPError(500, "备份失败: "+err.Error())
	}
	st, _ := os.Stat(dest)
	size := int64(0)
	if st != nil {
		size = st.Size()
	}
	return map[string]any{"name": fileName, "size": size}, nil
}

// restoreApp 恢复备份：先把当前目录挪走，解包到新目录，启动失败则回滚。
func restoreApp(name, backupName string) (map[string]any, error) {
	if !appBackupNameRe.MatchString(backupName) {
		return nil, NewHTTPError(400, "备份文件名非法")
	}
	dir, err := installedAppDir(name)
	if err != nil {
		return nil, err
	}
	archivePath := filepath.Join(appBackupRoot(), name, backupName)
	if !fileExists(archivePath) {
		return nil, NewHTTPError(404, "备份不存在")
	}
	composePath := filepath.Join(dir, "docker-compose.yml")
	if fileExists(composePath) {
		_, _ = composeStream(composeEnginePath(composePath), []string{"stop"}, nil)
	}
	tmpDir := dir + ".restore-" + randomHex(4)
	backupDir := dir + ".before-restore-" + randomHex(4)
	if err := os.MkdirAll(tmpDir, 0o755); err != nil {
		return nil, err
	}
	if err := extractTarGz(archivePath, tmpDir); err != nil {
		_ = os.RemoveAll(tmpDir)
		return nil, NewHTTPError(400, "备份解包失败: "+err.Error())
	}
	if !fileExists(filepath.Join(tmpDir, "docker-compose.yml")) {
		_ = os.RemoveAll(tmpDir)
		return nil, NewHTTPError(400, "备份中缺少 docker-compose.yml")
	}
	hadOld := false
	if st, serr := os.Stat(dir); serr == nil && st.IsDir() {
		if err := os.Rename(dir, backupDir); err != nil {
			_ = os.RemoveAll(tmpDir)
			return nil, err
		}
		hadOld = true
	}
	if err := os.Rename(tmpDir, dir); err != nil {
		if hadOld {
			_ = os.Rename(backupDir, dir)
		}
		_ = os.RemoveAll(tmpDir)
		return nil, err
	}
	rc, out := composeStream(composeEnginePath(filepath.Join(dir, "docker-compose.yml")), []string{"up", "-d", "--remove-orphans"}, nil)
	if rc != 0 {
		_ = os.RemoveAll(dir)
		if hadOld {
			_ = os.Rename(backupDir, dir)
			_, _ = composeStream(composeEnginePath(filepath.Join(dir, "docker-compose.yml")), []string{"up", "-d"}, nil)
		}
		return nil, NewHTTPError(500, "恢复后启动失败，已回滚: "+tailText(out, 2000))
	}
	if hadOld {
		_ = os.RemoveAll(backupDir)
	}
	return installedAppDetail(name)
}

func importApp(name, archivePath string) (map[string]any, error) {
	dir, err := installedAppDir(name)
	if err != nil {
		return nil, err
	}
	if _, serr := os.Stat(dir); serr == nil {
		return nil, NewHTTPError(409, "同名应用已存在，请换一个名称")
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	if err := extractTarGz(archivePath, dir); err != nil {
		_ = os.RemoveAll(dir)
		return nil, NewHTTPError(400, "备份解包失败: "+err.Error())
	}
	if !fileExists(filepath.Join(dir, "docker-compose.yml")) {
		_ = os.RemoveAll(dir)
		return nil, NewHTTPError(400, "备份中缺少 docker-compose.yml")
	}
	rc, out := composeStream(composeEnginePath(filepath.Join(dir, "docker-compose.yml")), []string{"up", "-d", "--remove-orphans"}, nil)
	if rc != 0 {
		return nil, NewHTTPError(500, "导入后启动失败: "+tailText(out, 2000))
	}
	return installedAppDetail(name)
}

func saveAppCompose(name, text string, apply bool) (map[string]any, error) {
	composePath, err := installedComposePath(name)
	if err != nil {
		return nil, err
	}
	var parsed map[string]any
	if yerr := yaml.Unmarshal([]byte(text), &parsed); yerr != nil {
		return nil, NewHTTPError(400, "docker-compose.yml 解析失败: "+yerr.Error())
	}
	if len(mapOf(parsed["services"])) == 0 {
		return nil, NewHTTPError(400, "docker-compose.yml 缺少 services 定义")
	}
	old, _ := os.ReadFile(composePath)
	CaptureSnapshot("app-compose", name, composePath)
	if werr := os.WriteFile(composePath, []byte(text), 0o644); werr != nil {
		return nil, werr
	}
	if apply {
		rc, out := composeStream(composeEnginePath(composePath), []string{"up", "-d", "--remove-orphans"}, nil)
		if rc != 0 {
			_ = os.WriteFile(composePath, old, 0o644)
			return nil, NewHTTPError(500, "应用配置失败，已回滚: "+tailText(out, 2000))
		}
	}
	return installedAppDetail(name)
}

func registerAppLifecycleRoutes(a *App) {
	a.Get("/api/appstore/installed", perm("appstore"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"apps": listInstalledApps()})
	})
	a.Get("/api/appstore/installed/{app_name}", perm("appstore"), func(w http.ResponseWriter, r *http.Request) error {
		detail, err := installedAppDetail(appNameParam(r))
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"app": detail})
	})
	a.Post("/api/appstore/installed/{app_name}/action", perm("appstore"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Action string `json:"action"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		res, err := appComposeAction(appNameParam(r), strings.TrimSpace(req.Action))
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, res)
	})
	a.Get("/api/appstore/installed/{app_name}/check-update", perm("appstore"), func(w http.ResponseWriter, r *http.Request) error {
		items, err := appUpdateState(appNameParam(r))
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"services": items})
	})
	a.Get("/api/appstore/installed/{app_name}/compose", perm("appstore"), func(w http.ResponseWriter, r *http.Request) error {
		detail, err := installedAppDetail(appNameParam(r))
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"name": detail["name"], "compose": detail["compose"]})
	})
	a.Put("/api/appstore/installed/{app_name}/compose", perm("appstore"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Compose string `json:"compose"`
			Apply   bool   `json:"apply"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		detail, err := saveAppCompose(appNameParam(r), req.Compose, req.Apply)
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"app": detail})
	})
	a.Post("/api/appstore/installed/{app_name}/backup", perm("appstore"), func(w http.ResponseWriter, r *http.Request) error {
		res, err := backupApp(appNameParam(r))
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, res)
	})
	a.Get("/api/appstore/installed/{app_name}/backups", perm("appstore"), func(w http.ResponseWriter, r *http.Request) error {
		name := appNameParam(r)
		if _, err := installedAppDir(name); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"backups": listAppBackups(name)})
	})
	a.Post("/api/appstore/installed/{app_name}/restore", perm("appstore"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Name string `json:"name"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		detail, err := restoreApp(appNameParam(r), req.Name)
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"app": detail})
	})
	a.Post("/api/appstore/installed/import", perm("appstore"), func(w http.ResponseWriter, r *http.Request) error {
		if err := r.ParseMultipartForm(32 << 20); err != nil {
			return NewHTTPError(400, "上传解析失败: "+err.Error())
		}
		name := strings.TrimSpace(r.FormValue("name"))
		if _, err := installedAppDir(name); err != nil {
			return err
		}
		file, hdr, ferr := r.FormFile("file")
		if ferr != nil {
			return NewHTTPError(400, "缺少备份文件")
		}
		defer file.Close()
		if hdr.Size > 512<<20 {
			return NewHTTPError(413, "备份文件不能超过 512MB")
		}
		tmpDir := DataPath("tmp")
		if err := EnsureDir(tmpDir); err != nil {
			return err
		}
		tmpPath := filepath.Join(tmpDir, "app-import-"+randomHex(6)+".tar.gz")
		out, oerr := os.OpenFile(tmpPath, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o600)
		if oerr != nil {
			return oerr
		}
		_, cerr := io.Copy(out, io.LimitReader(file, 512<<20))
		_ = out.Close()
		if cerr != nil {
			_ = os.Remove(tmpPath)
			return cerr
		}
		defer os.Remove(tmpPath)
		detail, err := importApp(name, tmpPath)
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"app": detail})
	})
}

func appNameParam(r *http.Request) string {
	return strings.TrimSpace(PathParam(r, "app_name"))
}

func firstNonEmpty(values ...string) string {
	for _, v := range values {
		if strings.TrimSpace(v) != "" {
			return v
		}
	}
	return ""
}
