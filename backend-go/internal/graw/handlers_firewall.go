package graw

import (
	"net"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// 防火墙管理：Linux iptables / Windows netsh advfirewall；规则元数据存 data/firewall.json。
var blockProtectedPorts = map[int]bool{22: true, 80: true, 443: true, 8000: true}

func loadFirewall() map[string]any {
	var data map[string]any
	if err := ReadJSON(DataPath("firewall.json"), &data); err != nil || data == nil {
		return map[string]any{"enabled": true, "port_rules": []any{}, "ip_rules": []any{}}
	}
	if _, ok := data["port_rules"]; !ok {
		data["port_rules"] = []any{}
	}
	if _, ok := data["ip_rules"]; !ok {
		data["ip_rules"] = []any{}
	}
	return data
}

func saveFirewall(data map[string]any) error {
	CaptureSnapshot("firewall", "firewall", DataPath("firewall.json"))
	return WriteJSONFile(DataPath("firewall.json"), data)
}

func firewallRules(data map[string]any, key string) []map[string]any {
	out := []map[string]any{}
	if arr, ok := data[key].([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok {
				out = append(out, m)
			}
		}
	}
	return out
}

func validateIPValue(value string) *HTTPError {
	if value == "" {
		return NewHTTPError(400, "IP 或 CIDR 格式非法")
	}
	if net.ParseIP(value) != nil {
		return nil
	}
	if _, _, err := net.ParseCIDR(value); err == nil {
		return nil
	}
	return NewHTTPError(400, "IP 或 CIDR 格式非法: "+value)
}

func iptablesCmd(args ...string) (int, string, string) {
	all := append([]string{"iptables"}, args...)
	res := HostCmd(10*time.Second, all[0], all[1:]...)
	return res.Code, res.Stdout, res.Stderr
}

func netshCmd(args ...string) (int, string, string) {
	all := append([]string{"netsh", "advfirewall", "firewall"}, args...)
	res := HostCmd(10*time.Second, all[0], all[1:]...)
	return res.Code, res.Stdout, res.Stderr
}

func flushPort(port int, proto string) {
	if windowsTarget() {
		return
	}
	dport := itoa(port)
	rules := [][]string{
		{"-t", "mangle", "-D", "PREROUTING", "-p", proto, "--dport", dport, "-j", "ACCEPT"},
		{"-t", "mangle", "-D", "PREROUTING", "-p", proto, "--dport", dport, "-j", "DROP"},
		{"-D", "INPUT", "-p", proto, "--dport", dport, "-j", "ACCEPT"},
		{"-D", "INPUT", "-p", proto, "--dport", dport, "-j", "DROP"},
	}
	for _, rule := range rules {
		iptablesCmd(rule...)
	}
}

func addPortRule(rule map[string]any) {
	port := int(int64Of(rule["port"]))
	proto := str(rule["protocol"])
	if proto == "" {
		proto = "tcp"
	}
	action := "accept"
	if str(rule["action"]) == "deny" {
		action = "block"
	}
	name := "graw_port_" + str(rule["id"])
	flushPort(port, proto)
	if windowsTarget() {
		netshCmd("add", "rule", "name="+name, "dir=in", "action="+action,
			"protocol="+proto, "localport="+itoa(port))
		return
	}
	if action == "accept" {
		iptablesCmd("-I", "INPUT", "-p", proto, "--dport", itoa(port), "-j", "ACCEPT")
		iptablesCmd("-t", "mangle", "-I", "PREROUTING", "-p", proto, "--dport", itoa(port), "-j", "ACCEPT")
	} else {
		iptablesCmd("-I", "INPUT", "-p", proto, "--dport", itoa(port), "-j", "DROP")
		iptablesCmd("-t", "mangle", "-I", "PREROUTING", "-p", proto, "--dport", itoa(port), "-j", "DROP")
	}
}

func delPortRule(rule map[string]any) {
	if windowsTarget() {
		netshCmd("delete", "rule", "name=graw_port_"+str(rule["id"]))
		return
	}
	proto := str(rule["protocol"])
	if proto == "" {
		proto = "tcp"
	}
	flushPort(int(int64Of(rule["port"])), proto)
}

func addIPRule(rule map[string]any) {
	ip := str(rule["ip"])
	action := "accept"
	if str(rule["action"]) == "deny" {
		action = "block"
	}
	if windowsTarget() {
		netshCmd("add", "rule", "name=graw_ip_"+str(rule["id"]), "dir=in",
			"action="+action, "remoteip="+ip)
		return
	}
	act := "ACCEPT"
	if action == "block" {
		act = "DROP"
	}
	iptablesCmd("-A", "INPUT", "-s", ip, "-j", act)
}

func delIPRule(rule map[string]any) {
	ip := str(rule["ip"])
	if windowsTarget() {
		netshCmd("delete", "rule", "name=graw_ip_"+str(rule["id"]))
		return
	}
	iptablesCmd("-D", "INPUT", "-s", ip, "-j", "ACCEPT")
	iptablesCmd("-D", "INPUT", "-s", ip, "-j", "DROP")
}

func listeningTCPPorts() map[int]bool {
	ports := map[int]bool{}
	for _, cmdline := range [][]string{{"ss", "-tln"}, {"netstat", "-tln"}} {
		res := HostCmd(10*time.Second, cmdline[0], cmdline[1:]...)
		if res.Code != 0 || strings.TrimSpace(res.Stdout) == "" {
			continue
		}
		for _, line := range strings.Split(res.Stdout, "\n") {
			fields := strings.Fields(line)
			if len(fields) < 4 || fields[0] != "LISTEN" {
				continue
			}
			local := fields[3]
			idx := strings.LastIndex(local, ":")
			if idx < 0 {
				continue
			}
			if p, err := strconv.Atoi(local[idx+1:]); err == nil {
				ports[p] = true
			}
		}
		if len(ports) > 0 {
			return ports
		}
	}
	return ports
}

func registerFirewallRoutes(a *App) {
	a.Get("/api/firewall/status", perm("firewall"), func(w http.ResponseWriter, r *http.Request) error {
		data := loadFirewall()
		platform := "linux"
		if windowsTarget() {
			platform = "windows"
		}
		return WriteJSON(w, 200, map[string]any{"enabled": boolOf(data["enabled"]), "platform": platform})
	})
	a.Get("/api/firewall/rules", perm("firewall"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, loadFirewall())
	})
	a.Post("/api/firewall/port", perm("firewall"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Port     int    `json:"port"`
			Protocol string `json:"protocol"`
			Action   string `json:"action"`
			Comment  string `json:"comment"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if req.Port < 1 || req.Port > 65535 {
			return NewHTTPError(422, "端口需在 1-65535 之间")
		}
		if req.Protocol == "" {
			req.Protocol = "tcp"
		}
		if req.Protocol != "tcp" && req.Protocol != "udp" {
			return NewHTTPError(422, "协议仅支持 tcp/udp")
		}
		if req.Action != "allow" && req.Action != "deny" {
			req.Action = "allow"
		}
		rule := map[string]any{
			"id": randomHex(8), "port": req.Port, "protocol": req.Protocol,
			"action": req.Action, "comment": req.Comment,
			"created_at": time.Now().Format("2006-01-02T15:04:05"),
		}
		addPortRule(rule)
		data := loadFirewall()
		data["port_rules"] = append(firewallRules(data, "port_rules"), rule)
		if err := saveFirewall(data); err != nil {
			return err
		}
		return WriteJSON(w, 200, rule)
	})
	a.Delete("/api/firewall/port/{rule_id}", perm("firewall"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "rule_id")
		data := loadFirewall()
		rules := firewallRules(data, "port_rules")
		var rule map[string]any
		kept := []map[string]any{}
		for _, rr := range rules {
			if str(rr["id"]) == id {
				rule = rr
			} else {
				kept = append(kept, rr)
			}
		}
		if rule == nil {
			return NewHTTPError(404, "Rule not found")
		}
		delPortRule(rule)
		data["port_rules"] = kept
		if err := saveFirewall(data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/firewall/ip", perm("firewall"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			IP      string `json:"ip"`
			Action  string `json:"action"`
			Comment string `json:"comment"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if err := validateIPValue(req.IP); err != nil {
			return err
		}
		if req.Action != "allow" && req.Action != "deny" {
			req.Action = "allow"
		}
		rule := map[string]any{
			"id": randomHex(8), "ip": req.IP, "action": req.Action,
			"comment": req.Comment, "created_at": time.Now().Format("2006-01-02T15:04:05"),
		}
		addIPRule(rule)
		data := loadFirewall()
		data["ip_rules"] = append(firewallRules(data, "ip_rules"), rule)
		if err := saveFirewall(data); err != nil {
			return err
		}
		return WriteJSON(w, 200, rule)
	})
	a.Delete("/api/firewall/ip/{rule_id}", perm("firewall"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "rule_id")
		data := loadFirewall()
		var rule map[string]any
		kept := []map[string]any{}
		for _, rr := range firewallRules(data, "ip_rules") {
			if str(rr["id"]) == id {
				rule = rr
			} else {
				kept = append(kept, rr)
			}
		}
		if rule == nil {
			return NewHTTPError(404, "Rule not found")
		}
		delIPRule(rule)
		data["ip_rules"] = kept
		if err := saveFirewall(data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/firewall/toggle", perm("firewall"), func(w http.ResponseWriter, r *http.Request) error {
		var body map[string]any
		_ = DecodeJSON(r, &body)
		enabled := true
		if v, ok := body["enabled"].(bool); ok {
			enabled = v
		}
		data := loadFirewall()
		data["enabled"] = enabled
		if err := saveFirewall(data); err != nil {
			return err
		}
		if windowsTarget() {
			action := "off"
			if enabled {
				action = "on"
			}
			HostCmd(10*time.Second, "netsh", "advfirewall", "set", "allprofiles", "state", action)
		}
		return WriteJSON(w, 200, map[string]any{"enabled": enabled})
	})
	a.Post("/api/firewall/clear", perm("firewall"), func(w http.ResponseWriter, r *http.Request) error {
		data := loadFirewall()
		removed := 0
		for _, rule := range firewallRules(data, "port_rules") {
			delPortRule(rule)
			removed++
		}
		for _, rule := range firewallRules(data, "ip_rules") {
			delIPRule(rule)
			removed++
		}
		data["port_rules"] = []any{}
		data["ip_rules"] = []any{}
		if err := saveFirewall(data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "removed": removed})
	})
	a.Post("/api/firewall/reconcile", perm("firewall"), func(w http.ResponseWriter, r *http.Request) error {
		data := loadFirewall()
		count := 0
		for _, rule := range firewallRules(data, "port_rules") {
			addPortRule(rule)
			count++
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "applied": count})
	})
	a.Get("/api/firewall/listening", perm("firewall"), func(w http.ResponseWriter, r *http.Request) error {
		data := loadFirewall()
		allowed := map[int]bool{}
		for _, rule := range firewallRules(data, "port_rules") {
			if str(rule["action"]) == "allow" {
				allowed[int(int64Of(rule["port"]))] = true
			}
		}
		ports := listeningTCPPorts()
		items := []map[string]any{}
		ordered := []int{}
		for p := range ports {
			ordered = append(ordered, p)
		}
		sortInts(ordered)
		for _, p := range ordered {
			items = append(items, map[string]any{
				"port": p, "allowed": allowed[p], "protected": blockProtectedPorts[p],
			})
		}
		return WriteJSON(w, 200, map[string]any{"ports": items})
	})
	a.Post("/api/firewall/block-unopened", perm("firewall"), func(w http.ResponseWriter, r *http.Request) error {
		data := loadFirewall()
		allowSet, denySet := map[int]bool{}, map[int]bool{}
		for _, rule := range firewallRules(data, "port_rules") {
			p := int(int64Of(rule["port"]))
			if str(rule["action"]) == "allow" {
				allowSet[p] = true
			} else if str(rule["action"]) == "deny" {
				denySet[p] = true
			}
		}
		listening := listeningTCPPorts()
		targets := []int{}
		for p := range listening {
			if !allowSet[p] && !denySet[p] && !blockProtectedPorts[p] {
				targets = append(targets, p)
			}
		}
		sortInts(targets)
		created := []int{}
		for _, p := range targets {
			rule := map[string]any{
				"id": randomHex(8), "port": p, "protocol": "tcp", "action": "deny",
				"comment": "默认屏蔽未放行端口", "created_at": time.Now().Format("2006-01-02T15:04:05"),
			}
			addPortRule(rule)
			data["port_rules"] = append(firewallRules(data, "port_rules"), rule)
			created = append(created, p)
		}
		if err := saveFirewall(data); err != nil {
			return err
		}
		protected := []int{}
		for p := range listening {
			if blockProtectedPorts[p] && !allowSet[p] {
				protected = append(protected, p)
			}
		}
		sortInts(protected)
		return WriteJSON(w, 200, map[string]any{
			"ok": true, "created": len(created), "ports": created, "skipped_protected": protected,
		})
	})
}

func sortInts(v []int) {
	for i := 1; i < len(v); i++ {
		for j := i; j > 0 && v[j] < v[j-1]; j-- {
			v[j], v[j-1] = v[j-1], v[j]
		}
	}
}
