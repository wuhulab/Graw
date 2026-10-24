package graw

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"gopkg.in/yaml.v3"
)

// 应用商店 / 任务中心（语言运行时见 handlers_runtime.go）。

var appIDRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`)

const (
	defaultAppIndexURL = "https://wuhulab.github.io/Graw-app-store/index.json"
	remoteIndexTTL     = 86400
	localIndexTTL      = 60
	readmeMaxBytes     = 512 * 1024
)

var validRestartPolicies = map[string]bool{
	"no": true, "always": true, "unless-stopped": true, "on-failure": true,
}

func appStoreDir() string {
	if v := strings.TrimSpace(os.Getenv("GRAW_APP_STORE_DIR")); v != "" {
		return v
	}
	for _, p := range []string{filepath.Join("..", "app-store"), "app-store"} {
		if IsDir(p) {
			abs, _ := filepath.Abs(p)
			return abs
		}
	}
	return ""
}

// --- 索引地址配置 ---

func loadAppStoreConfig() map[string]any {
	cfg := map[string]any{"index_url": ""}
	_ = ReadJSON(DataPath("appstore.json"), &cfg)
	if cfg == nil {
		cfg = map[string]any{"index_url": ""}
	}
	return cfg
}

func saveAppStoreConfig(cfg map[string]any) error {
	return WriteJSONFile(DataPath("appstore.json"), cfg)
}

type appIndexCacheState struct {
	mu     sync.Mutex
	at     float64
	source string
	data   map[string]any
	err    string
}

var appIndexCache = &appIndexCacheState{}

func clearAppIndexCache() {
	appIndexCache.mu.Lock()
	appIndexCache.at = 0
	appIndexCache.data = nil
	appIndexCache.mu.Unlock()
}

func loadLocalAppIndex() map[string]any {
	dir := appStoreDir()
	if dir == "" {
		return nil
	}
	b, err := os.ReadFile(filepath.Join(dir, "index.json"))
	if err != nil {
		return nil
	}
	var data map[string]any
	if err := json.Unmarshal(b, &data); err != nil || data == nil {
		return nil
	}
	return data
}

// assertPublicHTTPURL SSRF 防护：仅允许 http/https，且目标必须是公网地址。
func assertPublicHTTPURL(rawURL string) error {
	u, err := url.Parse(rawURL)
	if err != nil || u.Scheme == "" {
		return NewHTTPError(400, "仅支持 http/https 地址")
	}
	scheme := strings.ToLower(u.Scheme)
	if scheme != "http" && scheme != "https" {
		return NewHTTPError(400, "仅支持 http/https 地址")
	}
	if os.Getenv("GRAW_APPSTORE_ALLOW_PRIVATE_NET") == "1" {
		return nil
	}
	host := u.Hostname()
	if host == "" {
		return NewHTTPError(400, "URL 缺少主机名")
	}
	ips, err := net.LookupIP(host)
	if err != nil {
		return NewHTTPError(400, "无法解析主机名: "+host)
	}
	for _, ip := range ips {
		if !isPublicIP(ip) {
			return NewHTTPError(400, "目标主机 "+host+" 不是公网地址，已拒绝（SSRF 防护）")
		}
	}
	return nil
}

func isPublicIP(ip net.IP) bool {
	if ip == nil || ip.IsLoopback() || ip.IsUnspecified() || ip.IsMulticast() ||
		ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() || ip.IsPrivate() {
		return false
	}
	// 100.64.0.0/10（运营商级 NAT，Python ipaddress.is_global 视为非公网）
	if v4 := ip.To4(); v4 != nil && v4[0] == 100 && v4[1] >= 64 && v4[1] <= 127 {
		return false
	}
	return ip.IsGlobalUnicast()
}

// fetchAppURL 拉取远程内容：入口与每一跳重定向均校验公网 http(s)。
func fetchAppURL(rawURL string, timeout time.Duration, maxBytes int64) (string, error) {
	if err := assertPublicHTTPURL(rawURL); err != nil {
		return "", err
	}
	client := &http.Client{
		Timeout: timeout,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) > 10 {
				return fmt.Errorf("重定向次数过多")
			}
			if err := assertPublicHTTPURL(req.URL.String()); err != nil {
				return err
			}
			return nil
		},
	}
	req, err := http.NewRequest("GET", rawURL, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("User-Agent", "Graw-Panel/1.0")
	req.Header.Set("Accept", "application/json")
	resp, err := client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return "", fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	reader := io.Reader(resp.Body)
	if maxBytes > 0 {
		reader = io.LimitReader(resp.Body, maxBytes+1)
	}
	b, err := io.ReadAll(reader)
	if err != nil {
		return "", err
	}
	if maxBytes > 0 && int64(len(b)) > maxBytes {
		return "", NewHTTPError(413, "响应内容过大，已中止拉取")
	}
	return string(b), nil
}

// loadAppIndex 返回 (source, data, errorText, err)。本地商店目录存在时优先本地，
// 否则拉取远程索引（最多每 24h 一次）。
func loadAppIndex(refresh bool) (string, map[string]any, string, error) {
	now := float64(time.Now().Unix())
	useLocal := IsDir(appStoreDir())

	appIndexCache.mu.Lock()
	defer appIndexCache.mu.Unlock()

	if !refresh {
		ttl := float64(remoteIndexTTL)
		if useLocal {
			ttl = localIndexTTL
		}
		if appIndexCache.at != 0 && (now-appIndexCache.at) < ttl && appIndexCache.data != nil {
			return appIndexCache.source, appIndexCache.data, appIndexCache.err, nil
		}
	}

	errText := ""
	if useLocal {
		data := loadLocalAppIndex()
		if data != nil {
			appIndexCache.at, appIndexCache.source, appIndexCache.data, appIndexCache.err = now, "local", data, ""
			return "local", data, "", nil
		}
		errText = "本地 app-store/index.json 缺失"
	}

	cfg := loadAppStoreConfig()
	indexURL := strings.TrimSpace(str(cfg["index_url"]))
	if indexURL == "" {
		indexURL = defaultAppIndexURL
	}
	// 远程索引每天最多拉取一次：即使手动刷新，24h 内也直接返回缓存
	if appIndexCache.at != 0 && (now-appIndexCache.at) < remoteIndexTTL && appIndexCache.data != nil {
		src := appIndexCache.source
		if refresh {
			src = "remote_cached"
			errText = "已达每日拉取上限（一天最多刷新一次），请明天再试"
		}
		return src, appIndexCache.data, errText, nil
	}

	raw, err := fetchAppURL(indexURL, 30*time.Second, 8<<20)
	if err != nil {
		errText = "远程索引拉取失败: " + err.Error()
		return "", nil, errText, NewHTTPError(502, errText)
	}
	var data map[string]any
	if err := json.Unmarshal([]byte(raw), &data); err != nil || data == nil {
		errText = "索引格式不正确（缺少 apps 字段）"
		return "", nil, errText, NewHTTPError(502, errText)
	}
	if _, ok := data["apps"]; !ok {
		errText = "索引格式不正确（缺少 apps 字段）"
		return "", nil, errText, NewHTTPError(502, errText)
	}
	appIndexCache.at, appIndexCache.source, appIndexCache.data, appIndexCache.err = now, "remote", data, ""
	return "remote", data, "", nil
}

func appList(data map[string]any) []map[string]any {
	out := []map[string]any{}
	if arr, ok := data["apps"].([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok {
				out = append(out, m)
			}
		}
	}
	return out
}

func findApp(id string) (map[string]any, error) {
	_, data, _, err := loadAppIndex(false)
	if err != nil {
		return nil, err
	}
	for _, app := range appList(data) {
		if str(app["id"]) == id {
			return app, nil
		}
	}
	return nil, NewHTTPError(404, "应用不存在: "+id)
}

// appComposeText 按 compose_url 拉取 docker-compose.yml，失败时回退本地文件。
func appComposeText(app map[string]any) (string, error) {
	id := str(app["id"])
	if !appIDRe.MatchString(id) {
		return "", NewHTTPError(400, "应用 id 非法: "+id)
	}
	localPath := ""
	if dir := appStoreDir(); dir != "" {
		localPath = filepath.Join(dir, "apps", id, "docker-compose.yml")
	}
	if remoteURL := str(app["compose_url"]); remoteURL != "" {
		if text, err := fetchAppURL(remoteURL, 60*time.Second, 4<<20); err == nil {
			return text, nil
		} else if Exists(localPath) {
			if b, rerr := os.ReadFile(localPath); rerr == nil {
				return string(b), nil
			}
		} else {
			return "", NewHTTPError(502, "拉取 docker-compose.yml 失败: "+err.Error())
		}
	}
	if localPath != "" && Exists(localPath) {
		if b, err := os.ReadFile(localPath); err == nil {
			return string(b), nil
		}
	}
	return "", NewHTTPError(404, "应用未提供 docker-compose.yml")
}

// --- GitHub README ---

var githubRepoRe = regexp.MustCompile(`github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$`)
var githubOwnerRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$`)

func parseGitHubRepo(source string) (string, string, bool) {
	if source == "" {
		return "", "", false
	}
	m := githubRepoRe.FindStringSubmatch(source)
	if len(m) < 3 {
		return "", "", false
	}
	owner := m[1]
	name := strings.TrimRight(m[2], "/")
	if !githubOwnerRe.MatchString(owner) || !githubOwnerRe.MatchString(name) {
		return "", "", false
	}
	return owner, name, true
}

func fetchReadmeURL(rawURL string) (string, error) {
	client := &http.Client{Timeout: 30 * time.Second}
	req, err := http.NewRequest("GET", rawURL, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("User-Agent", "Graw-Panel/1.0")
	resp, err := client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return "", fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	b, err := io.ReadAll(io.LimitReader(resp.Body, readmeMaxBytes+1))
	if err != nil {
		return "", err
	}
	if len(b) > readmeMaxBytes {
		return "", NewHTTPError(413, "README 过大，已中止拉取")
	}
	return string(b), nil
}

func appReadme(app map[string]any) (string, string, error) {
	repo := str(app["source"])
	owner, name, ok := parseGitHubRepo(repo)
	if !ok {
		return "", "", NewHTTPError(400, "该应用未提供有效的 GitHub 开源社区地址")
	}
	apiReq, _ := http.NewRequest("GET", "https://api.github.com/repos/"+owner+"/"+name+"/readme", nil)
	apiReq.Header.Set("Accept", "application/vnd.github.raw+json")
	apiReq.Header.Set("User-Agent", "Graw-Panel/1.0")
	content := ""
	client := &http.Client{Timeout: 30 * time.Second}
	if resp, err := client.Do(apiReq); err == nil {
		func() {
			defer resp.Body.Close()
			if resp.StatusCode >= 200 && resp.StatusCode < 300 {
				b, rerr := io.ReadAll(io.LimitReader(resp.Body, readmeMaxBytes+1))
				if rerr == nil {
					if len(b) > readmeMaxBytes {
						content = ""
						return
					}
					content = string(b)
				}
			}
		}()
		if content == "" {
			// 超限时与 Python 一致直接返回 413，不再尝试 raw 兜底
			if resp.ContentLength > readmeMaxBytes {
				return "", "", NewHTTPError(413, "README 过大，已中止拉取")
			}
		}
	}
	if content == "" {
		for _, branch := range []string{"HEAD", "main", "master"} {
			raw := "https://raw.githubusercontent.com/" + owner + "/" + name + "/" + branch + "/README.md"
			text, err := fetchReadmeURL(raw)
			if err == nil {
				content = text
				break
			}
			if he, ok := err.(*HTTPError); ok && he.Status == 413 {
				return "", "", he
			}
		}
	}
	if content == "" {
		return "", "", NewHTTPError(502, "拉取 README 失败: "+owner+"/"+name)
	}
	return owner + "/" + name, content, nil
}

// --- 安装请求与 compose 改写 ---

type appInstallRequest struct {
	AppID         string           `json:"app_id"`
	AppName       string           `json:"app_name"`
	Version       string           `json:"version"`
	Port          *int             `json:"port"`
	Ports         []map[string]any `json:"ports"`
	Timezone      string           `json:"timezone"`
	ContainerName string           `json:"container_name"`
	ExposePort    bool             `json:"expose_port"`
	Restart       string           `json:"restart"`
	CPULimit      float64          `json:"cpu_limit"`
	MemLimitMB    int              `json:"mem_limit_mb"`
	Pull          *bool            `json:"pull"`
	Compose       string           `json:"compose"`
}

func (req *appInstallRequest) normalize() {
	if req.Version == "" {
		req.Version = "latest"
	}
	if req.Timezone == "" {
		req.Timezone = "Asia/Shanghai"
	}
	if req.Restart == "" {
		req.Restart = "always"
	}
}

func (req appInstallRequest) validate() error {
	if !appIDRe.MatchString(req.AppName) {
		return NewHTTPError(400, "Graw 维护应用名称只能包含英文字母 / 数字 / _ / - / .，且必须以字母或数字开头")
	}
	if !validRestartPolicies[req.Restart] {
		return NewHTTPError(400, "非法重启策略: "+req.Restart)
	}
	if req.ContainerName != "" && !appIDRe.MatchString(req.ContainerName) {
		return NewHTTPError(400, "容器名称只能包含英文字母 / 数字 / _ / - / .")
	}
	if len(req.Version) > 128 || !regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._+~/-]*$`).MatchString(req.Version) {
		return NewHTTPError(422, "版本号格式非法")
	}
	if len(req.Timezone) > 64 || !regexp.MustCompile(`^[A-Za-z0-9_+\-/]+$`).MatchString(req.Timezone) {
		return NewHTTPError(422, "时区格式非法")
	}
	for _, p := range req.Ports {
		for _, key := range []string{"container", "external"} {
			v := int(int64Of(p[key]))
			if v != 0 && (v < 1 || v > 65535) {
				return NewHTTPError(422, "端口需在 1-65535 之间")
			}
		}
	}
	if req.Port != nil && (*req.Port < 1 || *req.Port > 65535) {
		return NewHTTPError(422, "端口需在 1-65535 之间")
	}
	return nil
}

func setServiceEnv(svc map[string]any, key, value string) {
	switch env := svc["environment"].(type) {
	case map[string]any:
		env[key] = value
	case []any:
		prefix := key + "="
		for i, item := range env {
			if strings.HasPrefix(str(item), prefix) {
				env[i] = key + "=" + value
				return
			}
		}
		svc["environment"] = append(env, key+"="+value)
	default:
		svc["environment"] = map[string]any{key: value}
	}
}

// applyServicePort 替换 service 已声明的 external:container 端口映射。
func applyServicePort(svc map[string]any, external, container int) bool {
	target := itoa(container)
	ports, ok := svc["ports"].([]any)
	if !ok {
		return false
	}
	for i, item := range ports {
		switch p := item.(type) {
		case map[string]any:
			mapped := str(p["target"])
			if mapped == "" {
				mapped = str(p["container_port"])
			}
			if mapped == target {
				p["published"] = external
				return true
			}
		case string:
			parts := strings.Split(p, ":")
			if len(parts) >= 2 && parts[len(parts)-1] == target {
				if len(parts) == 2 {
					ports[i] = itoa(external) + ":" + itoa(container)
				} else {
					parts[len(parts)-2] = itoa(external)
					ports[i] = strings.Join(parts, ":")
				}
				return true
			}
		}
	}
	return false
}

func applyComposeOptions(composeText string, req appInstallRequest, app map[string]any) (string, error) {
	composeText = strings.ReplaceAll(composeText, "${VERSION}", req.Version)
	composeText = strings.ReplaceAll(composeText, "${TZ}", req.Timezone)

	var data map[string]any
	if err := yaml.Unmarshal([]byte(composeText), &data); err != nil {
		return "", NewHTTPError(400, "docker-compose.yml 解析失败: "+err.Error())
	}
	if data == nil {
		return "", NewHTTPError(400, "docker-compose.yml 缺少 services 定义")
	}
	servicesRaw, ok := data["services"]
	services, ok2 := servicesRaw.(map[string]any)
	if !ok || !ok2 || len(services) == 0 {
		return "", NewHTTPError(400, "docker-compose.yml 缺少 services 定义")
	}
	multi := len(services) > 1
	appPorts := []map[string]any{}
	if arr, ok := app["ports"].([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok {
				appPorts = append(appPorts, m)
			}
		}
	}
	primaryContainerPort := 0
	if len(appPorts) > 0 {
		primaryContainerPort = int(int64Of(appPorts[0]["container"]))
	}

	for svcName, raw := range services {
		svc, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		if req.ContainerName != "" {
			if multi {
				svc["container_name"] = req.ContainerName + "_" + svcName
			} else {
				svc["container_name"] = req.ContainerName
			}
		} else {
			delete(svc, "container_name")
		}
		svc["restart"] = req.Restart
		if req.Timezone != "" {
			setServiceEnv(svc, "TZ", req.Timezone)
		}
		limits := map[string]any{}
		if req.CPULimit > 0 {
			limits["cpus"] = strconv.FormatFloat(req.CPULimit, 'f', -1, 64)
		}
		if req.MemLimitMB > 0 {
			limits["memory"] = itoa(req.MemLimitMB) + "m"
		}
		if len(limits) > 0 {
			deploy, _ := svc["deploy"].(map[string]any)
			if deploy == nil {
				deploy = map[string]any{}
				svc["deploy"] = deploy
			}
			resources, _ := deploy["resources"].(map[string]any)
			if resources == nil {
				resources = map[string]any{}
				deploy["resources"] = resources
			}
			resources["limits"] = limits
		}
		if len(req.Ports) > 0 {
			for _, pm := range req.Ports {
				cPort := int(int64Of(pm["container"]))
				ePort := int(int64Of(pm["external"]))
				if cPort > 0 && ePort > 0 {
					applyServicePort(svc, ePort, cPort)
				}
			}
		} else if req.Port != nil && primaryContainerPort > 0 {
			applyServicePort(svc, *req.Port, primaryContainerPort)
		}
	}

	var buf bytes.Buffer
	enc := yaml.NewEncoder(&buf)
	enc.SetIndent(2)
	if err := enc.Encode(data); err != nil {
		return "", NewHTTPError(500, "docker-compose.yml 序列化失败: "+err.Error())
	}
	_ = enc.Close()
	return buf.String(), nil
}

// --- docker compose 执行 ---

func composeEnginePath(path string) string {
	return HostVisiblePath(path)
}

func openFirewallPort(port int, appName string) error {
	rule := map[string]any{
		"id": randomHex(8), "port": port, "protocol": "tcp", "action": "allow",
		"comment": "应用商店 " + appName, "created_at": time.Now().Format("2006-01-02T15:04:05"),
	}
	addPortRule(rule)
	data := loadFirewall()
	data["port_rules"] = append(firewallRules(data, "port_rules"), rule)
	return saveFirewall(data)
}

func composeStream(enginePath string, args []string, emit func(string)) (int, string) {
	emitLine := func(line string) {
		if strings.TrimSpace(line) != "" && emit != nil {
			emit(line)
		}
	}
	if remoteActive() {
		node := currentNode()
		bin := remoteDockerBin(node)
		if bin == "" {
			return 127, "远程节点 Docker/Podman 不可用"
		}
		full := append([]string{"compose", "-f", enginePath}, args...)
		res := remoteNodeCmd(node, 1800*time.Second, bin, full...)
		text := strings.TrimSpace(res.Stdout + res.Stderr)
		for _, line := range strings.Split(text, "\n") {
			emitLine(line)
		}
		if res.Code == 0 {
			return 0, text
		}
		return res.Code, text
	}
	bin := dockerCLI()
	if bin == "" {
		return 127, "Docker/Podman 不可用，无法安装应用"
	}
	full := append([]string{"compose", "-f", enginePath}, args...)
	cmd := exec.Command(bin, full...)
	stdout, stdoutW, err := os.Pipe()
	if err != nil {
		return 1, err.Error()
	}
	cmd.Stdout = stdoutW
	cmd.Stderr = stdoutW
	if err := cmd.Start(); err != nil {
		_ = stdout.Close()
		_ = stdoutW.Close()
		return 1, "无法启动 docker compose: " + err.Error()
	}
	_ = stdoutW.Close()
	lines := []string{}
	done := make(chan struct{})
	readerDone := make(chan struct{})
	go func() {
		sc := bufio.NewScanner(stdout)
		sc.Buffer(make([]byte, 64*1024), 1024*1024)
		for sc.Scan() {
			line := sc.Text()
			lines = append(lines, line)
			emitLine(line)
		}
		_ = stdout.Close()
		close(readerDone)
	}()
	go func() {
		select {
		case <-done:
		case <-time.After(1800 * time.Second):
			_ = cmd.Process.Kill()
		}
	}()
	err = cmd.Wait()
	close(done)
	<-readerDone
	text := strings.Join(lines, "\n")
	if err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			return ee.ExitCode(), text
		}
		return 1, text
	}
	return 0, text
}

func verifyComposeContainers(appName, containerName string) (bool, string) {
	containers, res := dockerJSONArray("ps", "-a", "--format", "json")
	if res.Code != 0 {
		return true, "无法查询容器列表（跳过验证）"
	}
	if len(containers) == 0 {
		return false, "compose up 返回成功，但未发现任何容器"
	}
	targets := map[string]bool{}
	if containerName != "" {
		targets[containerName] = true
	}
	prefixes := []string{appName + "_", appName + "-"}
	matched := []map[string]any{}
	for _, c := range containers {
		rawName := c["Names"]
		if rawName == nil {
			rawName = c["name"]
		}
		names := []string{}
		switch v := rawName.(type) {
		case []any:
			for _, item := range v {
				names = append(names, str(item))
			}
		case string:
			names = append(names, v)
		}
		hit := false
		for _, name := range names {
			if name == "" {
				continue
			}
			if targets[name] {
				hit = true
				break
			}
			for _, p := range prefixes {
				if strings.HasPrefix(name, p) {
					hit = true
					break
				}
			}
			if hit {
				break
			}
		}
		if hit {
			matched = append(matched, c)
		}
	}
	if len(matched) == 0 {
		return false, "compose up 返回成功，但未找到对应容器（镜像可能拉取失败）"
	}
	running := 0
	for _, c := range matched {
		if strings.ToLower(str(c["State"])) == "running" || strings.HasPrefix(strings.ToLower(str(c["Status"])), "up") {
			running++
		}
	}
	if running == 0 {
		return false, "容器已创建但未运行（共 " + itoa(len(matched)) + " 个，状态异常）"
	}
	return true, itoa(running) + " 个容器运行中"
}

// --- 安装流程 ---

type installEvent func(map[string]any)

func installPrepare(req appInstallRequest) (containerName, projectDir, composePath, enginePath string, warnings []string, err error) {
	warnings = []string{}
	req.normalize()
	if verr := req.validate(); verr != nil {
		return "", "", "", "", nil, verr
	}
	app, aerr := findApp(req.AppID)
	if aerr != nil {
		return "", "", "", "", nil, aerr
	}
	composeText := req.Compose
	if strings.TrimSpace(composeText) == "" {
		composeText, aerr = appComposeText(app)
		if aerr != nil {
			return "", "", "", "", nil, aerr
		}
	}
	finalCompose, cerr := applyComposeOptions(composeText, req, app)
	if cerr != nil {
		return "", "", "", "", nil, cerr
	}
	containerName = req.ContainerName
	if containerName == "" {
		containerName = "graw-" + req.AppName
	}
	projectDir = DataPath("appstore", req.AppName)
	if err = EnsureDir(projectDir); err != nil {
		return
	}
	composePath = filepath.Join(projectDir, "docker-compose.yml")
	if werr := os.WriteFile(composePath, []byte(finalCompose), 0o644); werr != nil {
		err = werr
		return
	}
	if req.ExposePort {
		opened := []int{}
		if len(req.Ports) > 0 {
			for _, pm := range req.Ports {
				if v := int(int64Of(pm["external"])); v > 0 {
					opened = append(opened, v)
				}
			}
		} else if req.Port != nil && *req.Port > 0 {
			opened = append(opened, *req.Port)
		}
		for _, p := range opened {
			if ferr := openFirewallPort(p, req.AppName); ferr != nil {
				warnings = append(warnings, "防火墙端口 "+itoa(p)+" 放行失败: "+ferr.Error())
			}
		}
	}
	enginePath = composeEnginePath(composePath)
	return
}

// installStreamWorker 执行安装并推送 status/log/error/result 事件。
func installStreamWorker(req appInstallRequest, emit installEvent, taskID string) {
	req.normalize()
	if verr := req.validate(); verr != nil {
		emit(map[string]any{"type": "error", "message": verr.Error()})
		if taskID != "" {
			updateTaskRecord(taskID, map[string]any{"status": "error", "error": verr.Error(), "finished_at": nowISO()})
		}
		return
	}
	containerName, projectDir, composePath, enginePath, warnings, err := installPrepare(req)
	if err != nil {
		emit(map[string]any{"type": "error", "message": err.Error()})
		if taskID != "" {
			updateTaskRecord(taskID, map[string]any{"status": "error", "error": err.Error(), "finished_at": nowISO()})
		}
		return
	}
	emit(map[string]any{"type": "status", "message": "已生成 docker-compose.yml，开始拉取 / 启动容器..."})
	emitLog := func(line string) {
		emit(map[string]any{"type": "log", "line": line})
	}
	pull := true
	if req.Pull != nil {
		pull = *req.Pull
	}
	if pull {
		emit(map[string]any{"type": "status", "message": "正在拉取镜像（docker compose pull）..."})
		rc, out := composeStream(enginePath, []string{"pull"}, emitLog)
		if rc != 0 {
			tail := out
			if len(tail) > 2000 {
				tail = tail[len(tail)-2000:]
			}
			warnings = append(warnings, "镜像拉取可能失败（继续尝试启动）:\n"+tail)
		}
		emit(map[string]any{"type": "status", "message": "镜像拉取完成，正在启动容器..."})
	} else {
		emit(map[string]any{"type": "status", "message": "已跳过拉取镜像，正在启动容器..."})
	}
	rc, upOutput := composeStream(enginePath, []string{"up", "-d", "--remove-orphans"}, emitLog)
	if rc != 0 {
		msg := "docker compose up 失败\n\n" + tailText(upOutput, 4000) + "\n\n项目目录: " + composePath
		emit(map[string]any{"type": "error", "message": msg})
		if taskID != "" {
			updateTaskRecord(taskID, map[string]any{"status": "error", "error": msg, "finished_at": nowISO()})
		}
		return
	}
	ok, detail := verifyComposeContainers(req.AppName, containerName)
	if !ok {
		msg := "安装未成功：" + detail + "\n\n" + tailText(upOutput, 4000) + "\n\n项目目录: " + composePath
		emit(map[string]any{"type": "error", "message": msg})
		if taskID != "" {
			updateTaskRecord(taskID, map[string]any{"status": "error", "error": msg, "finished_at": nowISO()})
		}
		return
	}
	emit(map[string]any{"type": "status", "message": "容器验证通过（" + detail + "）"})
	result := map[string]any{
		"ok": true, "app_id": req.AppID, "app_name": req.AppName,
		"container_name": containerName, "version": req.Version,
		"project_dir": projectDir, "compose_file": composePath,
		"port": req.Port, "expose_port": req.ExposePort, "restart": req.Restart,
		"output": tailText(upOutput, 4000), "warnings": warnings,
	}
	if result["output"] == "" {
		result["output"] = "(无输出)"
	}
	emit(map[string]any{"type": "result", "data": result})
	if taskID != "" {
		updateTaskRecord(taskID, map[string]any{"status": "completed", "result": result, "finished_at": nowISO()})
	}
}

func tailText(text string, max int) string {
	text = strings.TrimSpace(text)
	if len(text) > max {
		return text[len(text)-max:]
	}
	return text
}

func installAppSync(req appInstallRequest) (map[string]any, error) {
	req.normalize()
	var result map[string]any
	var outErr error
	installStreamWorker(req, func(evt map[string]any) {
		switch str(evt["type"]) {
		case "result":
			result = mapOf(evt["data"])
		case "error":
			outErr = NewHTTPError(500, str(evt["message"]))
		}
	}, "")
	if outErr != nil {
		return nil, outErr
	}
	if result == nil {
		return nil, NewHTTPError(500, "安装失败")
	}
	return result, nil
}

// --- 任务中心 ---

var taskIDRe = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)

func nowISO() string {
	return time.Now().Format("2006-01-02T15:04:05")
}

func loadTaskRecords() []map[string]any {
	tasks := []map[string]any{}
	_ = ReadJSON(DataPath("tasks.json"), &tasks)
	if tasks == nil {
		tasks = []map[string]any{}
	}
	sort.SliceStable(tasks, func(i, j int) bool {
		return str(tasks[i]["started_at"]) > str(tasks[j]["started_at"])
	})
	return tasks
}

func saveTaskRecords(tasks []map[string]any) error {
	return WriteJSONFile(DataPath("tasks.json"), tasks)
}

func createTaskRecord(record map[string]any) map[string]any {
	id := str(record["id"])
	if id == "" {
		id = randomHex(8)
	}
	record["id"] = id
	if _, ok := record["status"]; !ok {
		record["status"] = "running"
	}
	if _, ok := record["started_at"]; !ok {
		record["started_at"] = nowISO()
	}
	if _, ok := record["finished_at"]; !ok {
		record["finished_at"] = ""
	}
	if _, ok := record["result"]; !ok {
		record["result"] = nil
	}
	if _, ok := record["error"]; !ok {
		record["error"] = ""
	}
	tasks := loadTaskRecords()
	tasks = append(tasks, record)
	_ = saveTaskRecords(tasks)
	return record
}

func updateTaskRecord(id string, fields map[string]any) {
	tasks := loadTaskRecords()
	for _, t := range tasks {
		if str(t["id"]) != id {
			continue
		}
		for k, v := range fields {
			t[k] = v
		}
	}
	_ = saveTaskRecords(tasks)
}

func taskLogPath(id string) string {
	return DataPath("tasks", id+".log")
}

// appendTaskLog 把安装事件（status/log/error/result）写入任务 JSONL 日志。
func appendTaskLog(id string, evt map[string]any) {
	etype := str(evt["type"])
	text := ""
	switch etype {
	case "status":
		text = str(evt["message"])
	case "log":
		text = str(evt["line"])
	case "error":
		text = str(evt["message"])
	case "result":
		b, _ := json.Marshal(evt["data"])
		text = string(b)
	default:
		return
	}
	if text == "" {
		return
	}
	_ = os.MkdirAll(DataPath("tasks"), 0o700)
	f, err := os.OpenFile(taskLogPath(id), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return
	}
	defer f.Close()
	b, _ := json.Marshal(map[string]any{"type": etype, "text": text})
	_, _ = f.Write(append(b, '\n'))
}

func readTaskLog(id string) []map[string]any {
	lines := []map[string]any{}
	f, err := os.Open(taskLogPath(id))
	if err != nil {
		return lines
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 64*1024), 4*1024*1024)
	for sc.Scan() {
		raw := strings.TrimSpace(sc.Text())
		if raw == "" {
			continue
		}
		var obj map[string]any
		if json.Unmarshal([]byte(raw), &obj) == nil {
			lines = append(lines, map[string]any{
				"type": defaultString(str(obj["type"]), "log"),
				"text": str(obj["text"]),
			})
		} else {
			lines = append(lines, map[string]any{"type": "log", "text": raw})
		}
	}
	return lines
}

func deleteTaskRecord(id string) {
	tasks := loadTaskRecords()
	kept := []map[string]any{}
	for _, t := range tasks {
		if str(t["id"]) != id {
			kept = append(kept, t)
		}
	}
	_ = saveTaskRecords(kept)
	_ = os.Remove(taskLogPath(id))
}

func getTask(id string) map[string]any {
	for _, t := range loadTaskRecords() {
		if str(t["id"]) == id {
			return t
		}
	}
	return nil
}

// --- 路由 ---

func registerAppStoreRoutes(a *App) {
	a.Get("/api/appstore/config", perm("appstore"), func(w http.ResponseWriter, r *http.Request) error {
		cfg := loadAppStoreConfig()
		indexURL := strings.TrimSpace(str(cfg["index_url"]))
		if indexURL == "" {
			indexURL = defaultAppIndexURL
		}
		return WriteJSON(w, 200, map[string]any{
			"index_url": indexURL, "configured": strings.TrimSpace(str(cfg["index_url"])) != "",
			"default_url": defaultAppIndexURL,
		})
	})
	a.Put("/api/appstore/config", perm("appstore"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			IndexURL string `json:"index_url"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		raw := strings.TrimSpace(req.IndexURL)
		if len(raw) > 2048 {
			return NewHTTPError(422, "index_url 过长")
		}
		if raw != "" {
			u, err := url.Parse(raw)
			if err != nil || strings.ToLower(u.Scheme) != "http" && strings.ToLower(u.Scheme) != "https" {
				return NewHTTPError(400, "仅支持 http/https 地址")
			}
		}
		cfg := loadAppStoreConfig()
		cfg["index_url"] = raw
		if err := saveAppStoreConfig(cfg); err != nil {
			return err
		}
		clearAppIndexCache()
		return WriteJSON(w, 200, map[string]any{"ok": true, "index_url": raw})
	})
	a.Get("/api/appstore/index", perm("appstore"), func(w http.ResponseWriter, r *http.Request) error {
		refresh := strings.EqualFold(r.URL.Query().Get("refresh"), "1") ||
			strings.EqualFold(r.URL.Query().Get("refresh"), "true")
		source, data, errText, err := loadAppIndex(refresh)
		if err != nil {
			return err
		}
		apps := appList(data)
		for _, app := range apps {
			app["icon"] = "/api/appstore/icons/" + str(app["id"])
		}
		store := mapOf(data["store"])
		return WriteJSON(w, 200, map[string]any{
			"source": source, "error": errText, "updated_at": store["updated_at"],
			"store": store, "apps": apps,
		})
	})
	a.Get("/api/appstore/icons/{app_id}", public(), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "app_id")
		if !appIDRe.MatchString(id) {
			return NewHTTPError(400, "非法应用 ID")
		}
		dir := appStoreDir()
		if dir == "" {
			return NewHTTPError(404, "图标不存在: "+id)
		}
		for _, item := range []struct{ name, mime string }{
			{"icon.png", "image/png"}, {"icon.svg", "image/svg+xml"},
		} {
			p := filepath.Join(dir, "apps", id, item.name)
			if Exists(p) {
				w.Header().Set("Content-Type", item.mime)
				http.ServeFile(w, r, p)
				return nil
			}
		}
		return NewHTTPError(404, "图标不存在: "+id)
	})
	a.Get("/api/appstore/app/{app_id}/compose", perm("appstore"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "app_id")
		app, err := findApp(id)
		if err != nil {
			return err
		}
		compose, err := appComposeText(app)
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{
			"app_id": id, "name": defaultString(str(app["name"]), id), "compose": compose,
		})
	})
	a.Get("/api/appstore/app/{app_id}/readme", perm("appstore"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "app_id")
		app, err := findApp(id)
		if err != nil {
			return err
		}
		repo, readme, err := appReadme(app)
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{
			"app_id": id, "name": defaultString(str(app["name"]), id),
			"repo": repo, "source": str(app["source"]), "readme": readme,
		})
	})
	a.Post("/api/appstore/install", perm("appstore"), func(w http.ResponseWriter, r *http.Request) error {
		var req appInstallRequest
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if req.AppName == "" {
			req.AppName = req.AppID
		}
		res, err := installAppSync(req)
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, res)
	})
	a.Post("/api/appstore/install/stream", perm("appstore"), func(w http.ResponseWriter, r *http.Request) error {
		var req appInstallRequest
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if req.AppName == "" {
			req.AppName = req.AppID
		}
		req.normalize()
		if err := req.validate(); err != nil {
			return err
		}
		flusher, _ := w.(http.Flusher)
		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("X-Accel-Buffering", "no")
		w.Header().Set("Connection", "keep-alive")
		w.WriteHeader(200)
		taskID := randomHex(8)
		events := make(chan map[string]any, 256)
		createTaskRecord(map[string]any{
			"id": taskID, "type": "appstore-install", "status": "running",
			"app_id": req.AppID, "app_name": req.AppName, "version": req.Version,
			"title": "安装「" + req.AppName + "」",
		})
		go func() {
			defer close(events)
			installStreamWorker(req, func(evt map[string]any) {
				appendTaskLog(taskID, evt)
				events <- evt
			}, taskID)
		}()
		writeEvent := func(payload map[string]any) {
			b, _ := json.Marshal(payload)
			fmt.Fprintf(w, "data: %s\n\n", string(b))
			if flusher != nil {
				flusher.Flush()
			}
		}
		writeEvent(map[string]any{"type": "task_id", "task_id": taskID})
		ticker := time.NewTicker(500 * time.Millisecond)
		defer ticker.Stop()
		ctx := r.Context()
		for {
			select {
			case evt, ok := <-events:
				if !ok {
					return nil
				}
				writeEvent(evt)
			case <-ctx.Done():
				return nil
			case <-ticker.C:
				if ctx.Err() != nil {
					return nil
				}
			}
		}
	})

	// 任务中心
	a.Get("/api/tasks", perm("appstore"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"tasks": loadTaskRecords()})
	})
	a.Get("/api/tasks/{task_id}", perm("appstore"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "task_id")
		if !taskIDRe.MatchString(id) {
			return NewHTTPError(400, "非法的任务 ID")
		}
		task := getTask(id)
		if task == nil {
			return NewHTTPError(404, "任务不存在")
		}
		return WriteJSON(w, 200, task)
	})
	a.Get("/api/tasks/{task_id}/log", perm("appstore"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "task_id")
		if !taskIDRe.MatchString(id) {
			return NewHTTPError(400, "非法的任务 ID")
		}
		if getTask(id) == nil {
			return NewHTTPError(404, "任务不存在")
		}
		lines := readTaskLog(id)
		text := []string{}
		for _, l := range lines {
			text = append(text, str(l["text"]))
		}
		return WriteJSON(w, 200, map[string]any{"task_id": id, "lines": lines, "text": strings.Join(text, "\n")})
	})
	a.Delete("/api/tasks/{task_id}", perm("appstore"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "task_id")
		if !taskIDRe.MatchString(id) {
			return NewHTTPError(400, "非法的任务 ID")
		}
		if getTask(id) == nil {
			return NewHTTPError(404, "任务不存在")
		}
		deleteTaskRecord(id)
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})

	registerRuntimeRoutes(a)
}
