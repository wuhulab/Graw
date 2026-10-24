package graw

import (
	"math"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/shirou/gopsutil/v4/process"
)

func registerProcessRoutes(a *App) {
	a.Get("/api/process/list", perm("process"), func(w http.ResponseWriter, r *http.Request) error {
		sortBy := r.URL.Query().Get("sort_by")
		limit := QueryInt(r, "limit", 200)
		if limit <= 0 || limit > 2000 {
			limit = 200
		}
		var procs []map[string]any
		if remoteActive() {
			procs = remoteProcessList()
		} else {
			procs = listProcesses()
		}
		key := "cpu"
		if sortBy == "memory" || sortBy == "pid" || sortBy == "name" {
			key = sortBy
		}
		sort.Slice(procs, func(i, j int) bool {
			switch key {
			case "memory":
				return num(procs[i]["memory"]) > num(procs[j]["memory"])
			case "pid":
				return num(procs[i]["pid"]) < num(procs[j]["pid"])
			case "name":
				return strings.ToLower(str(procs[i]["name"])) < strings.ToLower(str(procs[j]["name"]))
			default:
				return num(procs[i]["cpu"]) > num(procs[j]["cpu"])
			}
		})
		if len(procs) > limit {
			procs = procs[:limit]
		}
		return WriteJSON(w, 200, procs)
	})
	a.Post("/api/process/{pid}/kill", perm("process"), func(w http.ResponseWriter, r *http.Request) error {
		u := CurrentUser(r)
		pid, err := strconv.Atoi(PathParam(r, "pid"))
		if err != nil || pid <= 0 || pid > math.MaxInt32 {
			return NewHTTPError(422, "pid 非法")
		}
		var req struct {
			Force bool `json:"force"`
		}
		_ = DecodeJSON(r, &req)
		if remoteActive() {
			signum := 15
			if req.Force {
				signum = 9
			}
			res := HostShell(15*time.Second, "kill -"+itoa(signum)+" "+itoa(pid))
			if res.Code != 0 {
				if strings.Contains(strings.ToLower(res.Stderr), "no such process") {
					return NewHTTPError(404, "Process not found")
				}
				return NewHTTPError(500, strings.TrimSpace(res.Stderr))
			}
			AuditRecord("结束进程", u.Username(), ClientIP(r),
				"pid:"+itoa(pid)+" 强制:"+strconv.FormatBool(req.Force))
			return WriteJSON(w, 200, map[string]any{"ok": true})
		}
		p, err := process.NewProcess(int32(pid))
		if err != nil {
			return NewHTTPError(404, "Process not found")
		}
		name, _ := p.Name()
		if req.Force {
			err = p.Kill()
		} else {
			err = p.Terminate()
		}
		if err != nil {
			if strings.Contains(strings.ToLower(err.Error()), "no such process") {
				return NewHTTPError(404, "Process not found")
			}
			if osPermission(err) {
				return NewHTTPError(403, "Access denied")
			}
			return NewHTTPError(500, err.Error())
		}
		AuditRecord("结束进程", u.Username(), ClientIP(r),
			"pid:"+itoa(pid)+" name:"+name+" 强制:"+strconv.FormatBool(req.Force))
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Get("/api/process/{pid}", perm("process"), func(w http.ResponseWriter, r *http.Request) error {
		pid, err := strconv.Atoi(PathParam(r, "pid"))
		if err != nil || pid <= 0 || pid > math.MaxInt32 {
			return NewHTTPError(422, "pid 非法")
		}
		if remoteActive() {
			detail := remoteProcessDetail(pid)
			if _, missing := detail["detail"]; missing {
				return NewHTTPError(404, "Process not found")
			}
			return WriteJSON(w, 200, detail)
		}
		p, err := process.NewProcess(int32(pid))
		if err != nil {
			return NewHTTPError(404, "Process not found")
		}
		name, _ := p.Name()
		exe, _ := p.Exe()
		cmdline, _ := p.CmdlineSlice()
		username, _ := p.Username()
		statuses, _ := p.Status()
		cpu, _ := p.Percent(0)
		rss := uint64(0)
		if mi, err := p.MemoryInfo(); err == nil && mi != nil {
			rss = mi.RSS
		}
		threads, _ := p.NumThreads()
		ct, _ := p.CreateTime()
		return WriteJSON(w, 200, map[string]any{
			"pid": pid, "name": name, "exe": exe, "cmdline": cmdline,
			"username": username, "status": strings.Join(statuses, "+"),
			"cpu": round1(cpu), "memory": rss, "num_threads": threads,
			"create_time": ct,
		})
	})
}

// remoteProcessList 通过 ps 采样远程节点进程列表。
func remoteProcessList() []map[string]any {
	res := HostShell(20*time.Second,
		"ps -eo pid=,user=,stat=,pcpu=,rss=,etimes=,comm= --no-headers 2>/dev/null || ps -eo pid=,user=,stat=,pcpu=,rss=,etimes=,comm=")
	procs := []map[string]any{}
	now := time.Now().Unix()
	for _, line := range strings.Split(res.Stdout, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 7 {
			continue
		}
		pid, _ := strconv.Atoi(fields[0])
		cpu, _ := strconv.ParseFloat(fields[3], 64)
		rssKB, _ := strconv.ParseInt(fields[4], 10, 64)
		etimes, _ := strconv.ParseInt(fields[5], 10, 64)
		procs = append(procs, map[string]any{
			"pid": pid, "username": fields[1], "status": fields[2],
			"cpu": round1(cpu), "memory": rssKB * 1024,
			"create_time": now - etimes, "name": fields[6],
		})
	}
	return procs
}

func remoteProcessDetail(pid int) map[string]any {
	res := HostShell(10*time.Second,
		"ps -p "+itoa(pid)+" -o pid=,user=,stat=,pcpu=,rss=,etimes=,comm=,args= 2>/dev/null || echo MISSING")
	out := strings.TrimSpace(res.Stdout)
	if out == "" || strings.Contains(out, "MISSING") {
		return map[string]any{"detail": "Process not found"}
	}
	fields := strings.Fields(out)
	if len(fields) < 7 {
		return map[string]any{"detail": "Process not found"}
	}
	cpu, _ := strconv.ParseFloat(fields[3], 64)
	rssKB, _ := strconv.ParseInt(fields[4], 10, 64)
	etimes, _ := strconv.ParseInt(fields[5], 10, 64)
	args := fields[6:]
	return map[string]any{
		"pid": pid, "username": fields[1], "status": fields[2],
		"cpu": round1(cpu), "memory": rssKB * 1024,
		"create_time": time.Now().Unix() - etimes, "name": fields[6],
		"exe": "", "cmdline": args, "num_threads": 0,
	}
}

func osPermission(err error) bool {
	return strings.Contains(strings.ToLower(err.Error()), "permission")
}

func listProcesses() []map[string]any {
	procs, err := process.Processes()
	if err != nil {
		return []map[string]any{}
	}
	for _, p := range procs {
		_, _ = p.Percent(0)
	}
	time.Sleep(100 * time.Millisecond)
	out := make([]map[string]any, 0, len(procs))
	for _, p := range procs {
		cpu, err := p.Percent(0)
		if err != nil {
			continue
		}
		name, _ := p.Name()
		username, _ := p.Username()
		statuses, _ := p.Status()
		rss := uint64(0)
		if mi, err := p.MemoryInfo(); err == nil && mi != nil {
			rss = mi.RSS
		}
		ct, _ := p.CreateTime()
		out = append(out, map[string]any{
			"pid": p.Pid, "name": name, "username": username,
			"status": strings.Join(statuses, "+"), "cpu": round1(cpu),
			"memory": rss, "create_time": ct,
		})
	}
	return out
}
