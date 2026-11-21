package graw

import (
	"net/http"
	"regexp"
	"strings"
	"time"
)

// 虚拟机管理（P2，可选扩展）：只做 libvirt/virsh 的「发现 + 日常启停 + 详情」。
// 不创建/克隆虚拟机（涉及磁盘镜像、网络与 ISO 选择，更适合独立插件或模板化流程）。
//
// 依赖宿主机已安装 libvirt 与 virsh；未安装时返回 available:false，前端显示引导。

var vmNameRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`)

func vmNameFromPath(r *http.Request) (string, error) {
	name := strings.TrimSpace(PathParam(r, "vm_name"))
	if !vmNameRe.MatchString(name) {
		return "", NewHTTPError(400, "虚拟机名称非法")
	}
	return name, nil
}

func virshAvailable() (bool, string) {
	if HostWhich("virsh") == "" {
		return false, "未检测到 virsh（请先安装 libvirt-client）"
	}
	res := HostCmd(10*time.Second, "virsh", "list", "--all", "--name")
	if res.Code != 0 {
		msg := strings.TrimSpace(res.Stderr)
		if msg == "" {
			msg = "无法连接 libvirt（请确认 libvirtd 已启动）"
		}
		return false, msg
	}
	return true, ""
}

func parseVirshInfo(text string) map[string]any {
	out := map[string]any{}
	for _, line := range strings.Split(text, "\n") {
		parts := strings.SplitN(line, ":", 2)
		if len(parts) != 2 {
			continue
		}
		key := strings.TrimSpace(parts[0])
		val := strings.TrimSpace(parts[1])
		switch key {
		case "Name":
			out["name"] = val
		case "UUID":
			out["uuid"] = val
		case "State":
			out["state"] = val
		case "CPU(s)":
			out["vcpus"] = int64Of(val)
		case "Max memory":
			// 形如 2097152 KiB
			fields := strings.Fields(val)
			if len(fields) > 0 {
				out["memory_kb"] = int64Of(fields[0])
			}
		case "Autostart":
			out["autostart"] = strings.EqualFold(val, "enable")
		case "Persistent":
			out["persistent"] = strings.EqualFold(val, "yes")
		}
	}
	return out
}

func listVMs() map[string]any {
	ok, reason := virshAvailable()
	if !ok {
		return map[string]any{"available": false, "reason": reason, "vms": []any{}}
	}
	res := HostCmd(15*time.Second, "virsh", "list", "--all", "--name")
	names := []string{}
	for _, line := range strings.Split(strings.TrimSpace(res.Stdout), "\n") {
		name := strings.TrimSpace(line)
		if name != "" && vmNameRe.MatchString(name) {
			names = append(names, name)
		}
	}
	sortStrings(names)
	vms := []map[string]any{}
	for _, name := range names {
		info := HostCmd(10*time.Second, "virsh", "dominfo", name)
		item := parseVirshInfo(info.Stdout)
		item["name"] = name
		if _, ok := item["state"]; !ok {
			item["state"] = "unknown"
		}
		vms = append(vms, item)
	}
	return map[string]any{"available": true, "vms": vms}
}

func vmDetail(name string) (map[string]any, error) {
	info := HostCmd(10*time.Second, "virsh", "dominfo", name)
	if info.Code != 0 {
		return nil, NewHTTPError(404, "虚拟机不存在或 libvirt 不可用")
	}
	detail := parseVirshInfo(info.Stdout)
	detail["name"] = name
	xml := HostCmd(10*time.Second, "virsh", "dumpxml", name)
	if xml.Code == 0 {
		text := xml.Stdout
		if len(text) > 128*1024 {
			text = text[:128*1024]
		}
		detail["xml"] = text
	}
	vnc := HostCmd(10*time.Second, "virsh", "vncdisplay", name)
	if vnc.Code == 0 {
		detail["vnc"] = strings.TrimSpace(vnc.Stdout)
	}
	return detail, nil
}

var vmActions = map[string][]string{
	"start":    {"start"},
	"shutdown": {"shutdown"},
	"reboot":   {"reboot"},
	"destroy":  {"destroy"},
	"suspend":  {"suspend"},
	"resume":   {"resume"},
}

func vmAction(name, action string) (map[string]any, error) {
	action = strings.TrimSpace(action)
	var args []string
	switch action {
	case "autostart_on":
		args = []string{"autostart", name}
	case "autostart_off":
		args = []string{"autostart", "--disable", name}
	default:
		base, ok := vmActions[action]
		if !ok {
			return nil, NewHTTPError(400, "不支持的动作: "+action)
		}
		args = append(base, name)
	}
	res := HostCmd(120*time.Second, "virsh", args...)
	out := strings.TrimSpace(res.Stdout + res.Stderr)
	if res.Code != 0 {
		return nil, NewHTTPError(500, "虚拟机操作失败: "+tailText(out, 2000))
	}
	return map[string]any{"ok": true, "output": tailText(out, 1000)}, nil
}

func registerVMRoutes(a *App) {
	a.Get("/api/vms/status", perm("vms"), func(w http.ResponseWriter, r *http.Request) error {
		ok, reason := virshAvailable()
		version := ""
		if ok {
			res := HostCmd(10*time.Second, "virsh", "--version")
			version = strings.TrimSpace(res.Stdout)
		}
		return WriteJSON(w, 200, map[string]any{
			"available": ok, "reason": reason, "version": version,
		})
	})
	a.Get("/api/vms", perm("vms"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, listVMs())
	})
	a.Get("/api/vms/{vm_name}", perm("vms"), func(w http.ResponseWriter, r *http.Request) error {
		name, err := vmNameFromPath(r)
		if err != nil {
			return err
		}
		detail, derr := vmDetail(name)
		if derr != nil {
			return derr
		}
		return WriteJSON(w, 200, map[string]any{"vm": detail})
	})
	a.Post("/api/vms/{vm_name}/action", perm("vms"), func(w http.ResponseWriter, r *http.Request) error {
		name, err := vmNameFromPath(r)
		if err != nil {
			return err
		}
		var req struct {
			Action string `json:"action"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		res, aerr := vmAction(name, req.Action)
		if aerr != nil {
			return aerr
		}
		AuditRecord("虚拟机操作", CurrentUser(r).Username(), ClientIP(r), name+" "+req.Action)
		return WriteJSON(w, 200, res)
	})
}
