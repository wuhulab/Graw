package graw

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Docker 模块：通过 docker/podman CLI 实现容器、镜像、卷、网络与 compose 管理。
// 与 Python 版保持同一套响应结构，前端无需改动。

var (
	dockerBinOnce sync.Once
	dockerBin     string
	registryRe    = regexp.MustCompile(`^[A-Za-z0-9._:/@-]{1,253}$`)
	dockerRefRe   = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,254}$`)
)

func dockerCLI() string {
	bin := ""
	if v := strings.TrimSpace(os.Getenv("GRAW_DOCKER_BIN")); v != "" {
		bin = v
	} else {
		dockerBinOnce.Do(func() {
			// 容器部署时 docker CLI 在面板容器内、通过挂载的 docker.sock 访问引擎，
			// 因此优先在本容器 PATH 查找，而不是 chroot 到宿主机（宿主未必装 CLI）。
			for _, name := range []string{"docker", "podman"} {
				if p, err := exec.LookPath(name); err == nil {
					dockerBin = p
					break
				}
				if p := HostWhich(name); p != "" {
					dockerBin = p
					break
				}
			}
		})
		bin = dockerBin
	}
	// 面板镜像自带 docker CLI，`docker -v` 恒可用；真正决定能否管理容器的是
	// 能否连到 daemon。漏挂 socket 的容器部署在此自动指向宿主机 socket。
	if strings.TrimSuffix(strings.ToLower(filepath.Base(bin)), ".exe") == "docker" {
		applyHostDockerSocket()
	}
	return bin
}

// dockerHostSocketOnce 保证 socket 探测只执行一次，避免每条 docker 命令都 stat 一遍。
var dockerHostSocketOnce sync.Once

// applyHostDockerSocket 在「只挂载了宿主机根目录、漏挂 docker.sock」的容器部署下，
// 把 DOCKER_HOST 指向宿主机上的 socket，恢复容器/镜像管理能力。
//
// 背景：Docker 窗口的可用性判定依赖 daemon，而不是 docker CLI 是否存在。
// 官方 compose 会把 /var/run/docker.sock 挂进面板容器；但用 `docker run`
// 只挂 `/:/host`（HOST_ROOT=/host）的用户，容器内 `docker -v` 正常、Docker
// 窗口却一直显示「不可用」。此时宿主机 socket 可通过 HOST_ROOT 访问。
//
// 仅在以下条件同时满足时改写（显式配置永远优先）：
//   - 本机模式（远程节点的 docker 命令走 SSH，与本机 CLI 环境无关）；
//   - 用户未显式设置 DOCKER_HOST；
//   - 默认 /var/run/docker.sock 不存在（已正确挂载时保持默认，不改变既有行为）；
//   - HOST_ROOT 下确实存在 socket 文件。
func applyHostDockerSocket() {
	// 请求级节点可能已切到远端：此时不能用本机探测结果覆盖环境，
	// 也不能提前消耗掉 Once（本地请求稍后仍需检测）。
	if remoteActive() {
		return
	}
	dockerHostSocketOnce.Do(func() {
		if isWindows() || strings.TrimSpace(os.Getenv("DOCKER_HOST")) != "" {
			return
		}
		hostRoot := GetConfig().HostRoot
		if hostRoot == "" {
			return
		}
		if _, err := os.Stat("/var/run/docker.sock"); err == nil {
			return
		}
		for _, rel := range []string{"var/run/docker.sock", "run/docker.sock"} {
			p := filepath.Join(hostRoot, rel)
			st, err := os.Stat(p)
			if err == nil && st.Mode()&os.ModeSocket != 0 {
				_ = os.Setenv("DOCKER_HOST", "unix://"+filepath.ToSlash(p))
				log.Printf("未挂载 /var/run/docker.sock，改用宿主机 socket：%s", p)
				return
			}
		}
	})
}

func dockerRun(timeout time.Duration, args ...string) CmdResult {
	if remoteActive() {
		node := currentNode()
		bin := remoteDockerBin(node)
		if bin == "" {
			return CmdResult{Code: 127, Err: fmt.Errorf("远程节点 Docker/Podman 不可用")}
		}
		return remoteNodeCmd(node, timeout, bin, args...)
	}
	bin := dockerCLI()
	if bin == "" {
		return CmdResult{Code: 127, Err: fmt.Errorf("docker/podman 不可用")}
	}
	// 不经过 HostCmd 的 chroot：容器内的 CLI 通过 docker.sock 作用于宿主机引擎。
	return runCommand(timeout, bin, args...)
}

var (
	remoteDockerMu   sync.Mutex
	remoteDockerBins = map[string]string{}
)

// remoteDockerBin 探测远端节点可用的容器 CLI（docker 优先，podman 兜底）。
func remoteDockerBin(node map[string]any) string {
	key := nodeKey(node)
	remoteDockerMu.Lock()
	if v, ok := remoteDockerBins[key]; ok {
		remoteDockerMu.Unlock()
		return v
	}
	remoteDockerMu.Unlock()
	res := remoteExec(node, 15*time.Second, "command -v docker || command -v podman || true", nil)
	bin := ""
	for _, line := range strings.Split(strings.TrimSpace(res.Stdout), "\n") {
		line = strings.TrimSpace(line)
		if line != "" {
			bin = line
			break
		}
	}
	remoteDockerMu.Lock()
	remoteDockerBins[key] = bin
	remoteDockerMu.Unlock()
	return bin
}

func dockerJSONLines(args ...string) ([]map[string]any, CmdResult) {
	res := dockerRun(30*time.Second, args...)
	items := []map[string]any{}
	if res.Code != 0 {
		return items, res
	}
	for _, line := range strings.Split(res.Stdout, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var item map[string]any
		if err := json.Unmarshal([]byte(line), &item); err != nil {
			continue
		}
		items = append(items, item)
	}
	return items, res
}

func dockerJSONArray(args ...string) ([]map[string]any, CmdResult) {
	res := dockerRun(30*time.Second, args...)
	items := []map[string]any{}
	if res.Code != 0 {
		return items, res
	}
	out := strings.TrimSpace(res.Stdout)
	if out == "" {
		return items, res
	}
	if strings.HasPrefix(out, "[") {
		_ = json.Unmarshal([]byte(out), &items)
		return items, res
	}
	for _, line := range strings.Split(out, "\n") {
		var item map[string]any
		if json.Unmarshal([]byte(line), &item) == nil {
			items = append(items, item)
		}
	}
	return items, res
}

// dockerAvailable 探测容器引擎是否真的可用，返回服务端版本、宿主系统与失败原因。
//
// 判定必须落到 daemon：`docker -v` 只说明客户端存在（面板镜像自带 docker CLI），
// 能列出容器才说明能连上 daemon。失败时把 CLI 的 stderr 作为原因返回，
// 让前端直接展示具体问题（socket 未挂载 / 权限不足 / daemon 未启动），
// 而不是笼统的「Docker 不可用」。
func dockerAvailable() (version, osName, reason string) {
	if dockerCLI() == "" {
		return "", "", "未检测到 docker/podman 命令，请确认面板环境已安装容器 CLI"
	}
	res := dockerRun(10*time.Second, "version", "--format", "{{.Server.Version}}|{{.Server.Os}}")
	version = strings.TrimSpace(res.Stdout)
	if res.Code != 0 || version == "" {
		reason = cleanDockerReason(res.Err, res.Stderr)
		if reason == "" {
			reason = "无法连接容器引擎（docker version 执行失败）"
		}
		return "", "", reason
	}
	parts := strings.SplitN(version, "|", 2)
	version = strings.TrimSpace(parts[0])
	if len(parts) > 1 {
		osName = strings.TrimSpace(parts[1])
	}
	return version, osName, ""
}

func cleanDockerReason(err error, stderr string) string {
	msg := strings.TrimSpace(stderr)
	if msg == "" && err != nil {
		msg = err.Error()
	}
	if len(msg) > 400 {
		msg = msg[:400]
	}
	return msg
}

func dockerEngineName() string {
	if remoteActive() {
		bin := remoteDockerBin(currentNode())
		if strings.Contains(strings.ToLower(bin), "podman") {
			return "podman"
		}
		return "docker"
	}
	if strings.Contains(strings.ToLower(dockerCLI()), "podman") {
		return "podman"
	}
	return "docker"
}

func dockerStatus() map[string]any {
	version, osName, reason := dockerAvailable()
	if version == "" {
		if reason == "" {
			reason = "Docker/Podman 不可用"
		}
		return map[string]any{"available": false, "reason": reason}
	}
	containers, _ := dockerJSONLines("ps", "-a", "--format", "{{json .}}")
	images, _ := dockerJSONLines("images", "--format", "{{json .}}")
	running := 0
	for _, c := range containers {
		if strings.EqualFold(str(c["State"]), "running") || strings.HasPrefix(str(c["Status"]), "Up") {
			running++
		}
	}
	engine := dockerEngineName()
	if osName == "" {
		osName = "linux"
	}
	return map[string]any{
		"available": true, "containers": len(containers), "containers_running": running,
		"images": len(images), "server_version": version, "os": osName + " (" + engine + ")",
	}
}

func str(v any) string {
	switch x := v.(type) {
	case string:
		return x
	case json.Number:
		return x.String()
	case float64:
		return fmt.Sprintf("%v", x)
	case bool:
		return fmt.Sprintf("%v", x)
	case nil:
		return ""
	}
	return fmt.Sprint(v)
}

func parsePercent(v any) float64 {
	s := strings.TrimSuffix(strings.TrimSpace(str(v)), "%")
	if s == "" {
		return 0
	}
	f, err := parseFloatSafe(s)
	if err != nil {
		return 0
	}
	return roundN(f, 2)
}

func parseFloatSafe(s string) (float64, error) {
	var f float64
	if _, err := fmt.Sscanf(s, "%f", &f); err != nil {
		return 0, err
	}
	return f, nil
}

func dockerStats() map[string]map[string]any {
	result := map[string]map[string]any{}
	items, res := dockerJSONLines("stats", "--no-stream", "--format", "{{json .}}")
	if res.Code != 0 {
		return result
	}
	for _, item := range items {
		name := str(item["Name"])
		if name == "" {
			name = str(item["name"])
		}
		if name == "" {
			name = str(item["container"])
		}
		if name == "" {
			name = str(item["ID"])
		}
		if name == "" {
			continue
		}
		cpu := item["CPUPerc"]
		if cpu == nil {
			cpu = item["cpu_percent"]
		}
		memP := item["MemPerc"]
		if memP == nil {
			memP = item["mem_percent"]
		}
		memU := item["MemUsage"]
		if memU == nil {
			memU = item["mem_usage"]
		}
		result[name] = map[string]any{
			"cpu_percent": parsePercent(cpu), "mem_percent": parsePercent(memP),
			"mem_usage": str(memU),
		}
	}
	return result
}

func dockerMeta() map[string]any {
	meta := map[string]any{}
	if err := ReadJSON(DataPath("docker_meta.json"), &meta); err != nil {
		return map[string]any{"starred": map[string]any{}, "notes": map[string]any{}}
	}
	if _, ok := meta["starred"]; !ok {
		meta["starred"] = map[string]any{}
	}
	if _, ok := meta["notes"]; !ok {
		meta["notes"] = map[string]any{}
	}
	return meta
}

func saveDockerMeta(meta map[string]any) {
	_ = WriteJSONFile(DataPath("docker_meta.json"), meta)
}

func dockerContainers() []map[string]any {
	items, res := dockerJSONLines("ps", "-a", "--format", "{{json .}}")
	if res.Code != 0 {
		return []map[string]any{}
	}
	stats := map[string]map[string]any{}
	for _, it := range items {
		if strings.EqualFold(str(it["State"]), "running") || strings.HasPrefix(str(it["Status"]), "Up") {
			stats = dockerStats()
			break
		}
	}
	meta := dockerMeta()
	starred, _ := meta["starred"].(map[string]any)
	notes, _ := meta["notes"].(map[string]any)
	out := []map[string]any{}
	for _, c := range items {
		name := str(c["Names"])
		if name == "" {
			name = str(c["Name"])
		}
		id := str(c["ID"])
		if id == "" {
			id = str(c["Id"])
		}
		if len(id) > 12 {
			id = id[:12]
		}
		state := str(c["State"])
		if state == "" {
			if strings.HasPrefix(str(c["Status"]), "Up") {
				state = "running"
			} else {
				state = "exited"
			}
		}
		st := stats[name]
		if st == nil {
			st = map[string]any{}
		}
		created := str(c["CreatedAt"])
		if created == "" {
			created = str(c["Created"])
		}
		out = append(out, map[string]any{
			"id": id, "name": name, "image": str(c["Image"]), "status": str(c["Status"]),
			"state": state, "created": created, "ports": parsePorts(c["Ports"]),
			"cpu_percent": num(st["cpu_percent"]), "mem_percent": num(st["mem_percent"]),
			"mem_usage": str(st["mem_usage"]),
			"starred":   boolOf(starred[id]), "note": str(notes[id]),
		})
	}
	return out
}

func boolOf(v any) bool {
	b, _ := v.(bool)
	return b
}

func parsePorts(v any) []string {
	out := []string{}
	switch x := v.(type) {
	case string:
		for _, tok := range strings.Split(x, ",") {
			if s := strings.TrimSpace(tok); s != "" {
				out = append(out, s)
			}
		}
	case []any:
		for _, item := range x {
			if m, ok := item.(map[string]any); ok {
				out = append(out, str(m["host_port"])+"->"+str(m["container_port"]))
			} else {
				out = append(out, str(item))
			}
		}
	}
	return out
}

func registerDockerRoutes(a *App) {
	a.Get("/api/docker/status", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, dockerStatus())
	})
	a.Get("/api/docker/containers", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, dockerContainers())
	})
	a.Post("/api/docker/containers/{container_id}/action", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		id, err := safeDockerRef(PathParam(r, "container_id"), "容器标识")
		if err != nil {
			return err
		}
		var req struct {
			Action string `json:"action"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		args := []string{}
		switch req.Action {
		case "start":
			args = []string{"start", id}
		case "stop":
			args = []string{"stop", id}
		case "restart":
			args = []string{"restart", id}
		case "remove":
			args = []string{"rm", "-f", id}
		default:
			return NewHTTPError(400, "Unknown action: "+req.Action)
		}
		res := dockerRun(120*time.Second, args...)
		if res.Code != 0 {
			return NewHTTPError(500, cleanDockerReason(res.Err, res.Stderr))
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Get("/api/docker/containers/{container_id}/logs", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		id, err := safeDockerRef(PathParam(r, "container_id"), "容器标识")
		if err != nil {
			return err
		}
		tail := QueryInt(r, "tail", 200)
		if tail < 0 {
			tail = 200
		}
		if tail > 5000 {
			tail = 5000
		}
		res := dockerRun(60*time.Second, "logs", "--tail", itoa(tail), id)
		if res.Code != 0 {
			return NewHTTPError(500, cleanDockerReason(res.Err, res.Stderr))
		}
		text := res.Stdout + res.Stderr
		if strings.TrimSpace(text) == "" {
			text = "(空)"
		}
		return WriteJSON(w, 200, map[string]any{"logs": text})
	})
	a.Get("/api/docker/containers/{container_id}/stats", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		id, err := safeDockerRef(PathParam(r, "container_id"), "容器标识")
		if err != nil {
			return err
		}
		cid := id
		if len(cid) > 12 {
			cid = cid[:12]
		}
		for key, val := range dockerStats() {
			if key == cid || strings.HasPrefix(key, cid) || strings.Contains(key, cid) {
				return WriteJSON(w, 200, val)
			}
		}
		return NewHTTPError(404, "容器未运行或无监控数据")
	})
	a.Post("/api/docker/containers/{container_id}/star", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		id, err := safeDockerRef(PathParam(r, "container_id"), "容器标识")
		if err != nil {
			return err
		}
		if len(id) > 12 {
			id = id[:12]
		}
		meta := dockerMeta()
		starred, _ := meta["starred"].(map[string]any)
		now := !boolOf(starred[id])
		starred[id] = now
		meta["starred"] = starred
		saveDockerMeta(meta)
		return WriteJSON(w, 200, map[string]any{"starred": now})
	})
	a.Post("/api/docker/containers/{container_id}/notes", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		id, err := safeDockerRef(PathParam(r, "container_id"), "容器标识")
		if err != nil {
			return err
		}
		var req struct {
			Note string `json:"note"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if len(id) > 12 {
			id = id[:12]
		}
		meta := dockerMeta()
		notes, _ := meta["notes"].(map[string]any)
		notes[id] = req.Note
		meta["notes"] = notes
		saveDockerMeta(meta)
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Get("/api/docker/containers/{container_id}/inspect", perm("docker"), dockerInspectHandler)
	a.Post("/api/docker/containers/{container_id}/backup", perm("docker"), dockerBackupHandler)
	a.Post("/api/docker/containers/{container_id}/upgrade", perm("docker"), dockerUpgradeHandler)
	a.Post("/api/docker/containers/{container_id}/commit", perm("docker"), dockerCommitHandler)
	a.Get("/api/docker/images", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, dockerImages())
	})
	a.Get("/api/docker/config", perm("docker"), dockerConfigGetHandler)
	a.Put("/api/docker/config", perm("docker"), dockerConfigPutHandler)
	a.Put("/api/docker/config/raw", perm("docker"), dockerConfigRawHandler)
	a.Get("/api/docker/compose/projects", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, composeProjects())
	})
	a.Post("/api/docker/compose/{name}/action", perm("docker"), composeActionHandler)
	a.Post("/api/docker/images/{image_id}/remove", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		id, err := safeDockerRef(PathParam(r, "image_id"), "镜像标识")
		if err != nil {
			return err
		}
		res := dockerRun(120*time.Second, "rmi", "-f", id)
		if res.Code != 0 {
			return NewHTTPError(500, cleanDockerReason(res.Err, res.Stderr))
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/docker/images/pull", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Name string `json:"name"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		name, err := safeDockerRef(req.Name, "镜像名")
		if err != nil || name == "" {
			return NewHTTPError(400, "请填写要拉取的镜像名")
		}
		res := dockerRun(600*time.Second, "pull", name)
		if res.Code != 0 {
			return NewHTTPError(500, cleanDockerReason(res.Err, res.Stderr))
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "detail": strings.TrimSpace(res.Stdout)})
	})
	a.Post("/api/docker/images/{image_id}/tag", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		id, err := safeDockerRef(PathParam(r, "image_id"), "镜像标识")
		if err != nil {
			return err
		}
		var req struct {
			Repo string `json:"repo"`
			Tag  string `json:"tag"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		repo := strings.TrimSpace(req.Repo)
		if repo == "" {
			return NewHTTPError(400, "请填写目标仓库名")
		}
		if !dockerRefRe.MatchString(repo) {
			return NewHTTPError(400, "仓库名含非法字符")
		}
		tag := strings.TrimSpace(req.Tag)
		if tag == "" {
			tag = "latest"
		}
		if !dockerRefRe.MatchString(tag) {
			return NewHTTPError(400, "标签含非法字符")
		}
		res := dockerRun(60*time.Second, "tag", id, repo+":"+tag)
		if res.Code != 0 {
			return NewHTTPError(500, cleanDockerReason(res.Err, res.Stderr))
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/docker/images/build", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Name       string `json:"name"`
			Tag        string `json:"tag"`
			ContextDir string `json:"context_dir"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		name := strings.TrimSpace(req.Name)
		contextDir := strings.TrimSpace(req.ContextDir)
		if name == "" {
			return NewHTTPError(400, "请填写镜像名")
		}
		if contextDir == "" || !filepath.IsAbs(contextDir) {
			return NewHTTPError(400, "构建目录必须是绝对路径")
		}
		if !IsDir(contextDir) {
			return NewHTTPError(400, "构建目录不存在: "+contextDir)
		}
		if !Exists(filepath.Join(contextDir, "Dockerfile")) {
			return NewHTTPError(400, "构建目录中未找到 Dockerfile")
		}
		if !dockerRefRe.MatchString(name) {
			return NewHTTPError(400, "镜像名含非法字符")
		}
		tag := strings.TrimSpace(req.Tag)
		if tag == "" {
			tag = "latest"
		}
		if !dockerRefRe.MatchString(tag) {
			return NewHTTPError(400, "标签含非法字符")
		}
		full := name + ":" + tag
		res := dockerRun(600*time.Second, "build", "-t", full, contextDir)
		if res.Code != 0 {
			return NewHTTPError(500, cleanDockerReason(res.Err, res.Stderr))
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "image": full, "detail": strings.TrimSpace(res.Stdout)})
	})
	a.Get("/api/docker/networks", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, dockerNetworks())
	})
	a.Post("/api/docker/networks/{network_name}/remove", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		name, err := safeDockerRef(PathParam(r, "network_name"), "网络标识")
		if err != nil {
			return err
		}
		res := dockerRun(60*time.Second, "network", "rm", name)
		if res.Code != 0 {
			return NewHTTPError(500, cleanDockerReason(res.Err, res.Stderr))
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})

	// 数据卷
	a.Get("/api/dockervolumes", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		items, res := dockerJSONLines("volume", "ls", "--format", "{{json .}}")
		if res.Code != 0 {
			return NewHTTPError(500, cleanDockerReason(res.Err, res.Stderr))
		}
		out := []map[string]any{}
		for _, v := range items {
			name := str(v["Name"])
			if name == "" {
				name = str(v["name"])
			}
			driver := str(v["Driver"])
			if driver == "" {
				driver = str(v["driver"])
			}
			mp := str(v["Mountpoint"])
			if mp == "" {
				mp = str(v["mountpoint"])
			}
			out = append(out, map[string]any{"name": name, "driver": driver, "mountpoint": mp})
		}
		return WriteJSON(w, 200, out)
	})
	a.Post("/api/dockervolumes/{name}/remove", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		name, err := safeDockerRef(PathParam(r, "name"), "数据卷标识")
		if err != nil {
			return err
		}
		res := dockerRun(60*time.Second, "volume", "rm", "-f", name)
		if res.Code != 0 {
			return NewHTTPError(500, cleanDockerReason(res.Err, res.Stderr))
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Get("/api/dockervolumes/{name}/inspect", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		name, err := safeDockerRef(PathParam(r, "name"), "数据卷标识")
		if err != nil {
			return err
		}
		items, res := dockerJSONArray("volume", "inspect", name)
		if res.Code != 0 || len(items) == 0 {
			return NewHTTPError(404, "数据卷不存在")
		}
		return WriteJSON(w, 200, items[0])
	})

	// 容器资源/端口编辑
	a.Get("/api/containeredit/{container_id}/info", perm("docker"), containerEditInfoHandler)
	a.Post("/api/containeredit/{container_id}/update-limits", perm("docker"), containerUpdateLimitsHandler)
	a.Post("/api/containeredit/{container_id}/rebuild", perm("docker"), containerRebuildHandler)

	a.wsRoute("/api/docker/ws", dockerWSHandler)
	StartDockerBackground()
}

func safeDockerRef(ref, what string) (string, *HTTPError) {
	ref = strings.TrimSpace(ref)
	if ref == "" || strings.HasPrefix(ref, "-") || !dockerRefRe.MatchString(ref) {
		return "", NewHTTPError(400, what+"非法")
	}
	return ref, nil
}

func dockerImages() []map[string]any {
	items, res := dockerJSONLines("images", "--format", "{{json .}}")
	if res.Code != 0 {
		return []map[string]any{}
	}
	out := []map[string]any{}
	for _, img := range items {
		repo := str(img["Repository"])
		tag := str(img["Tag"])
		id := str(img["ID"])
		id = strings.TrimPrefix(id, "sha256:")
		if len(id) > 19 {
			id = id[:19]
		}
		tags := []string{}
		if repo != "" && repo != "<none>" {
			if tag == "" {
				tag = "latest"
			}
			tags = append(tags, repo+":"+tag)
		}
		size := parseSizeBytes(str(img["Size"]))
		created := str(img["CreatedAt"])
		if created == "" {
			created = str(img["Created"])
		}
		out = append(out, map[string]any{"id": id, "tags": tags, "size": size, "created": created})
	}
	return out
}

func parseSizeBytes(s string) int64 {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0
	}
	re := regexp.MustCompile(`^([\d.]+)\s*([A-Za-z]+)$`)
	m := re.FindStringSubmatch(s)
	if m == nil {
		return 0
	}
	val, _ := parseFloatSafe(m[1])
	units := map[string]float64{
		"B": 1, "KB": 1024, "MB": 1024 * 1024, "GB": 1024 * 1024 * 1024,
		"TB": 1024 * 1024 * 1024 * 1024, "K": 1024, "M": 1024 * 1024,
		"G": 1024 * 1024 * 1024, "KiB": 1024, "MiB": 1024 * 1024, "GiB": 1024 * 1024 * 1024,
	}
	return int64(val * units[m[2]])
}

func dockerNetworks() []map[string]any {
	items, res := dockerJSONLines("network", "ls", "--format", "{{json .}}")
	if res.Code != 0 {
		return []map[string]any{}
	}
	out := []map[string]any{}
	for _, n := range items {
		name := str(n["Name"])
		id := str(n["ID"])
		if len(id) > 12 {
			id = id[:12]
		}
		subnets := []map[string]any{}
		iface := ""
		if name != "" {
			detail, res := dockerJSONArray("network", "inspect", name)
			if res.Code == 0 && len(detail) > 0 {
				if ipam, ok := detail[0]["IPAM"].(map[string]any); ok {
					if cfg, ok := ipam["Config"].([]any); ok {
						for _, c := range cfg {
							if cm, ok := c.(map[string]any); ok {
								subnets = append(subnets, map[string]any{
									"subnet": str(cm["Subnet"]), "gateway": str(cm["Gateway"]),
								})
							}
						}
					}
				}
				if options, ok := detail[0]["Options"].(map[string]any); ok {
					iface = str(options["com.docker.network.bridge.name"])
				}
			}
		}
		out = append(out, map[string]any{
			"name": name, "id": id, "driver": str(n["Driver"]), "interface": iface,
			"created": str(n["CreatedAt"]), "subnets": subnets, "internal": false,
			"labels": map[string]any{},
		})
	}
	return out
}

func composeProjects() []map[string]any {
	appstore := DataPath("appstore")
	out := []map[string]any{}
	entries, _ := os.ReadDir(appstore)
	running, total := map[string]int{}, map[string]int{}
	if items, res := dockerJSONLines("ps", "-a", "--format", "{{json .}}"); res.Code == 0 {
		for _, c := range items {
			labels := map[string]string{}
			if raw := str(c["Labels"]); raw != "" {
				for _, pair := range strings.Split(raw, ",") {
					if kv := strings.SplitN(pair, "=", 2); len(kv) == 2 {
						labels[kv[0]] = kv[1]
					}
				}
			}
			proj := labels["com.docker.compose.project"]
			if proj == "" {
				proj = labels["io.podman.compose.project"]
			}
			if proj == "" {
				continue
			}
			total[proj]++
			if strings.EqualFold(str(c["State"]), "running") || strings.HasPrefix(str(c["Status"]), "Up") {
				running[proj]++
			}
		}
	}
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		dir := filepath.Join(appstore, e.Name())
		compose := filepath.Join(dir, "docker-compose.yml")
		if !Exists(compose) {
			continue
		}
		out = append(out, map[string]any{
			"name": e.Name(), "path": dir, "compose_file": compose,
			"total": total[e.Name()], "running": running[e.Name()],
			"services": parseComposeServices(compose),
		})
	}
	sort.Slice(out, func(i, j int) bool { return out[i]["name"].(string) < out[j]["name"].(string) })
	return out
}

func parseComposeServices(path string) []string {
	data, err := os.ReadFile(path)
	if err != nil {
		return []string{}
	}
	services := []string{}
	inServices := false
	for _, line := range strings.Split(string(data), "\n") {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		if !strings.HasPrefix(line, " ") && strings.HasSuffix(trimmed, ":") {
			inServices = trimmed == "services:"
			continue
		}
		if inServices && strings.HasPrefix(line, "  ") && !strings.HasPrefix(line, "    ") && strings.HasSuffix(trimmed, ":") {
			services = append(services, strings.TrimSuffix(trimmed, ":"))
		}
	}
	return services
}

func composeActionHandler(w http.ResponseWriter, r *http.Request) error {
	name := PathParam(r, "name")
	if filepath.Base(name) != name || name == "" || name == "." || name == ".." {
		return NewHTTPError(400, "非法项目名")
	}
	var req struct {
		Action string `json:"action"`
	}
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	if req.Action != "up" && req.Action != "down" && req.Action != "restart" {
		return NewHTTPError(400, "不支持的操作: "+req.Action)
	}
	dir := filepath.Join(DataPath("appstore"), name)
	compose := filepath.Join(dir, "docker-compose.yml")
	if !Exists(compose) {
		return NewHTTPError(404, "未找到项目 "+name+" 的 docker-compose.yml")
	}
	file := HostVisiblePath(compose)
	var res CmdResult
	switch req.Action {
	case "up":
		res = dockerRun(300*time.Second, "compose", "-f", file, "up", "-d")
	case "down":
		res = dockerRun(300*time.Second, "compose", "-f", file, "down")
	case "restart":
		res = dockerRun(300*time.Second, "compose", "-f", file, "restart")
	}
	if res.Code != 0 {
		// 回退 docker-compose 旧命令
		bin := dockerCLI()
		if bin != "" {
			legacy := runCommand(300*time.Second, bin, "-compose", "-f", file, req.Action)
			if legacy.Code == 0 {
				return WriteJSON(w, 200, map[string]any{"ok": true, "detail": strings.TrimSpace(legacy.Stdout)})
			}
		}
		return NewHTTPError(500, cleanDockerReason(res.Err, res.Stderr))
	}
	return WriteJSON(w, 200, map[string]any{"ok": true, "detail": strings.TrimSpace(res.Stdout)})
}

// --- 容器详情 ---

func dockerInspectRaw(id string) (map[string]any, error) {
	items, res := dockerJSONArray("inspect", id)
	if res.Code != 0 || len(items) == 0 {
		return nil, fmt.Errorf("%s", cleanDockerReason(res.Err, res.Stderr))
	}
	return items[0], nil
}

func mapOf(v any) map[string]any {
	m, _ := v.(map[string]any)
	if m == nil {
		return map[string]any{}
	}
	return m
}

func sliceOf(v any) []any {
	s, _ := v.([]any)
	if s == nil {
		return []any{}
	}
	return s
}

func dockerInspectHandler(w http.ResponseWriter, r *http.Request) error {
	id, err := safeDockerRef(PathParam(r, "container_id"), "容器标识")
	if err != nil {
		return err
	}
	raw, inspectErr := dockerInspectRaw(id)
	if inspectErr != nil {
		return NewHTTPError(404, "容器不存在")
	}
	state := mapOf(raw["State"])
	config := mapOf(raw["Config"])
	hostCfg := mapOf(raw["HostConfig"])
	name := strings.TrimPrefix(str(raw["Name"]), "/")
	cid := str(raw["Id"])
	if len(cid) > 12 {
		cid = cid[:12]
	}
	ports := []any{}
	if ns, ok := raw["NetworkSettings"].(map[string]any); ok {
		if pm, ok := ns["Ports"].(map[string]any); ok {
			for k, v := range pm {
				binds := sliceOf(v)
				if len(binds) == 0 {
					ports = append(ports, map[string]any{"container_port": k})
					continue
				}
				for _, b := range binds {
					bm := mapOf(b)
					ports = append(ports, map[string]any{
						"host_ip": str(bm["HostIp"]), "host_port": str(bm["HostPort"]),
						"container_port": k,
					})
				}
			}
		}
	}
	return WriteJSON(w, 200, map[string]any{
		"id": cid, "name": name, "image": str(config["Image"]),
		"state": str(state["Status"]), "created": str(raw["Created"]),
		"started_at": str(state["StartedAt"]), "finished_at": str(state["FinishedAt"]),
		"cpu_percent": 0, "cpu_time": "", "cpu_cores": 0,
		"mem_usage": 0, "mem_limit": 0, "mem_percent": 0,
		"cache_usage": 0, "layer_size": 0, "virtual_size": int64Of(raw["SizeRootFs"]),
		"restart_count": int64Of(raw["RestartCount"]), "pid": int64Of(state["Pid"]),
		"cmd": config["Cmd"], "entrypoint": config["Entrypoint"], "env": config["Env"],
		"mounts": raw["Mounts"], "network_mode": str(hostCfg["NetworkMode"]),
		"restart_policy": str(mapOf(hostCfg["RestartPolicy"])["Name"]),
		"ports":          ports, "install_dir": "",
	})
}

func int64Of(v any) int64 {
	switch x := v.(type) {
	case float64:
		return int64(x)
	case json.Number:
		n, _ := x.Int64()
		return n
	case int64:
		return x
	case int:
		return int64(x)
	}
	return 0
}

func dockerBackupHandler(w http.ResponseWriter, r *http.Request) error {
	id, err := safeDockerRef(PathParam(r, "container_id"), "容器标识")
	if err != nil {
		return err
	}
	dir := DataPath("docker-backups")
	if err := EnsureDir(dir); err != nil {
		return err
	}
	if len(id) > 12 {
		id = id[:12]
	}
	name := id + "_" + time.Now().Format("20060102_150405") + ".tar"
	path := filepath.Join(dir, name)
	res := dockerRun(600*time.Second, "export", "-o", HostVisiblePath(path), id)
	if res.Code != 0 {
		return NewHTTPError(500, cleanDockerReason(res.Err, res.Stderr))
	}
	st, _ := os.Stat(path)
	size := int64(0)
	if st != nil {
		size = st.Size()
	}
	return WriteJSON(w, 200, map[string]any{"ok": true, "path": path, "size": size})
}

func dockerUpgradeHandler(w http.ResponseWriter, r *http.Request) error {
	id, err := safeDockerRef(PathParam(r, "container_id"), "容器标识")
	if err != nil {
		return err
	}
	raw, inspectErr := dockerInspectRaw(id)
	if inspectErr != nil {
		return NewHTTPError(404, "容器不存在")
	}
	image := str(mapOf(raw["Config"])["Image"])
	if image == "" {
		return NewHTTPError(400, "无法确定容器镜像")
	}
	pull := dockerRun(600*time.Second, "pull", image)
	if pull.Code != 0 {
		return NewHTTPError(500, cleanDockerReason(pull.Err, pull.Stderr))
	}
	newID, recreateErr := dockerRecreate(raw, nil, nil)
	if recreateErr != nil {
		return NewHTTPError(500, recreateErr.Error())
	}
	return WriteJSON(w, 200, map[string]any{"ok": true, "new_container_id": newID, "image": image})
}

func dockerCommitHandler(w http.ResponseWriter, r *http.Request) error {
	id, err := safeDockerRef(PathParam(r, "container_id"), "容器标识")
	if err != nil {
		return err
	}
	var req struct {
		Repo string `json:"repo"`
		Tag  string `json:"tag"`
	}
	_ = DecodeJSON(r, &req)
	repo := strings.TrimSpace(req.Repo)
	if repo == "" {
		return NewHTTPError(400, "请填写镜像仓库名")
	}
	if !dockerRefRe.MatchString(repo) {
		return NewHTTPError(400, "仓库名含非法字符")
	}
	tag := strings.TrimSpace(req.Tag)
	if tag == "" {
		tag = "latest"
	}
	if !dockerRefRe.MatchString(tag) {
		return NewHTTPError(400, "标签含非法字符")
	}
	full := repo + ":" + tag
	res := dockerRun(300*time.Second, "commit", id, full)
	if res.Code != 0 {
		return NewHTTPError(500, cleanDockerReason(res.Err, res.Stderr))
	}
	out := strings.TrimSpace(res.Stdout)
	if len(out) > 19 {
		out = out[:19]
	}
	return WriteJSON(w, 200, map[string]any{"ok": true, "image": full, "id": out})
}

// dockerRecreate 按 inspect 数据重建容器（保留名称/环境变量/端口/挂载/网络/重启策略）。
func dockerRecreate(raw map[string]any, env []map[string]string, ports []map[string]string) (string, error) {
	config := mapOf(raw["Config"])
	hostCfg := mapOf(raw["HostConfig"])
	image := str(config["Image"])
	if image == "" {
		return "", fmt.Errorf("无法确定容器镜像")
	}
	name := strings.TrimPrefix(str(raw["Name"]), "/")
	args := []string{"run", "-d"}
	if name != "" {
		args = append(args, "--name", name)
	}
	if env != nil {
		for _, kv := range env {
			k := strings.TrimSpace(kv["key"])
			if k == "" {
				continue
			}
			args = append(args, "--env", k+"="+kv["value"])
		}
	} else {
		for _, e := range sliceOf(config["Env"]) {
			args = append(args, "--env", str(e))
		}
	}
	if ports != nil {
		for _, p := range ports {
			hp := strings.TrimSpace(p["host_port"])
			cp := strings.TrimSpace(p["container_port"])
			if hp == "" || cp == "" {
				continue
			}
			proto := strings.TrimSpace(p["protocol"])
			if proto == "" {
				proto = "tcp"
			}
			ip := strings.TrimSpace(p["ip"])
			spec := hp + ":" + cp + "/" + proto
			if ip != "" {
				spec = ip + ":" + spec
			}
			args = append(args, "--publish", spec)
		}
	} else {
		if bindings, ok := hostCfg["PortBindings"].(map[string]any); ok {
			for cport, list := range bindings {
				for _, b := range sliceOf(list) {
					bm := mapOf(b)
					spec := str(bm["HostPort"]) + ":" + cport
					if str(bm["HostIp"]) != "" {
						spec = str(bm["HostIp"]) + ":" + spec
					}
					args = append(args, "--publish", spec)
				}
			}
		}
	}
	for _, m := range sliceOf(raw["Mounts"]) {
		mm := mapOf(m)
		src := str(mm["Source"])
		dst := str(mm["Destination"])
		if src == "" || dst == "" {
			continue
		}
		args = append(args, "--volume", src+":"+dst)
	}
	if network := str(hostCfg["NetworkMode"]); network != "" && network != "default" && network != "bridge" {
		args = append(args, "--network", network)
	}
	restart := str(mapOf(hostCfg["RestartPolicy"])["Name"])
	if restart != "" && restart != "no" {
		args = append(args, "--restart", restart)
	}
	if entry := config["Entrypoint"]; entry != nil {
		if list := sliceOf(entry); len(list) > 0 {
			args = append(args, "--entrypoint", str(list[0]))
		}
	}
	args = append(args, image)
	for _, c := range sliceOf(config["Cmd"]) {
		args = append(args, str(c))
	}
	dockerRun(30*time.Second, "rm", "-f", str(raw["Id"]))
	res := dockerRun(180*time.Second, args...)
	if res.Code != 0 {
		return "", fmt.Errorf("%s", cleanDockerReason(res.Err, res.Stderr))
	}
	out := strings.TrimSpace(res.Stdout)
	if len(out) > 12 {
		out = out[:12]
	}
	return out, nil
}

// --- 容器编辑 ---

func containerEditInfoHandler(w http.ResponseWriter, r *http.Request) error {
	id, err := safeDockerRef(PathParam(r, "container_id"), "容器标识")
	if err != nil {
		return err
	}
	raw, e := dockerInspectRaw(id)
	if e != nil {
		return NewHTTPError(404, "容器不存在")
	}
	hostCfg := mapOf(raw["HostConfig"])
	config := mapOf(raw["Config"])
	memory := int64Of(hostCfg["Memory"])
	env := []map[string]any{}
	for _, item := range sliceOf(config["Env"]) {
		kv := strings.SplitN(str(item), "=", 2)
		value := ""
		if len(kv) > 1 {
			value = kv[1]
		}
		env = append(env, map[string]any{"key": kv[0], "value": value})
	}
	ports := []map[string]any{}
	if bindings, ok := hostCfg["PortBindings"].(map[string]any); ok {
		for cport, list := range bindings {
			c, proto := cport, "tcp"
			if idx := strings.Index(cport, "/"); idx >= 0 {
				c, proto = cport[:idx], cport[idx+1:]
			}
			for _, b := range sliceOf(list) {
				bm := mapOf(b)
				ports = append(ports, map[string]any{
					"ip": str(bm["HostIp"]), "host_port": str(bm["HostPort"]),
					"container_port": c, "protocol": proto,
				})
			}
		}
	}
	name := strings.TrimPrefix(str(raw["Name"]), "/")
	return WriteJSON(w, 200, map[string]any{
		"id": str(raw["Id"])[:minInt(12, len(str(raw["Id"])))], "name": name,
		"state": str(mapOf(raw["State"])["Status"]), "image": str(config["Image"]),
		"cpu_cores": parseCPUCores(hostCfg),
		"memory_mb": func() int64 {
			if memory > 0 {
				return memory / 1024 / 1024
			}
			return 0
		}(),
		"memory_unlimited": memory <= 0, "env": env, "ports": ports,
		"restart_policy": str(mapOf(hostCfg["RestartPolicy"])["Name"]),
	})
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func parseCPUCores(hostCfg map[string]any) float64 {
	nano := int64Of(hostCfg["NanoCpus"])
	quota := int64Of(hostCfg["CpuQuota"])
	period := int64Of(hostCfg["CpuPeriod"])
	if nano > 0 {
		return roundN(float64(nano)/1e9, 2)
	}
	if quota > 0 && period > 0 {
		return roundN(float64(quota)/float64(period), 2)
	}
	return 0
}

func containerUpdateLimitsHandler(w http.ResponseWriter, r *http.Request) error {
	id, err := safeDockerRef(PathParam(r, "container_id"), "容器标识")
	if err != nil {
		return err
	}
	var req struct {
		Cpus     float64 `json:"cpus"`
		MemoryMB int64   `json:"memory_mb"`
	}
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	if req.Cpus < 0.1 || req.Cpus > 64 {
		return NewHTTPError(400, "CPU 核数须在 0.1-64 之间")
	}
	if req.MemoryMB != 0 && (req.MemoryMB < 32 || req.MemoryMB > 262144) {
		return NewHTTPError(400, "内存限制须为 0（不限）或 32-262144 MB")
	}
	args := []string{"update", "--cpus", strings.TrimRight(strings.TrimRight(fmt.Sprintf("%.2f", req.Cpus), "0"), ".")}
	if req.MemoryMB > 0 {
		args = append(args, "--memory", itoa(int(req.MemoryMB))+"m")
	} else {
		args = append(args, "--memory", "0")
	}
	args = append(args, id)
	res := dockerRun(60*time.Second, args...)
	if res.Code != 0 {
		return NewHTTPError(500, cleanDockerReason(res.Err, res.Stderr))
	}
	return WriteJSON(w, 200, map[string]any{"ok": true})
}

func containerRebuildHandler(w http.ResponseWriter, r *http.Request) error {
	id, err := safeDockerRef(PathParam(r, "container_id"), "容器标识")
	if err != nil {
		return err
	}
	var req struct {
		Env []struct {
			Key   string `json:"key"`
			Value string `json:"value"`
		} `json:"env"`
		Ports []struct {
			HostPort      string `json:"host_port"`
			ContainerPort string `json:"container_port"`
			Protocol      string `json:"protocol"`
			IP            string `json:"ip"`
		} `json:"ports"`
	}
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	raw, e := dockerInspectRaw(id)
	if e != nil {
		return NewHTTPError(404, "容器不存在")
	}
	env := []map[string]string{}
	for _, e := range req.Env {
		env = append(env, map[string]string{"key": e.Key, "value": e.Value})
	}
	ports := []map[string]string{}
	for _, p := range req.Ports {
		ports = append(ports, map[string]string{
			"host_port": p.HostPort, "container_port": p.ContainerPort,
			"protocol": p.Protocol, "ip": p.IP,
		})
	}
	newID, e := dockerRecreate(raw, env, ports)
	if e != nil {
		return NewHTTPError(500, e.Error())
	}
	return WriteJSON(w, 200, map[string]any{"ok": true, "new_container_id": newID})
}

// --- 引擎配置 ---

func dockerConfigPath() (string, string) {
	engine := dockerEngineName()
	if engine == "podman" {
		return engine, "/etc/containers/registries.conf"
	}
	return engine, "/etc/docker/daemon.json"
}

func readHostText(path string) string {
	b, err := HostReadBytes(path)
	if err != nil {
		return ""
	}
	return string(b)
}

func writeHostText(path, content string) error {
	return HostWriteBytes(path, []byte(content), 0o644)
}

func dockerPanelConfig() map[string]any {
	cfg := map[string]any{"iptables": true}
	if err := ReadJSON(DataPath("docker_config.json"), &cfg); err != nil {
		return map[string]any{"iptables": true}
	}
	return cfg
}

// parseRegistriesTOML 解析 Podman registries.conf（TOML）中的 search/mirrors/private。
// 只做正则提取，不引入完整 TOML 解析器：该文件由面板或发行版生成，结构稳定。
func parseRegistriesTOML(content string) (search, mirrors, private []string) {
	search, mirrors, private = []string{}, []string{}, []string{}
	collect := func(raw string) []string {
		out := []string{}
		for _, item := range strings.Split(raw, ",") {
			item = strings.TrimSpace(strings.Trim(strings.TrimSpace(item), `"'`))
			if item != "" {
				out = append(out, item)
			}
		}
		return out
	}
	if m := regexp.MustCompile(`unqualified-search-registries\s*=\s*\[([^\]]*)\]`).FindStringSubmatch(content); len(m) == 2 {
		search = collect(m[1])
	}
	if m := regexp.MustCompile(`\[registries\.search\]\s*registries\s*=\s*\[([^\]]*)\]`).FindStringSubmatch(content); len(m) == 2 {
		search = collect(m[1])
	}
	// [[registry]] / [[registry.mirror]] 块：解析 location / insecure。
	for _, block := range regexp.MustCompile(`\[\[registry(?:\.([a-z]+))?\]\]`).FindAllStringSubmatchIndex(content, -1) {
		end := len(content)
		if next := regexp.MustCompile(`\[\[registry(?:\.([a-z]+))?\]\]`).FindStringIndex(content[block[1]:]); next != nil {
			end = block[1] + next[0]
		}
		body := content[block[1]:end]
		kind := ""
		if block[2] >= 0 && block[3] >= 0 {
			kind = content[block[2]:block[3]]
		}
		loc := ""
		if m := regexp.MustCompile(`location\s*=\s*"([^"]+)"`).FindStringSubmatch(body); len(m) == 2 {
			loc = m[1]
		}
		if loc == "" {
			continue
		}
		if kind == "mirror" {
			mirrors = append(mirrors, loc)
		} else if kind == "" && strings.Contains(body, "insecure") {
			private = append(private, loc)
		}
	}
	return search, mirrors, private
}

// buildRegistriesTOML 生成 Podman registries.conf，把镜像加速作为 docker.io 的 mirror。
func buildRegistriesTOML(search, mirrors, private []string) string {
	if len(search) == 0 {
		search = []string{"docker.io"}
	}
	lines := []string{"# 由 Graw 面板生成，请勿手动编辑本文件头", ""}
	lines = append(lines, "unqualified-search-registries = "+jsonQuoteList(search))
	lines = append(lines, "[registries.search]")
	lines = append(lines, "registries = "+jsonQuoteList(search))
	lines = append(lines, "", "[[registry]]", `location = "docker.io"`)
	for _, m := range mirrors {
		lines = append(lines, "", "[[registry.mirror]]", "location = "+strconv.Quote(m))
	}
	for _, p := range private {
		lines = append(lines, "", "[[registry]]", "location = "+strconv.Quote(p), "insecure = true")
	}
	return strings.Join(lines, "\n") + "\n"
}

// jsonQuoteList 把字符串列表输出为 TOML 兼容的 JSON 风格数组。
func jsonQuoteList(items []string) string {
	quoted := make([]string, 0, len(items))
	for _, item := range items {
		quoted = append(quoted, strconv.Quote(item))
	}
	return "[" + strings.Join(quoted, ", ") + "]"
}

func dockerConfigGetHandler(w http.ResponseWriter, r *http.Request) error {
	if v, _, _ := dockerAvailable(); v == "" {
		return NewHTTPError(503, "Docker/Podman 不可用")
	}
	engine, path := dockerConfigPath()
	content := readHostText(path)
	mirrors := []string{}
	private := []string{}
	iptables := any(nil)
	configType := "json"
	if engine == "podman" {
		configType = "toml"
		_, mirrors, private = parseRegistriesTOML(content)
	} else {
		var data map[string]any
		if strings.TrimSpace(content) != "" && json.Unmarshal([]byte(content), &data) == nil {
			mirrors = toStringSlice(data["registry-mirrors"])
			private = toStringSlice(data["insecure-registries"])
			if v, ok := data["iptables"].(bool); ok {
				iptables = v
			}
		}
	}
	panel := dockerPanelConfig()
	if iptables == nil {
		iptables = boolOf(panel["iptables"])
	}
	if len(mirrors) == 0 {
		panelMirrors := toStringSlice(panel["mirrors"])
		mirrors = panelMirrors
	}
	return WriteJSON(w, 200, map[string]any{
		"engine": engine, "config_path": path, "config_type": configType,
		"mirror_enabled": len(mirrors) > 0, "mirrors": mirrors,
		"private_registries": private, "iptables": iptables,
		"iptables_supported": engine == "docker", "content": content,
	})
}

func dockerConfigPutHandler(w http.ResponseWriter, r *http.Request) error {
	var req struct {
		MirrorEnabled     bool     `json:"mirror_enabled"`
		Mirrors           []string `json:"mirrors"`
		PrivateRegistries []string `json:"private_registries"`
		Iptables          bool     `json:"iptables"`
	}
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	mirrors := []string{}
	for _, m := range req.Mirrors {
		m = strings.TrimSpace(m)
		if m == "" {
			continue
		}
		if !registryRe.MatchString(m) {
			return NewHTTPError(400, "镜像加速地址含非法字符（仅允许字母/数字/._:/@-，长度 ≤253）")
		}
		mirrors = append(mirrors, m)
	}
	if !req.MirrorEnabled {
		mirrors = []string{}
	}
	private := []string{}
	for _, p := range req.PrivateRegistries {
		p = strings.TrimSpace(p)
		if p == "" {
			continue
		}
		if !registryRe.MatchString(p) {
			return NewHTTPError(400, "私有仓库地址含非法字符（仅允许字母/数字/._:/@-，长度 ≤253）")
		}
		private = append(private, p)
	}
	engine, path := dockerConfigPath()
	configType := "json"
	if engine == "podman" {
		// Podman 的 registries.conf 是 TOML：保留已有 search 列表，重建 registry/mirror 段。
		configType = "toml"
		search, _, _ := parseRegistriesTOML(readHostText(path))
		if err := writeHostText(path, buildRegistriesTOML(search, mirrors, private)); err != nil {
			return NewHTTPError(500, "写入引擎配置文件失败: "+err.Error())
		}
	} else {
		data := map[string]any{}
		content := readHostText(path)
		if strings.TrimSpace(content) != "" {
			_ = json.Unmarshal([]byte(content), &data)
			if data == nil {
				data = map[string]any{}
			}
		}
		data["registry-mirrors"] = mirrors
		if len(private) > 0 {
			data["insecure-registries"] = private
		} else {
			delete(data, "insecure-registries")
		}
		data["iptables"] = req.Iptables
		b, _ := json.MarshalIndent(data, "", "  ")
		if err := writeHostText(path, string(b)); err != nil {
			return NewHTTPError(500, "写入引擎配置文件失败: "+err.Error())
		}
	}
	panel := dockerPanelConfig()
	panel["iptables"] = req.Iptables
	panel["config_path"] = path
	panel["engine"] = engine
	panel["mirrors"] = mirrors
	_ = WriteJSONFile(DataPath("docker_config.json"), panel)
	return WriteJSON(w, 200, map[string]any{
		"ok": true, "config_path": path, "config_type": configType,
		"iptables_supported": engine == "docker",
	})
}

func dockerConfigRawHandler(w http.ResponseWriter, r *http.Request) error {
	var req struct {
		Content string `json:"content"`
	}
	if err := DecodeJSON(r, &req); err != nil {
		return err
	}
	_, path := dockerConfigPath()
	if err := writeHostText(path, req.Content); err != nil {
		return NewHTTPError(500, "写入配置文件失败: "+err.Error())
	}
	return WriteJSON(w, 200, map[string]any{"ok": true})
}

// --- Docker 实时 WebSocket 推送 ---

type dockerHubType struct {
	mu      sync.Mutex
	clients map[*wsClient]struct{}
	cache   map[string]any
	wake    chan struct{}
}

var dockerHub = &dockerHubType{clients: map[*wsClient]struct{}{}, wake: make(chan struct{}, 1)}

// StartDockerBackground 启动 Docker 实时采集协程（幂等）。
func StartDockerBackground() {
	go func() {
		ticker := time.NewTicker(5 * time.Second)
		defer ticker.Stop()
		for {
			func() {
				defer func() { _ = recover() }()
				var data map[string]any
				readNodeScope(func() {
					data = map[string]any{
						"status":     dockerStatus(),
						"containers": dockerContainers(),
						"ts":         time.Now().Unix(),
					}
				})
				dockerHub.mu.Lock()
				dockerHub.cache = data
				clients := make([]*wsClient, 0, len(dockerHub.clients))
				for c := range dockerHub.clients {
					clients = append(clients, c)
				}
				dockerHub.mu.Unlock()
				for _, c := range clients {
					_ = c.sendJSON(map[string]any{"type": "docker", "data": data})
				}
			}()
			select {
			case <-ticker.C:
			case <-dockerHub.wake:
			}
		}
	}()
}

// serveNodeDockerWS 为裸 SSH 跨节点订阅独立采集 Docker 状态并推送。
func serveNodeDockerWS(conn *wsConn, nodeID, token string) {
	defer conn.Close()
	done := make(chan struct{})
	refresh := make(chan struct{}, 1)
	go func() {
		for {
			_, data, err := conn.ReadMessage()
			if err != nil {
				close(done)
				return
			}
			var msg map[string]any
			if json.Unmarshal(data, &msg) == nil && str(msg["type"]) == "refresh" {
				select {
				case refresh <- struct{}{}:
				default:
				}
			}
		}
	}()
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	for {
		var data map[string]any
		withNodeScope(nodeID, func() {
			data = map[string]any{
				"status": dockerStatus(), "containers": dockerContainers(), "ts": time.Now().Unix(),
			}
		})
		payload, _ := json.Marshal(map[string]any{"type": "docker", "data": data})
		if err := conn.WriteText(payload); err != nil {
			return
		}
		select {
		case <-done:
			return
		case <-ticker.C:
			if token != "" && !WSStillValid(token) {
				return
			}
		case <-refresh:
		}
	}
}

func dockerWSHandler(w http.ResponseWriter, r *http.Request) {
	u := AuthenticateWS(r)
	if u == nil {
		WriteJSON(w, 401, map[string]any{"detail": "未认证"})
		return
	}
	if IsDefaultPassword(u.PasswordHash()) || !HasPerm(u, "docker") {
		WriteJSON(w, 403, map[string]any{"detail": "无权限"})
		return
	}
	// 远端 Agent 子节点：桥接子节点自身的 /api/docker/ws。
	targetID := strings.TrimSpace(r.URL.Query().Get("node"))
	store := loadNodeStore()
	node, ok := store.Nodes[targetID]
	if !ok {
		node = store.Nodes[store.Current]
	}
	if str(node["type"]) == "ssh" && agentReady(node) {
		bridgeAgentWS(w, r, node, "/api/docker/ws")
		return
	}
	if str(node["type"]) == "ssh" && targetID != "" && targetID != store.Current {
		conn, err := upgradeWS(w, r)
		if err != nil {
			return
		}
		serveNodeDockerWS(conn, targetID, r.URL.Query().Get("token"))
		return
	}
	conn, err := upgradeWS(w, r)
	if err != nil {
		return
	}
	client := &wsClient{conn: conn}
	dockerHub.mu.Lock()
	dockerHub.clients[client] = struct{}{}
	cache := dockerHub.cache
	dockerHub.mu.Unlock()
	if cache != nil {
		_ = client.sendJSON(map[string]any{"type": "docker", "data": cache})
	}
	select {
	case dockerHub.wake <- struct{}{}:
	default:
	}
	token := r.URL.Query().Get("token")
	lastCheck := time.Now()
	defer func() {
		dockerHub.mu.Lock()
		delete(dockerHub.clients, client)
		dockerHub.mu.Unlock()
		_ = conn.Close()
	}()
	for {
		_, data, err := conn.ReadMessage()
		if err != nil {
			return
		}
		var msg map[string]any
		if json.Unmarshal(data, &msg) == nil && str(msg["type"]) == "refresh" {
			select {
			case dockerHub.wake <- struct{}{}:
			default:
			}
		}
		if time.Since(lastCheck) > 30*time.Second {
			lastCheck = time.Now()
			if !WSStillValid(token) {
				return
			}
		}
	}
}
