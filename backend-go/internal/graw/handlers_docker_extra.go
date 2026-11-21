package graw

import (
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

// Docker 容器与编排增强（P1）：
//   - 概览（容器/镜像/卷/网络/编排数量 + docker system df）
//   - 容器创建、批量启停/删除、关联资源
//   - 一键清理（容器/镜像/网络/卷/构建缓存）
//   - compose 导入与模板库
//   - 私有仓库 / 镜像加速查看
//
// 安全约束：容器 ID、容器名、项目名、镜像引用、网络名全部先过白名单正则；
// 清理与批量删除属于高风险动作，前端必须二次确认，后端只接受白名单 target/action。

var containerNameRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`)
var envKeyRe = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)
var volumeSpecRe = regexp.MustCompile(`^[A-Za-z0-9_./@:-]{1,512}$`)

var validPruneTargets = map[string]bool{
	"containers": true, "images": true, "networks": true, "volumes": true, "builder": true,
}

var validBatchActions = map[string]bool{
	"start": true, "stop": true, "restart": true, "remove": true,
}

func dockerOverview() map[string]any {
	status := dockerStatus()
	containers := dockerContainers()
	running := 0
	for _, c := range containers {
		if strings.EqualFold(str(c["state"]), "running") {
			running++
		}
	}
	images := dockerImages()
	networks := dockerNetworks()
	volumes, _ := dockerJSONLines("volume", "ls", "--format", "json")
	compose := composeProjects()

	disk := []map[string]any{}
	if lines, res := dockerJSONLines("system", "df", "--format", "json"); res.Code == 0 {
		for _, item := range lines {
			disk = append(disk, map[string]any{
				"type":        str(item["Type"]),
				"total":       int64Of(item["TotalCount"]),
				"size":        str(item["Size"]),
				"reclaimable": str(item["Reclaimable"]),
			})
		}
	}
	cfg := dockerPanelConfig()
	return map[string]any{
		"status": status,
		"counts": map[string]any{
			"containers": len(containers), "running": running,
			"images": len(images), "networks": len(networks),
			"volumes": len(volumes), "compose_projects": len(compose),
		},
		"disk":       disk,
		"registries": cfg["private_registries"],
		"mirrors":    cfg["mirrors"],
	}
}

func dockerPrune(target string) (string, error) {
	if !validPruneTargets[target] {
		return "", NewHTTPError(400, "不支持的清理目标")
	}
	args := []string{}
	switch target {
	case "containers":
		args = []string{"container", "prune", "-f"}
	case "images":
		args = []string{"image", "prune", "-a", "-f"}
	case "networks":
		args = []string{"network", "prune", "-f"}
	case "volumes":
		args = []string{"volume", "prune", "-f"}
	case "builder":
		args = []string{"builder", "prune", "-f"}
	}
	res := dockerRun(300*time.Second, args...)
	out := strings.TrimSpace(res.Stdout + res.Stderr)
	if res.Code != 0 {
		return out, NewHTTPError(500, "清理失败: "+tailText(out, 2000))
	}
	return out, nil
}

// dockerBatchAction 对一批容器执行动作；逐个返回结果，不因单个失败中断。
func dockerBatchAction(ids []string, action string) map[string]any {
	if !validBatchActions[action] {
		return map[string]any{"ok": false, "error": "不支持的动作", "results": []map[string]any{}}
	}
	if len(ids) > 100 {
		ids = ids[:100]
	}
	results := []map[string]any{}
	for _, id := range ids {
		id = strings.TrimSpace(id)
		if !containerNameRe.MatchString(id) {
			results = append(results, map[string]any{"id": id, "ok": false, "error": "容器标识非法"})
			continue
		}
		args := []string{}
		switch action {
		case "start":
			args = []string{"start", id}
		case "stop":
			args = []string{"stop", id}
		case "restart":
			args = []string{"restart", id}
		case "remove":
			args = []string{"rm", "-f", id}
		}
		res := dockerRun(120*time.Second, args...)
		out := strings.TrimSpace(res.Stdout + res.Stderr)
		results = append(results, map[string]any{
			"id": id, "ok": res.Code == 0, "output": tailText(out, 500),
		})
	}
	return map[string]any{"ok": true, "action": action, "results": results}
}

type containerCreatePort struct {
	Host      int    `json:"host"`
	Container int    `json:"container"`
	Protocol  string `json:"protocol"`
}

type containerCreateEnv struct {
	Key   string `json:"key"`
	Value string `json:"value"`
}

type containerCreateRequest struct {
	Image   string                `json:"image"`
	Name    string                `json:"name"`
	Ports   []containerCreatePort `json:"ports"`
	Env     []containerCreateEnv  `json:"env"`
	Volumes []string              `json:"volumes"`
	Network string                `json:"network"`
	Restart string                `json:"restart"`
	Command string                `json:"command"`
}

func dockerCreateContainer(req containerCreateRequest) (map[string]any, error) {
	if !dockerRefRe.MatchString(strings.TrimSpace(req.Image)) {
		return nil, NewHTTPError(400, "镜像引用非法")
	}
	if req.Name != "" && !containerNameRe.MatchString(req.Name) {
		return nil, NewHTTPError(400, "容器名称非法")
	}
	restart := strings.TrimSpace(req.Restart)
	if restart == "" {
		restart = "unless-stopped"
	}
	if !validRestartPolicies[restart] {
		return nil, NewHTTPError(400, "重启策略非法")
	}
	args := []string{"run", "-d"}
	if req.Name != "" {
		args = append(args, "--name", req.Name)
	}
	args = append(args, "--restart", restart)
	seenPorts := map[string]bool{}
	for _, p := range req.Ports {
		if p.Host < 1 || p.Host > 65535 || p.Container < 1 || p.Container > 65535 {
			return nil, NewHTTPError(400, "端口需在 1-65535 之间")
		}
		proto := strings.ToLower(strings.TrimSpace(p.Protocol))
		if proto == "" {
			proto = "tcp"
		}
		if proto != "tcp" && proto != "udp" {
			return nil, NewHTTPError(400, "端口协议只能是 tcp/udp")
		}
		spec := itoa(p.Host) + ":" + itoa(p.Container) + "/" + proto
		if seenPorts[spec] {
			continue
		}
		seenPorts[spec] = true
		args = append(args, "-p", spec)
	}
	for _, e := range req.Env {
		key := strings.TrimSpace(e.Key)
		if key == "" {
			continue
		}
		if !envKeyRe.MatchString(key) {
			return nil, NewHTTPError(400, "环境变量名非法: "+key)
		}
		args = append(args, "-e", key+"="+e.Value)
	}
	for _, v := range req.Volumes {
		v = strings.TrimSpace(v)
		if v == "" {
			continue
		}
		if !volumeSpecRe.MatchString(v) || strings.Contains(v, "..") {
			return nil, NewHTTPError(400, "卷挂载格式非法: "+v)
		}
		args = append(args, "-v", v)
	}
	if req.Network != "" {
		if !containerNameRe.MatchString(req.Network) {
			return nil, NewHTTPError(400, "网络名称非法")
		}
		args = append(args, "--network", req.Network)
	}
	args = append(args, strings.TrimSpace(req.Image))
	if cmd := strings.TrimSpace(req.Command); cmd != "" {
		if len(cmd) > 4096 {
			return nil, NewHTTPError(400, "启动命令过长")
		}
		args = append(args, "sh", "-c", cmd)
	}
	res := dockerRun(300*time.Second, args...)
	out := strings.TrimSpace(res.Stdout + res.Stderr)
	if res.Code != 0 {
		return nil, NewHTTPError(500, "创建容器失败: "+tailText(out, 2000))
	}
	return map[string]any{"ok": true, "id": strings.TrimSpace(res.Stdout), "output": out}, nil
}

// containerRelated 汇总容器的网络、挂载、镜像与 compose 归属，供「关联资源」面板使用。
func containerRelated(id string) (map[string]any, error) {
	raw, err := dockerInspectRaw(id)
	if err != nil {
		return nil, err
	}
	name := ""
	if v, ok := raw["Name"].(string); ok {
		name = strings.TrimPrefix(v, "/")
	}
	image := ""
	if v, ok := raw["Config"].(map[string]any); ok {
		image = str(v["Image"])
	}
	networks := []string{}
	if net, ok := raw["NetworkSettings"].(map[string]any); ok {
		for key := range mapOf(net["Networks"]) {
			networks = append(networks, key)
		}
	}
	mounts := []map[string]any{}
	if items, ok := raw["Mounts"].([]any); ok {
		for _, item := range items {
			m := mapOf(item)
			mounts = append(mounts, map[string]any{
				"type": str(m["Type"]), "source": str(m["Source"]), "destination": str(m["Destination"]),
				"name": str(m["Name"]), "rw": boolOf(m["RW"]),
			})
		}
	}
	labels := map[string]any{}
	if cfg, ok := raw["Config"].(map[string]any); ok {
		labels = mapOf(cfg["Labels"])
	}
	return map[string]any{
		"id": id, "name": name, "image": image,
		"networks": networks, "mounts": mounts,
		"compose_project": str(labels["com.docker.compose.project"]),
		"compose_service": str(labels["com.docker.compose.service"]),
	}, nil
}

// --- compose 导入与模板库 ---

func composeTemplatePath() string {
	return DataPath("compose_templates.json")
}

func loadComposeTemplates() []map[string]any {
	items := []map[string]any{}
	_ = ReadJSON(composeTemplatePath(), &items)
	return items
}

func saveComposeTemplates(items []map[string]any) error {
	CaptureSnapshot("compose-templates", "docker", composeTemplatePath())
	return WriteJSONFile(composeTemplatePath(), items)
}

func validateComposeText(text string) error {
	var parsed map[string]any
	if err := yaml.Unmarshal([]byte(text), &parsed); err != nil {
		return NewHTTPError(400, "docker-compose.yml 解析失败: "+err.Error())
	}
	if len(mapOf(parsed["services"])) == 0 {
		return NewHTTPError(400, "docker-compose.yml 缺少 services 定义")
	}
	return nil
}

func composeProjectDir(name string) (string, error) {
	if !appIDRe.MatchString(name) {
		return "", NewHTTPError(400, "编排项目名称非法")
	}
	dir := DataPath("compose", name)
	if err := EnsureDir(dir); err != nil {
		return "", err
	}
	return dir, nil
}

// deployComposeProject 把 compose 内容落到 data/compose/<name> 并启动。
func deployComposeProject(name, text string, start bool) (map[string]any, error) {
	if err := validateComposeText(text); err != nil {
		return nil, err
	}
	dir, err := composeProjectDir(name)
	if err != nil {
		return nil, err
	}
	path := filepath.Join(dir, "docker-compose.yml")
	if fileExists(path) {
		return nil, NewHTTPError(409, "同名编排项目已存在")
	}
	if werr := os.WriteFile(path, []byte(text), 0o644); werr != nil {
		return nil, werr
	}
	out := ""
	if start {
		rc, o := composeStream(composeEnginePath(path), []string{"up", "-d", "--remove-orphans"}, nil)
		out = o
		if rc != 0 {
			return nil, NewHTTPError(500, "编排启动失败: "+tailText(o, 2000))
		}
	}
	return map[string]any{"ok": true, "name": name, "path": path, "output": tailText(out, 2000)}, nil
}

func registerDockerExtraRoutes(a *App) {
	a.Get("/api/docker/overview", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, dockerOverview())
	})
	a.Post("/api/docker/prune", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Target string `json:"target"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		out, err := dockerPrune(strings.TrimSpace(req.Target))
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "output": out})
	})
	a.Post("/api/docker/containers/batch", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			IDs    []string `json:"ids"`
			Action string   `json:"action"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		return WriteJSON(w, 200, dockerBatchAction(req.IDs, strings.TrimSpace(req.Action)))
	})
	a.Post("/api/docker/containers/create", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		var req containerCreateRequest
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		res, err := dockerCreateContainer(req)
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, res)
	})
	a.Get("/api/docker/containers/{container_id}/related", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		res, err := containerRelated(strings.TrimSpace(PathParam(r, "container_id")))
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, res)
	})
	a.Get("/api/docker/registries", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		cfg := dockerPanelConfig()
		return WriteJSON(w, 200, map[string]any{
			"private_registries": cfg["private_registries"],
			"mirrors":            cfg["mirrors"],
		})
	})
	a.Post("/api/docker/compose/import", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Name    string `json:"name"`
			Compose string `json:"compose"`
			Start   bool   `json:"start"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		res, err := deployComposeProject(strings.TrimSpace(req.Name), req.Compose, req.Start)
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, res)
	})
	a.Get("/api/docker/compose/templates", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"templates": loadComposeTemplates()})
	})
	a.Post("/api/docker/compose/templates", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Name    string `json:"name"`
			Compose string `json:"compose"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		name := strings.TrimSpace(req.Name)
		if !appIDRe.MatchString(name) {
			return NewHTTPError(400, "模板名称非法")
		}
		if err := validateComposeText(req.Compose); err != nil {
			return err
		}
		items := loadComposeTemplates()
		now := time.Now().Format("2006-01-02 15:04:05")
		found := false
		for _, item := range items {
			if str(item["name"]) == name {
				item["compose"] = req.Compose
				item["updated_at"] = now
				found = true
				break
			}
		}
		if !found {
			items = append(items, map[string]any{
				"name": name, "compose": req.Compose, "created_at": now, "updated_at": now,
			})
		}
		if err := saveComposeTemplates(items); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "templates": items})
	})
	a.Delete("/api/docker/compose/templates/{template_name}", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		name := strings.TrimSpace(PathParam(r, "template_name"))
		items := loadComposeTemplates()
		out := items[:0]
		for _, item := range items {
			if str(item["name"]) != name {
				out = append(out, item)
			}
		}
		if err := saveComposeTemplates(out); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/docker/compose/templates/{template_name}/deploy", perm("docker"), func(w http.ResponseWriter, r *http.Request) error {
		templateName := strings.TrimSpace(PathParam(r, "template_name"))
		var req struct {
			Project string `json:"project"`
			Start   bool   `json:"start"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		text := ""
		for _, item := range loadComposeTemplates() {
			if str(item["name"]) == templateName {
				text = str(item["compose"])
				break
			}
		}
		if text == "" {
			return NewHTTPError(404, "模板不存在")
		}
		name := strings.TrimSpace(req.Project)
		if name == "" {
			name = templateName
		}
		res, err := deployComposeProject(name, text, req.Start)
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, res)
	})
}
