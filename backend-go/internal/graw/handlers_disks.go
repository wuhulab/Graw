package graw

import (
	"encoding/json"
	"net/http"
	"runtime"
	"strconv"
	"strings"
	"time"

	"github.com/shirou/gopsutil/v4/disk"
)

type lsblkNode struct {
	Name       string      `json:"name"`
	Size       json.Number `json:"size"`
	Rota       any         `json:"rota"`
	Type       string      `json:"type"`
	Fstype     string      `json:"fstype"`
	Mountpoint *string     `json:"mountpoint"`
	Label      string      `json:"label"`
	Model      string      `json:"model"`
	Children   []lsblkNode `json:"children"`
}

func gb(v uint64) string {
	g := float64(v) / (1024 * 1024 * 1024)
	if g >= 100 {
		return itoa(int(g+0.5)) + "G"
	}
	return strconvF(g, 1) + "G"
}

func strconvF(v float64, dec int) string {
	b, _ := json.Marshal(roundN(v, dec))
	s := string(b)
	if dec == 1 && !strings.Contains(s, ".") {
		s += ".0"
	}
	return s
}

func roundN(v float64, dec int) float64 {
	pow := 1.0
	for i := 0; i < dec; i++ {
		pow *= 10
	}
	return float64(int64(v*pow+0.5)) / pow
}

func lsblkRota(v any) string {
	switch x := v.(type) {
	case bool:
		if x {
			return "1"
		}
		return "0"
	case string:
		return x
	case json.Number:
		return x.String()
	case float64:
		if x != 0 {
			return "1"
		}
		return "0"
	}
	return "0"
}

func parseLsblkTree(node lsblkNode, isTop bool) ([]map[string]any, []map[string]any) {
	partitions := []map[string]any{}
	disks := []map[string]any{}
	for _, child := range node.Children {
		ctype := strings.TrimSpace(child.Type)
		if ctype == "disk" {
			d, _ := parseLsblkTree(child, false)
			disks = append(disks, d...)
		} else if ctype == "part" || ctype == "lvm" || ctype == "crypt" {
			mp := ""
			if child.Mountpoint != nil {
				mp = strings.TrimSpace(*child.Mountpoint)
			}
			size, _ := child.Size.Int64()
			partitions = append(partitions, map[string]any{
				"name": child.Name, "device": "/dev/" + child.Name, "size": size,
				"fstype": child.Fstype, "mountpoint": mp, "label": child.Label,
			})
		}
	}
	if strings.TrimSpace(node.Type) == "disk" {
		size, _ := node.Size.Int64()
		dtype := "SSD"
		if lsblkRota(node.Rota) == "1" {
			dtype = "HDD"
		}
		disks = append(disks, map[string]any{
			"name": node.Name, "size": size, "size_display": gb(uint64(size)),
			"partitions": len(partitions), "type": dtype, "model": node.Model,
			"system": isSystemDisk(node.Name, partitions), "parts": partitions,
		})
	}
	_ = isTop
	return disks, partitions
}

func isSystemDisk(diskName string, partitions []map[string]any) bool {
	roots := map[string]bool{"/": true, "/boot": true}
	if GetConfig().HostRoot != "" {
		roots[GetConfig().HostRoot] = true
	}
	for _, p := range partitions {
		mp, _ := p["mountpoint"].(string)
		if roots[strings.TrimSpace(mp)] {
			name, _ := p["name"].(string)
			if name == diskName || strings.HasPrefix(name, diskName) {
				return true
			}
		}
	}
	return false
}

func loadLsblkDisks() []map[string]any {
	if HostWhich("lsblk") == "" {
		return []map[string]any{}
	}
	res := HostCmd(10*time.Second, "lsblk", "-b", "-J", "-o", "NAME,SIZE,ROTA,TYPE,FSTYPE,MOUNTPOINT,LABEL,MODEL")
	if res.Code != 0 {
		res = runCommand(10*time.Second, "lsblk", "-b", "-J", "-o", "NAME,SIZE,ROTA,TYPE,FSTYPE,MOUNTPOINT,LABEL,MODEL")
	}
	var payload struct {
		Blockdevices []lsblkNode `json:"blockdevices"`
	}
	if err := json.Unmarshal([]byte(res.Stdout), &payload); err != nil {
		return []map[string]any{}
	}
	disks := []map[string]any{}
	for _, node := range payload.Blockdevices {
		d, _ := parseLsblkTree(node, true)
		disks = append(disks, d...)
	}
	return disks
}

func loadWindowsDisks() []map[string]any {
	seen := map[string]map[string]any{}
	for _, part := range diskPartitions() {
		name := strings.TrimRight(part.Device, ":\\")
		if name == "" {
			name = "disk"
		}
		d, ok := seen[name]
		if !ok {
			d = map[string]any{
				"name": name, "size": uint64(0), "size_display": "-", "partitions": 0,
				"type": "SSD", "model": "", "system": true, "parts": []map[string]any{},
			}
			seen[name] = d
		}
		parts := d["parts"].([]map[string]any)
		parts = append(parts, map[string]any{
			"name": part.Device, "device": part.Device, "size": uint64(0),
			"fstype": part.Fstype, "mountpoint": part.Mountpoint, "label": "",
		})
		d["parts"] = parts
		d["partitions"] = len(parts)
	}
	out := make([]map[string]any, 0, len(seen))
	for _, d := range seen {
		out = append(out, d)
	}
	return out
}

func diskPartitions() []disk.PartitionStat {
	parts, err := disk.Partitions(false)
	if err != nil {
		return nil
	}
	return parts
}

func attachDiskUsage(disks []map[string]any) {
	for _, d := range disks {
		parts, _ := d["parts"].([]map[string]any)
		for _, p := range parts {
			mp, _ := p["mountpoint"].(string)
			if mp == "" {
				p["used"], p["available"], p["percent"] = uint64(0), uint64(0), nil
				continue
			}
			if remoteActive() {
				res := HostCmd(10*time.Second, "df", "-kP", "--", mp)
				lines := strings.Split(strings.TrimSpace(res.Stdout), "\n")
				if res.Code != 0 || len(lines) == 0 {
					p["used"], p["available"], p["percent"] = uint64(0), uint64(0), nil
					continue
				}
				fields := strings.Fields(lines[len(lines)-1])
				if len(fields) < 5 {
					p["used"], p["available"], p["percent"] = uint64(0), uint64(0), nil
					continue
				}
				totalKB, _ := strconv.ParseInt(fields[1], 10, 64)
				usedKB, _ := strconv.ParseInt(fields[2], 10, 64)
				availKB, _ := strconv.ParseInt(fields[3], 10, 64)
				pct := 0.0
				if totalKB > 0 {
					pct = float64(usedKB) / float64(totalKB) * 100
				}
				p["size"], p["used"], p["available"] = uint64(totalKB)*1024, uint64(usedKB)*1024, uint64(availKB)*1024
				p["percent"] = round1(pct)
				p["size_display"] = gb(uint64(totalKB) * 1024)
				p["used_display"] = gb(uint64(usedKB) * 1024)
				p["avail_display"] = gb(uint64(availKB) * 1024)
				continue
			}
			target := mp
			if mp == "/" {
				target = HostPath("/")
			}
			usage, err := disk.Usage(target)
			if err != nil {
				p["used"], p["available"], p["percent"] = uint64(0), uint64(0), nil
				continue
			}
			p["size"] = usage.Total
			p["used"] = usage.Used
			p["available"] = usage.Free
			p["percent"] = round1(usage.UsedPercent)
			p["size_display"] = gb(usage.Total)
			p["used_display"] = gb(usage.Used)
			p["avail_display"] = gb(usage.Free)
		}
	}
}

func registerDiskRoutes(a *App) {
	a.Get("/api/disks/list", perm("disks"), func(w http.ResponseWriter, r *http.Request) error {
		var disks []map[string]any
		if runtime.GOOS == "windows" && GetConfig().HostRoot == "" && !remoteActive() {
			disks = loadWindowsDisks()
		} else {
			disks = loadLsblkDisks()
		}
		attachDiskUsage(disks)
		for _, d := range disks {
			name, _ := d["name"].(string)
			parts, _ := d["parts"].([]map[string]any)
			sys := isSystemDisk(name, parts)
			d["system"] = sys
			for _, p := range parts {
				p["system"] = sys
			}
		}
		return WriteJSON(w, 200, map[string]any{"disks": disks})
	})
	a.Post("/api/disks/mount", perm("disks"), func(w http.ResponseWriter, r *http.Request) error {
		device := r.URL.Query().Get("device")
		mountpoint := r.URL.Query().Get("mountpoint")
		if runtime.GOOS == "windows" && !remoteActive() {
			return WriteJSON(w, 200, map[string]any{"ok": false, "message": "Windows 环境不支持 mount 命令"})
		}
		valid := device != ""
		for _, c := range device {
			if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '.' || c == '-' || c == '_') {
				valid = false
				break
			}
		}
		if !valid {
			return WriteJSON(w, 200, map[string]any{"ok": false, "message": "设备名不合法"})
		}
		if !strings.HasPrefix(mountpoint, "/") || mountpoint == "/" {
			return WriteJSON(w, 200, map[string]any{"ok": false, "message": "挂载点必须是绝对路径且不能为根目录"})
		}
		if HostWhich("mount") == "" {
			return WriteJSON(w, 200, map[string]any{"ok": false, "message": "宿主机缺少 mount 命令"})
		}
		devPath := "/dev/" + device
		hostMp := HostPath(mountpoint)
		if res := HostCmd(30*time.Second, "mkdir", "-p", hostMp); res.Code != 0 {
			return WriteJSON(w, 200, map[string]any{"ok": false, "message": "创建挂载目录失败：" + strings.TrimSpace(res.Stderr)})
		}
		if res := HostCmd(60*time.Second, "mount", devPath, hostMp); res.Code != 0 {
			return WriteJSON(w, 200, map[string]any{"ok": false, "message": "挂载失败：" + strings.TrimSpace(res.Stderr)})
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "message": "挂载成功"})
	})
}
