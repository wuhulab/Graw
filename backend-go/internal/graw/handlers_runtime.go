package graw

import (
	"net"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// 运行环境管理（与 Python routers/runtime.py 数据模型 / 接口字段保持一致）。

const (
	runtimeDefaultWorkdir = "/app"
	runtimeContainerPfx   = "graw-rt-"
)

type runtimeDef struct {
	Key            string
	Label          string
	Versions       []string
	DefaultVersion string
	Image          func(string) string
	Workdir        string
	SuggestCmd     string
}

var runtimeDefs = []runtimeDef{
	{"python", "Python", []string{"3.13", "3.12", "3.11", "3.10", "3.9"}, "3.12",
		func(v string) string { return "python:" + v }, "/app", "python app.py"},
	{"java", "Java", []string{"17", "21", "11", "8"}, "17",
		func(v string) string { return "openjdk:" + v }, "/app", "java -jar app.jar"},
	{"node", "Node.js", []string{"20", "22", "18", "16"}, "20",
		func(v string) string { return "node:" + v }, "/app", "npm start"},
	{"go", "Go", []string{"1.23", "1.22", "1.21", "1.20"}, "1.22",
		func(v string) string { return "golang:" + v }, "/app", "go run main.go"},
	{"dotnet", ".NET", []string{"8.0", "9.0", "6.0"}, "8.0",
		func(v string) string { return "mcr.microsoft.com/dotnet/sdk:" + v }, "/app", "dotnet run"},
	{"php", "PHP", []string{"8.3", "8.2", "8.1", "8.0"}, "8.2",
		func(v string) string { return "php:" + v + "-apache" }, "/var/www/html", ""},
	{"html", "HTML Project", []string{"3", "3.12"}, "3",
		func(v string) string { return "python:" + v + "-alpine" }, "/app", "python -m http.server 80"},
	{"other", "Other", []string{"12", "11", "10"}, "12",
		func(v string) string { return "debian:" + v }, "/app", ""},
}

func runtimeDefByKey(key string) *runtimeDef {
	for i := range runtimeDefs {
		if runtimeDefs[i].Key == key {
			return &runtimeDefs[i]
		}
	}
	return nil
}

var (
	runtimeNameRe      = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]*$`)
	runtimeImageRe     = regexp.MustCompile(`^[a-z0-9][a-z0-9](?:[a-z0-9._/-]*[a-z0-9_])?(?::[A-Za-z0-9][A-Za-z0-9._-]{0,127})?$`)
	runtimeVersionRe   = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$`)
	runtimeDigitsRe    = regexp.MustCompile(`^\d{1,5}$`)
	runtimeEnvNameRe   = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)
	runtimeHostnameRe  = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,253}$`)
	runtimeTypeAllowed = map[string]bool{
		"python": true, "java": true, "node": true, "go": true,
		"dotnet": true, "php": true, "html": true, "other": true,
	}
)

type runtimePortMap struct {
	External string `json:"external"`
	Internal string `json:"internal"`
	Protocol string `json:"protocol"`
}

type runtimeEnvVar struct {
	Name  string `json:"name"`
	Value string `json:"value"`
}

type runtimeMountItem struct {
	Host      string `json:"host"`
	Container string `json:"container"`
	Mode      string `json:"mode"`
}

type runtimeHostMap struct {
	Hostname string `json:"hostname"`
	IP       string `json:"ip"`
}

type runtimeCreateRequest struct {
	Type           string             `json:"type"`
	Name           string             `json:"name"`
	ProjectDir     string             `json:"project_dir"`
	StartCommand   string             `json:"start_command"`
	AppVersion     string             `json:"app_version"`
	ContainerName  string             `json:"container_name"`
	Notes          string             `json:"notes"`
	HTMLPort       *string            `json:"html_port"`
	InstallCommand string             `json:"install_command"`
	Ports          []runtimePortMap   `json:"ports"`
	Env            []runtimeEnvVar    `json:"env"`
	Mounts         []runtimeMountItem `json:"mounts"`
	Hosts          []runtimeHostMap   `json:"hosts"`
}

func loadRuntimeConfigs() []map[string]any {
	configs := []map[string]any{}
	_ = ReadJSON(DataPath("runtime.json"), &configs)
	if configs == nil {
		configs = []map[string]any{}
	}
	return configs
}

func saveRuntimeConfigs(configs []map[string]any) error {
	return WriteJSONFile(DataPath("runtime.json"), configs)
}

func runtimeConfigByID(id string) map[string]any {
	for _, c := range loadRuntimeConfigs() {
		if str(c["id"]) == id {
			return c
		}
	}
	return nil
}

// validateRuntimeMountHost 校验将被挂载进容器的宿主路径（防容器逃逸）。
func validateRuntimeMountHost(host, field string) error {
	host = strings.TrimSpace(host)
	if host == "" {
		return NewHTTPError(400, field+"不能为空")
	}
	if strings.HasPrefix(host, `\\?\`) || strings.HasPrefix(host, `\\.\`) {
		return NewHTTPError(400, field+"非法（不支持设备命名空间路径）")
	}
	if strings.HasPrefix(host, `\\`) || strings.HasPrefix(host, "//") || (len(host) > 1 && host[1] == ':') {
		return NewHTTPError(400, field+"非法（不支持 Windows 盘符/UNC 路径）")
	}
	for _, c := range host {
		if c < 0x20 || c == 0x7F {
			return NewHTTPError(400, field+"包含非法控制字符")
		}
	}
	norm := path.Clean(strings.ReplaceAll(host, "\\", "/"))
	if norm == "/" || norm == "." {
		return NewHTTPError(400, field+"不能是根目录或当前目录")
	}
	if strings.Contains(norm, "docker.sock") {
		return NewHTTPError(400, field+"禁止挂载 docker.sock（宿主逃逸风险）")
	}
	systemRoots := []string{"/etc", "/usr", "/var", "/run", "/bin", "/sbin", "/boot",
		"/proc", "/sys", "/dev", "/lib", "/lib64", "/root"}
	for _, extra := range strings.Split(os.Getenv("GRAW_RUNTIME_MOUNT_DENY"), ",") {
		if e := strings.TrimSpace(extra); e != "" {
			systemRoots = append(systemRoots, path.Clean(strings.ReplaceAll(e, "\\", "/")))
		}
	}
	for _, root := range systemRoots {
		if norm == root || strings.HasPrefix(norm, root+"/") {
			return NewHTTPError(400, field+"位于系统敏感目录（"+root+"），禁止挂载")
		}
	}
	dataDir, _ := filepath.Abs(DataPath())
	dataNorm := path.Clean(filepath.ToSlash(dataDir))
	if norm == dataNorm || strings.HasPrefix(norm, dataNorm+"/") {
		return NewHTTPError(400, field+"位于面板数据目录，禁止挂载")
	}
	return nil
}

func runtimeContainerStatus(name string) map[string]any {
	if name == "" {
		return map[string]any{"exists": false, "running": false, "state": "unknown", "status": "", "container_id": ""}
	}
	items, res := dockerJSONArray("ps", "-a", "--filter", "name=^"+name+"$", "--format", "json")
	if res.Code != 0 || len(items) == 0 {
		return map[string]any{"exists": false, "running": false, "state": "unknown", "status": "", "container_id": ""}
	}
	status := str(items[0]["Status"])
	running := strings.HasPrefix(status, "Up")
	state := "exited"
	if running {
		state = "running"
	}
	id := str(items[0]["Id"])
	if len(id) > 12 {
		id = id[:12]
	}
	return map[string]any{
		"exists": true, "running": running, "state": state,
		"status": status, "container_id": id,
	}
}

func runtimeBuildImage(cfg map[string]any) string {
	rt := runtimeDefByKey(str(cfg["type"]))
	if rt == nil {
		return str(cfg["app_version"])
	}
	version := str(cfg["app_version"])
	if version == "" {
		version = rt.DefaultVersion
	}
	return rt.Image(version)
}

func runtimeDefaultCommand(cfg map[string]any) string {
	if cmd := strings.TrimSpace(str(cfg["start_command"])); cmd != "" {
		return cmd
	}
	if rt := runtimeDefByKey(str(cfg["type"])); rt != nil {
		return rt.SuggestCmd
	}
	return ""
}

func runtimeEntryScript(command, installCmd string) string {
	installCmd = strings.TrimSpace(installCmd)
	if installCmd == "" {
		return ""
	}
	command = strings.TrimSpace(command)
	if command != "" {
		return installCmd + " && " + command
	}
	return installCmd + " && tail -f /dev/null"
}

// splitRuntimeCommand 模拟 shlex.split（支持单双引号与反斜杠转义）。
func splitRuntimeCommand(text string) []string {
	tokens := []string{}
	current := strings.Builder{}
	quote := rune(0)
	escaped := false
	hasToken := false
	for _, r := range text {
		if escaped {
			current.WriteRune(r)
			escaped = false
			hasToken = true
			continue
		}
		if r == '\\' && quote != '\'' {
			escaped = true
			hasToken = true
			continue
		}
		if quote != 0 {
			if r == quote {
				quote = 0
			} else {
				current.WriteRune(r)
			}
			hasToken = true
			continue
		}
		if r == '\'' || r == '"' {
			quote = r
			hasToken = true
			continue
		}
		if r == ' ' || r == '\t' || r == '\n' {
			if hasToken {
				tokens = append(tokens, current.String())
				current.Reset()
				hasToken = false
			}
			continue
		}
		current.WriteRune(r)
		hasToken = true
	}
	if hasToken {
		tokens = append(tokens, current.String())
	}
	return tokens
}

func runtimeMountPath(host string) string {
	return host
}

func runtimeBuildRunArgs(cfg map[string]any) []string {
	args := []string{}
	workdir := strings.TrimSuffix(defaultString(str(cfg["workdir"]), runtimeDefaultWorkdir), "/")
	if projectDir := str(cfg["project_dir"]); projectDir != "" {
		args = append(args, "-v", runtimeMountPath(projectDir)+":"+workdir)
	}
	if ports, ok := cfg["ports"].([]any); ok {
		for _, raw := range ports {
			p := mapOf(raw)
			external, internal := str(p["external"]), str(p["internal"])
			if external == "" || internal == "" {
				continue
			}
			proto := str(p["protocol"])
			if proto != "tcp" && proto != "udp" {
				proto = "tcp"
			}
			args = append(args, "-p", external+":"+internal+"/"+proto)
		}
	}
	if str(cfg["type"]) == "html" && str(cfg["html_port"]) != "" {
		args = append(args, "-p", str(cfg["html_port"])+":80/tcp")
	}
	if envs, ok := cfg["env"].([]any); ok {
		for _, raw := range envs {
			e := mapOf(raw)
			name := str(e["name"])
			if name == "" {
				continue
			}
			args = append(args, "-e", name+"="+str(e["value"]))
		}
	}
	if mounts, ok := cfg["mounts"].([]any); ok {
		for _, raw := range mounts {
			m := mapOf(raw)
			host, container := str(m["host"]), str(m["container"])
			if host == "" || container == "" {
				continue
			}
			spec := runtimeMountPath(host) + ":" + container
			if str(m["mode"]) == "ro" {
				spec += ":ro"
			}
			args = append(args, "-v", spec)
		}
	}
	if hosts, ok := cfg["hosts"].([]any); ok {
		for _, raw := range hosts {
			h := mapOf(raw)
			if hostname, ip := str(h["hostname"]), str(h["ip"]); hostname != "" && ip != "" {
				args = append(args, "--add-host", hostname+":"+ip)
			}
		}
	}
	args = append(args, "-w", workdir)
	return args
}

func runtimeCreateContainer(cfg map[string]any) (map[string]any, error) {
	baseName := str(cfg["container_name"])
	if baseName == "" {
		baseName = runtimeContainerPfx + defaultString(str(cfg["name"]), "t")
	}
	if !runtimeNameRe.MatchString(baseName) {
		return nil, NewHTTPError(400, "容器名称不合法: "+baseName)
	}
	finalName := baseName
	suffix := 0
	for runtimeContainerStatus(finalName)["exists"] == true {
		suffix++
		finalName = baseName + "-" + itoa(suffix)
	}
	image := runtimeBuildImage(cfg)
	if image == "" || !runtimeImageRe.MatchString(image) || strings.HasPrefix(image, "-") {
		return nil, NewHTTPError(400, "镜像名不合法: "+image)
	}
	command := runtimeDefaultCommand(cfg)
	script := runtimeEntryScript(command, str(cfg["install_command"]))
	args := []string{"run", "-d", "--name", finalName, "--label", "graw.runtime=1"}
	args = append(args, runtimeBuildRunArgs(cfg)...)
	args = append(args, image)
	if script != "" {
		args = append(args, "sh", "-lc", script)
	} else if command != "" {
		args = append(args, splitRuntimeCommand(command)...)
	}
	res := dockerRun(600*time.Second, args...)
	if res.Code != 0 {
		detail := strings.TrimSpace(res.Stderr)
		if detail == "" {
			detail = strings.TrimSpace(res.Stdout)
		}
		if detail == "" {
			detail = "容器创建失败"
		}
		return nil, NewHTTPError(500, detail)
	}
	containerID := ""
	if out := strings.TrimSpace(res.Stdout); out != "" {
		lines := strings.Split(out, "\n")
		containerID = tailText(lines[len(lines)-1], 12)
	}
	return map[string]any{
		"container_name": finalName, "container_id": containerID,
		"image": image, "command": command,
	}, nil
}

func validateRuntimeCreate(req *runtimeCreateRequest) error {
	if !runtimeTypeAllowed[req.Type] {
		return NewHTTPError(422, "不支持的运行时类型: "+req.Type)
	}
	req.Name = strings.TrimSpace(req.Name)
	if req.Name == "" || len(req.Name) > 64 {
		return NewHTTPError(422, "名称必填且不超过 64 字符")
	}
	req.ProjectDir = strings.TrimSpace(req.ProjectDir)
	if !filepath.IsAbs(req.ProjectDir) && !strings.HasPrefix(req.ProjectDir, "/") {
		return NewHTTPError(400, "项目目录必须为绝对路径")
	}
	if err := validateRuntimeMountHost(req.ProjectDir, "项目目录"); err != nil {
		return err
	}
	for _, m := range req.Mounts {
		if err := validateRuntimeMountHost(m.Host, "挂载源路径"); err != nil {
			return err
		}
	}
	if req.ContainerName != "" && !runtimeNameRe.MatchString(req.ContainerName) {
		return NewHTTPError(400, "容器名称只能包含字母、数字、_、-、.，且不能以 - 开头")
	}
	if req.AppVersion != "" && !runtimeVersionRe.MatchString(req.AppVersion) {
		return NewHTTPError(422, "版本号格式非法")
	}
	for _, p := range req.Ports {
		for _, side := range []string{p.External, p.Internal} {
			if side == "" {
				continue
			}
			if !runtimeDigitsRe.MatchString(side) {
				return NewHTTPError(422, "端口格式非法: "+side)
			}
			n, _ := strconv.Atoi(side)
			if n < 1 || n > 65535 {
				return NewHTTPError(422, "端口越界: "+side+"（必须在 1-65535）")
			}
		}
	}
	for _, e := range req.Env {
		if e.Name != "" && !runtimeEnvNameRe.MatchString(e.Name) {
			return NewHTTPError(422, "环境变量名非法: "+e.Name)
		}
	}
	for _, m := range req.Mounts {
		if m.Container != "" && !strings.HasPrefix(m.Container, "/") {
			return NewHTTPError(422, "容器内挂载路径必须是绝对路径")
		}
	}
	for _, h := range req.Hosts {
		if h.Hostname != "" && !runtimeHostnameRe.MatchString(h.Hostname) {
			return NewHTTPError(422, "主机名非法: "+h.Hostname)
		}
		if h.IP != "" && net.ParseIP(h.IP) == nil {
			return NewHTTPError(422, "主机映射 IP 不合法: "+h.IP)
		}
	}
	if req.HTMLPort != nil {
		if !runtimeDigitsRe.MatchString(*req.HTMLPort) {
			return NewHTTPError(422, "启动端口格式非法")
		}
		n, _ := strconv.Atoi(*req.HTMLPort)
		if n < 1 || n > 65535 {
			return NewHTTPError(422, "启动端口越界（必须在 1-65535）")
		}
	}
	return nil
}

func registerRuntimeRoutes(a *App) {
	a.Get("/api/runtime/templates", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		out := []map[string]any{}
		for _, rt := range runtimeDefs {
			out = append(out, map[string]any{
				"type": rt.Key, "label": rt.Label, "versions": rt.Versions,
				"default_version": rt.DefaultVersion, "workdir": rt.Workdir,
				"suggest_cmd": rt.SuggestCmd, "image": rt.Image(rt.DefaultVersion),
			})
		}
		return WriteJSON(w, 200, map[string]any{
			"runtimes": out, "port_protocols": []string{"tcp", "udp"},
			"mount_modes": []string{"rw", "ro"},
		})
	})
	a.Get("/api/runtime/list", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		out := []map[string]any{}
		for _, c := range loadRuntimeConfigs() {
			item := map[string]any{}
			for k, v := range c {
				item[k] = v
			}
			item["status"] = runtimeContainerStatus(str(c["container_name"]))
			out = append(out, item)
		}
		return WriteJSON(w, 200, map[string]any{"runtimes": out})
	})
	a.Post("/api/runtime/create", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		var req runtimeCreateRequest
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if err := validateRuntimeCreate(&req); err != nil {
			return err
		}
		rt := runtimeDefByKey(req.Type)
		version := strings.TrimSpace(req.AppVersion)
		if version == "" {
			version = rt.DefaultVersion
		}
		cfg := map[string]any{
			"id": "rt_" + randomHex(8), "type": req.Type, "name": req.Name,
			"project_dir": req.ProjectDir, "start_command": strings.TrimSpace(req.StartCommand),
			"app_version": version, "container_name": strings.TrimSpace(req.ContainerName),
			"notes": strings.TrimSpace(req.Notes), "html_port": nil,
			"install_command": strings.TrimSpace(req.InstallCommand),
			"ports":           []any{}, "env": []any{}, "mounts": []any{}, "hosts": []any{},
			"workdir": rt.Workdir, "created_at": nowISO(),
		}
		if req.HTMLPort != nil {
			cfg["html_port"] = *req.HTMLPort
		}
		for _, p := range req.Ports {
			proto := p.Protocol
			if proto == "" {
				proto = "tcp"
			}
			cfg["ports"] = append(cfg["ports"].([]any), map[string]any{
				"external": p.External, "internal": p.Internal, "protocol": proto,
			})
		}
		for _, e := range req.Env {
			cfg["env"] = append(cfg["env"].([]any), map[string]any{"name": e.Name, "value": e.Value})
		}
		for _, m := range req.Mounts {
			mode := m.Mode
			if mode != "ro" {
				mode = "rw"
			}
			cfg["mounts"] = append(cfg["mounts"].([]any), map[string]any{
				"host": m.Host, "container": m.Container, "mode": mode,
			})
		}
		for _, h := range req.Hosts {
			cfg["hosts"] = append(cfg["hosts"].([]any), map[string]any{"hostname": h.Hostname, "ip": h.IP})
		}
		created, err := runtimeCreateContainer(cfg)
		if err != nil {
			return err
		}
		cfg["container_name"] = created["container_name"]
		cfg["container_id"] = created["container_id"]
		cfg["image"] = created["image"]
		configs := loadRuntimeConfigs()
		configs = append(configs, cfg)
		if err := saveRuntimeConfigs(configs); err != nil {
			return err
		}
		return WriteJSON(w, 200, cfg)
	})
	a.Post("/api/runtime/{rid}/delete", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "rid")
		cfg := runtimeConfigByID(id)
		if cfg == nil {
			return NewHTTPError(404, "运行环境不存在")
		}
		name := str(cfg["container_name"])
		if name != "" && runtimeContainerStatus(name)["exists"] == true {
			res := dockerRun(120*time.Second, "rm", "-f", name)
			if res.Code != 0 {
				detail := strings.TrimSpace(res.Stderr)
				if detail == "" {
					detail = "删除容器失败"
				}
				return NewHTTPError(500, detail)
			}
		}
		kept := []map[string]any{}
		for _, c := range loadRuntimeConfigs() {
			if str(c["id"]) != id {
				kept = append(kept, c)
			}
		}
		if err := saveRuntimeConfigs(kept); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/runtime/{rid}/action", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "rid")
		var req struct {
			Action string `json:"action"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		cfg := runtimeConfigByID(id)
		if cfg == nil {
			return NewHTTPError(404, "运行环境不存在")
		}
		name := str(cfg["container_name"])
		if name == "" || runtimeContainerStatus(name)["exists"] != true {
			return NewHTTPError(404, "运行环境容器不存在，请重新创建")
		}
		var args []string
		switch req.Action {
		case "start", "stop", "restart":
			args = []string{req.Action, name}
		case "remove":
			args = []string{"rm", "-f", name}
		default:
			return NewHTTPError(400, "不支持的操作: "+req.Action)
		}
		res := dockerRun(180*time.Second, args...)
		if res.Code != 0 {
			detail := strings.TrimSpace(res.Stderr)
			if detail == "" {
				detail = "操作失败"
			}
			return NewHTTPError(500, detail)
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
}
