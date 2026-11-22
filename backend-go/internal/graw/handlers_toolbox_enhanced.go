package graw

import (
	"net"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// 系统工具箱增强（P1）：缓存清理、系统快速设置、Fail2ban。
//
// 所有操作都作用于「当前节点」的宿主机：请求经 Agent 代理后在子节点执行，
// 因此子节点需要 root 权限（容器部署由 privileged + /host 提供）。
// 写系统文件前统一备份为 <file>.graw.bak，避免改错无法恢复。

var hostnameRe = regexp.MustCompile(`^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$`)
var timezoneRe = regexp.MustCompile(`^[A-Za-z0-9_+\-/]{1,64}$`)
var jailNameRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`)
var cacheTargets = map[string]bool{"apt": true, "yum": true, "dnf": true, "journal": true, "tmp": true}

func toolboxSystemInfo() map[string]any {
	hostname := strings.TrimSpace(HostCmd(10*time.Second, "hostname").Stdout)
	tz := strings.TrimSpace(readHostText("/etc/timezone"))
	if tz == "" {
		tz = strings.TrimSpace(HostCmd(10*time.Second, "timedatectl", "show", "-p", "Timezone", "--value").Stdout)
	}
	dns := []string{}
	for _, line := range strings.Split(readHostText("/etc/resolv.conf"), "\n") {
		fields := strings.Fields(line)
		if len(fields) >= 2 && fields[0] == "nameserver" {
			dns = append(dns, fields[1])
		}
	}
	hosts := readHostText("/etc/hosts")
	if len(hosts) > 64*1024 {
		hosts = hosts[:64*1024]
	}
	swap := strings.TrimSpace(HostCmd(10*time.Second, "swapon", "--show", "--noheadings").Stdout)
	ntp := strings.TrimSpace(HostCmd(10*time.Second, "timedatectl", "show", "-p", "NTP", "--value").Stdout)
	ntpService := ""
	for _, svc := range []string{"systemd-timesyncd", "chronyd", "ntpd"} {
		res := HostCmd(5*time.Second, "systemctl", "is-active", svc)
		if strings.TrimSpace(res.Stdout) == "active" {
			ntpService = svc
			break
		}
	}
	return map[string]any{
		"hostname": hostname, "timezone": tz, "dns": dns, "hosts": hosts,
		"swap": swap, "ntp": ntp, "ntp_service": ntpService,
	}
}

func toolboxCacheClean(targets []string) (map[string]any, error) {
	out := map[string]any{}
	ran := false
	for _, target := range targets {
		target = strings.TrimSpace(target)
		if !cacheTargets[target] {
			return nil, NewHTTPError(400, "不支持的清理目标: "+target)
		}
		var res CmdResult
		switch target {
		case "apt":
			res = HostCmd(300*time.Second, "apt-get", "clean")
		case "yum":
			res = HostCmd(300*time.Second, "yum", "clean", "all")
		case "dnf":
			res = HostCmd(300*time.Second, "dnf", "clean", "all")
		case "journal":
			res = HostCmd(300*time.Second, "journalctl", "--vacuum-size=200M")
		case "tmp":
			res = HostCmd(300*time.Second, "systemd-tmpfiles", "--clean")
		}
		text := strings.TrimSpace(res.Stdout + res.Stderr)
		out[target] = map[string]any{"ok": res.Code == 0, "output": tailText(text, 2000)}
		ran = true
	}
	if !ran {
		return nil, NewHTTPError(400, "请至少选择一个清理目标")
	}
	return out, nil
}

// toolboxSystemUpdate 执行系统快速设置；value 的含义随 action 变化。
func toolboxSystemUpdate(action, value string) (map[string]any, error) {
	action = strings.TrimSpace(action)
	switch action {
	case "hostname":
		v := strings.TrimSpace(value)
		if !hostnameRe.MatchString(v) {
			return nil, NewHTTPError(400, "主机名只能包含字母/数字/-，且长度不超过 63")
		}
		res := HostCmd(30*time.Second, "hostnamectl", "set-hostname", v)
		if res.Code != 0 {
			return nil, NewHTTPError(500, "设置主机名失败: "+strings.TrimSpace(res.Stderr))
		}
	case "timezone":
		v := strings.TrimSpace(value)
		if !timezoneRe.MatchString(v) || strings.Contains(v, "..") {
			return nil, NewHTTPError(400, "时区格式非法")
		}
		if !Exists(HostPath("/usr/share/zoneinfo/" + v)) {
			return nil, NewHTTPError(400, "时区不存在: "+v)
		}
		res := HostCmd(30*time.Second, "timedatectl", "set-timezone", v)
		if res.Code != 0 {
			return nil, NewHTTPError(500, "设置时区失败: "+strings.TrimSpace(res.Stderr))
		}
	case "dns":
		ips := []string{}
		for _, line := range strings.Split(value, "\n") {
			line = strings.TrimSpace(line)
			if line == "" {
				continue
			}
			if net.ParseIP(line) == nil {
				return nil, NewHTTPError(400, "DNS 地址非法: "+line)
			}
			ips = append(ips, line)
		}
		if len(ips) == 0 {
			return nil, NewHTTPError(400, "DNS 列表不能为空")
		}
		content := "# Managed by Graw\n"
		for _, ip := range ips {
			content += "nameserver " + ip + "\n"
		}
		if err := writeHostFileWithBackup("/etc/resolv.conf", content); err != nil {
			return nil, err
		}
	case "hosts":
		if len(value) > 64*1024 || strings.ContainsRune(value, 0) {
			return nil, NewHTTPError(400, "/etc/hosts 内容非法或过大")
		}
		if err := writeHostFileWithBackup("/etc/hosts", value); err != nil {
			return nil, err
		}
	case "ntp":
		on := strings.EqualFold(strings.TrimSpace(value), "true") || value == "1"
		arg := "false"
		if on {
			arg = "true"
		}
		res := HostCmd(30*time.Second, "timedatectl", "set-ntp", arg)
		if res.Code != 0 {
			return nil, NewHTTPError(500, "设置 NTP 失败: "+strings.TrimSpace(res.Stderr))
		}
	case "swap":
		// value 来自 JSON 字符串字段，必须按字符串解析；此前用 int64Of(value)
		// 会把任意输入都当成 0，导致创建 Swap 永远返回「大小非法」。
		sizeMB, aerr := strconv.Atoi(strings.TrimSpace(value))
		if aerr != nil || sizeMB < 128 || sizeMB > 65536 {
			return nil, NewHTTPError(400, "Swap 大小需在 128-65536 MB 之间")
		}
		script := "set -e\n" +
			"test ! -e /swapfile-graw || (swapoff /swapfile-graw 2>/dev/null || true)\n" +
			"fallocate -l " + strconv.Itoa(sizeMB) + "M /swapfile-graw\n" +
			"chmod 600 /swapfile-graw\n" +
			"mkswap /swapfile-graw >/dev/null\n" +
			"swapon /swapfile-graw\n" +
			"grep -q '/swapfile-graw' /etc/fstab || echo '/swapfile-graw none swap sw 0 0' >> /etc/fstab\n"
		res := HostShell(120*time.Second, script)
		if res.Code != 0 {
			return nil, NewHTTPError(500, "创建 Swap 失败: "+strings.TrimSpace(res.Stderr))
		}
	case "swapoff":
		res := HostShell(60*time.Second, "swapoff /swapfile-graw 2>/dev/null || true; sed -i '\\#/swapfile-graw#d' /etc/fstab")
		if res.Code != 0 {
			return nil, NewHTTPError(500, "关闭 Swap 失败: "+strings.TrimSpace(res.Stderr))
		}
	default:
		return nil, NewHTTPError(400, "不支持的系统设置: "+action)
	}
	return toolboxSystemInfo(), nil
}

func writeHostFileWithBackup(path, content string) error {
	real := HostPath(path)
	if Exists(real) {
		old := readHostText(path)
		_ = writeHostText(path+".graw.bak", old)
	}
	if err := writeHostText(path, content); err != nil {
		return NewHTTPError(500, "写入 "+path+" 失败: "+err.Error())
	}
	return nil
}

// --- Fail2ban ---

func fail2banStatus() map[string]any {
	client := HostWhich("fail2ban-client")
	if client == "" {
		return map[string]any{"installed": false, "running": false, "jails": []any{}}
	}
	active := strings.TrimSpace(HostCmd(10*time.Second, "systemctl", "is-active", "fail2ban").Stdout)
	res := HostCmd(15*time.Second, "fail2ban-client", "status")
	running := res.Code == 0
	jails := []map[string]any{}
	if running {
		jailList := ""
		for _, line := range strings.Split(res.Stdout, "\n") {
			if strings.Contains(line, "Jail list:") {
				jailList = strings.TrimSpace(strings.SplitN(line, ":", 2)[1])
			}
		}
		for _, jail := range strings.Split(jailList, ",") {
			jail = strings.TrimSpace(jail)
			if jail == "" {
				continue
			}
			jres := HostCmd(15*time.Second, "fail2ban-client", "status", jail)
			item := map[string]any{"name": jail, "raw": strings.TrimSpace(jres.Stdout)}
			banned := []string{}
			for _, line := range strings.Split(jres.Stdout, "\n") {
				if strings.Contains(line, "Banned IP list:") {
					for _, ip := range strings.Fields(strings.SplitN(line, ":", 2)[1]) {
						banned = append(banned, ip)
					}
				}
			}
			item["banned"] = banned
			jails = append(jails, item)
		}
	}
	return map[string]any{"installed": true, "running": running, "active": active, "jails": jails}
}

func fail2banAction(action, jail, ip string) (map[string]any, error) {
	if HostWhich("fail2ban-client") == "" {
		return nil, NewHTTPError(400, "未安装 fail2ban-client")
	}
	action = strings.TrimSpace(action)
	jail = strings.TrimSpace(jail)
	ip = strings.TrimSpace(ip)
	var res CmdResult
	switch action {
	case "start", "stop", "restart":
		res = HostCmd(60*time.Second, "systemctl", action, "fail2ban")
	case "ban", "unban":
		if jail == "" || !jailNameRe.MatchString(jail) {
			return nil, NewHTTPError(400, "Jail 名称非法")
		}
		if net.ParseIP(ip) == nil {
			return nil, NewHTTPError(400, "IP 地址非法")
		}
		verb := "banip"
		if action == "unban" {
			verb = "unbanip"
		}
		res = HostCmd(30*time.Second, "fail2ban-client", "set", jail, verb, ip)
	default:
		return nil, NewHTTPError(400, "不支持的动作: "+action)
	}
	out := strings.TrimSpace(res.Stdout + res.Stderr)
	if res.Code != 0 {
		return nil, NewHTTPError(500, "fail2ban 操作失败: "+out)
	}
	return map[string]any{"ok": true, "output": out, "status": fail2banStatus()}, nil
}

func registerToolboxEnhancedRoutes(a *App) {
	a.Get("/api/toolbox/system", perm("toolbox"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"system": toolboxSystemInfo()})
	})
	a.Post("/api/toolbox/system", perm("toolbox"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Action string `json:"action"`
			Value  string `json:"value"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		info, err := toolboxSystemUpdate(req.Action, req.Value)
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "system": info})
	})
	a.Post("/api/toolbox/cache/clean", perm("toolbox"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Targets []string `json:"targets"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		res, err := toolboxCacheClean(req.Targets)
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"results": res})
	})
	a.Get("/api/toolbox/fail2ban", perm("toolbox"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, fail2banStatus())
	})
	a.Post("/api/toolbox/fail2ban", perm("toolbox"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Action string `json:"action"`
			Jail   string `json:"jail"`
			IP     string `json:"ip"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		res, err := fail2banAction(req.Action, req.Jail, req.IP)
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, res)
	})
}
