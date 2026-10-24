package graw

import (
	"encoding/json"
	"fmt"
	"os"
	"runtime"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/shirou/gopsutil/v4/cpu"
	"github.com/shirou/gopsutil/v4/disk"
	"github.com/shirou/gopsutil/v4/host"
	"github.com/shirou/gopsutil/v4/load"
	"github.com/shirou/gopsutil/v4/mem"
	gnet "github.com/shirou/gopsutil/v4/net"
	"github.com/shirou/gopsutil/v4/sensors"
)

type ioCounters struct {
	At     time.Time
	NetIn  uint64
	NetOut uint64
	DiskR  uint64
	DiskW  uint64
}

// MetricsHub 负责统一采集、广播与缓存系统指标。
type MetricsHub struct {
	mu       sync.Mutex
	clients  map[*wsClient]struct{}
	cache    map[string]any
	fails    int
	lastNet  ioCounters
	lastDisk ioCounters
	rNet     ioCounters
	rDisk    ioCounters
}

var metricsHub = &MetricsHub{clients: map[*wsClient]struct{}{}}

type wsClient struct {
	conn *websocketConn
	mu   sync.Mutex
}

// 为避免在 sysmon 中暴露 gorilla 类型，这里用一个最小写接口（见 ws.go）。
type websocketConn = wsConn

// Overview 采集 CPU/内存/磁盘/负载/温度。
func (h *MetricsHub) overview() map[string]any {
	if remoteActive() {
		return h.remoteOverview()
	}
	cpuPct := 0.0
	if vals, err := cpu.Percent(0, false); err == nil && len(vals) > 0 {
		cpuPct = vals[0]
	}
	m, err := mem.VirtualMemory()
	memTotal, memAvail, memUsed, memPct := uint64(0), uint64(0), uint64(0), 0.0
	if err == nil {
		memTotal = m.Total
		memAvail = m.Available
		memUsed = m.Total - m.Available
		memPct = float64(memUsed) / float64(maxu(memTotal, 1)) * 100
	}
	diskPath := "/"
	if isWindows() {
		diskPath = `C:\`
	} else {
		diskPath = HostPath("/")
	}
	storage := map[string]any{"percent": 0.0, "total": uint64(0), "used": uint64(0), "free": uint64(0)}
	if du, err := disk.Usage(diskPath); err == nil {
		storage = map[string]any{
			"percent": round1(du.UsedPercent), "total": du.Total, "used": du.Used, "free": du.Free,
		}
	}
	ncpu := runtime.NumCPU()
	if n, err := cpu.Counts(true); err == nil && n > 0 {
		ncpu = n
	}
	load1, load5, load15 := cpuPct/100*float64(ncpu), 0.0, 0.0
	if la, err := load.Avg(); err == nil {
		load1, load5, load15 = la.Load1, la.Load5, la.Load15
	}
	loadPct := load1 / float64(maxi(ncpu, 1)) * 100
	if loadPct > 100 {
		loadPct = 100
	}
	return map[string]any{
		"cpu": round1(cpuPct),
		"memory": map[string]any{
			"percent": round1(memPct), "total": memTotal, "used": memUsed, "available": memAvail,
		},
		"storage": storage,
		"load": map[string]any{
			"percent": round1(loadPct), "load1": round2(load1), "load5": round2(load5), "load15": round2(load15),
		},
		"temps": collectTemps(),
	}
}

func (h *MetricsHub) network() map[string]any {
	if remoteActive() {
		return h.remoteNetwork()
	}
	var sent, recv uint64
	if counters, err := gnet.IOCounters(false); err == nil && len(counters) > 0 {
		sent = counters[0].BytesSent
		recv = counters[0].BytesRecv
	}
	now := time.Now()
	h.mu.Lock()
	prevSent, prevRecv := h.lastNet.NetOut, h.lastNet.NetIn
	prevAt := h.lastNet.At
	h.lastNet = ioCounters{At: now, NetOut: sent, NetIn: recv}
	h.mu.Unlock()
	elapsed := now.Sub(prevAt).Seconds()
	if prevAt.IsZero() || elapsed < 0.001 {
		elapsed = 1
	}
	up := float64(int64(sent)-int64(prevSent)) / elapsed
	down := float64(int64(recv)-int64(prevRecv)) / elapsed
	if up < 0 {
		up = 0
	}
	if down < 0 {
		down = 0
	}
	return map[string]any{
		"timestamp": now.UnixMilli(), "upload": up, "download": down,
		"total_sent": sent, "total_recv": recv,
	}
}

func (h *MetricsHub) diskio() map[string]any {
	if remoteActive() {
		return h.remoteDiskIO()
	}
	var read, write uint64
	if counters, err := disk.IOCounters(); err == nil {
		for _, c := range counters {
			read += c.ReadBytes
			write += c.WriteBytes
		}
	}
	now := time.Now()
	h.mu.Lock()
	prevRead, prevWrite := h.lastDisk.DiskR, h.lastDisk.DiskW
	prevAt := h.lastDisk.At
	h.lastDisk = ioCounters{At: now, DiskR: read, DiskW: write}
	h.mu.Unlock()
	elapsed := now.Sub(prevAt).Seconds()
	if prevAt.IsZero() || elapsed < 0.001 {
		elapsed = 1
	}
	rs := float64(int64(read)-int64(prevRead)) / elapsed
	ws := float64(int64(write)-int64(prevWrite)) / elapsed
	if rs < 0 {
		rs = 0
	}
	if ws < 0 {
		ws = 0
	}
	return map[string]any{"timestamp": now.UnixMilli(), "read": rs, "write": ws}
}

// ioRefreshLocked 仅预热计数基线（调用方需持有 h.mu）。
func (h *MetricsHub) ioRefreshLocked() {
	now := time.Now()
	if counters, err := gnet.IOCounters(false); err == nil && len(counters) > 0 {
		h.lastNet = ioCounters{At: now, NetOut: counters[0].BytesSent, NetIn: counters[0].BytesRecv}
	} else {
		h.lastNet = ioCounters{At: now}
	}
	var read, write uint64
	if counters, err := disk.IOCounters(); err == nil {
		for _, c := range counters {
			read += c.ReadBytes
			write += c.WriteBytes
		}
	}
	h.lastDisk = ioCounters{At: now, DiskR: read, DiskW: write}
}

func (h *MetricsHub) info() map[string]any {
	if remoteActive() {
		return h.remoteInfo()
	}
	info, _ := host.Info()
	logical := runtime.NumCPU()
	physical := runtime.NumCPU()
	if n, err := cpu.Counts(true); err == nil {
		logical = n
	}
	if n, err := cpu.Counts(false); err == nil && n > 0 {
		physical = n
	}
	processor := ""
	if infos, err := cpu.Info(); err == nil && len(infos) > 0 {
		processor = infos[0].ModelName
	}
	hostname := ""
	system, release, version, machine := runtime.GOOS, "", "", runtime.GOARCH
	bootTime := time.Now()
	uptime := int64(0)
	if info != nil {
		hostname = info.Hostname
		system = platformName(info.OS)
		release = info.KernelVersion
		version = info.PlatformVersion
		machine = info.KernelArch
		bootTime = time.Unix(int64(info.BootTime), 0)
		uptime = int64(info.Uptime)
	}
	return map[string]any{
		"hostname": hostname, "system": system, "release": release,
		"version": version, "machine": machine, "processor": processor,
		"python_version": runtime.Version(), "cpu_count": logical,
		"cpu_count_physical": physical,
		"boot_time":          bootTime.Format("2006-01-02T15:04:05"),
		"uptime_seconds":     uptime,
	}
}

// CollectAll 采集四项合并指标。
func (h *MetricsHub) CollectAll() map[string]any {
	if remoteActive() {
		// 每个采集周期强制刷新一次远端批量脚本，四项指标共用。
		fetchRemoteBlocks(true)
	}
	return map[string]any{
		"overview": h.overview(),
		"network":  h.network(),
		"diskio":   h.diskio(),
		"info":     h.info(),
	}
}

func collectTemps() []map[string]any {
	out := []map[string]any{}
	seen := map[string]bool{}
	if list, err := sensors.SensorsTemperatures(); err == nil {
		for _, s := range list {
			temp := s.Temperature
			if temp <= 0 || temp > 150 {
				continue
			}
			name := s.SensorKey
			if name == "" {
				continue
			}
			if seen[name] {
				continue
			}
			seen[name] = true
			kind := "other"
			low := strings.ToLower(name)
			for _, kw := range []string{"cpu", "core", "package", "k10temp", "coretemp", "tdie", "soc", "tctl"} {
				if strings.Contains(low, kw) {
					kind = "cpu"
					break
				}
			}
			if len(name) > 48 {
				name = name[:48]
			}
			out = append(out, map[string]any{
				"name": name, "value": round1(temp),
				"kind": kind, "source": s.SensorKey,
			})
		}
	}
	sort.SliceStable(out, func(i, j int) bool {
		ki, kj := out[i]["kind"].(string), out[j]["kind"].(string)
		if ki != kj {
			return ki == "cpu"
		}
		return out[i]["name"].(string) < out[j]["name"].(string)
	})
	if len(out) > 8 {
		out = out[:8]
	}
	return out
}

// ---------------------------------------------------------------------------
// 裸 SSH 远端指标采集：单次连接执行一段脚本，取回 CPU/内存/磁盘/负载/网络/IO/温度。
// ---------------------------------------------------------------------------
const remoteMetricsScript = `
echo "===BLOCKSTAT0==="; head -1 /proc/stat 2>/dev/null
sleep 0.12
echo "===BLOCKSTAT1==="; head -1 /proc/stat 2>/dev/null
echo "===BLOCKBTIME==="; grep '^btime' /proc/stat 2>/dev/null
echo "===BLOCKMEM==="; cat /proc/meminfo 2>/dev/null
echo "===BLOCKDF==="; df -kP / 2>/dev/null | tail -1
echo "===BLOCKLOAD==="; cat /proc/loadavg 2>/dev/null
echo "===BLOCKNPROC==="; nproc 2>/dev/null
echo "===BLOCKNET==="; cat /proc/net/dev 2>/dev/null
echo "===BLOCKDISK==="; cat /proc/diskstats 2>/dev/null | awk '{print $6, $10}'
echo "===BLOCKNAME==="; hostname 2>/dev/null | head -1
echo "===BLOCKUNAME==="; uname -s 2>/dev/null; uname -r 2>/dev/null; uname -v 2>/dev/null; uname -m 2>/dev/null
echo "===BLOCKCPUINFO==="; grep 'model name' /proc/cpuinfo 2>/dev/null | head -1
echo "===BLOCKHWMON==="; for d in /sys/class/hwmon/hwmon*; do [ -d "$d" ] || continue; n=$(cat "$d/name" 2>/dev/null); for f in "$d"/temp*_input; do [ -f "$f" ] || continue; b=${f%_input}; echo "$n|$(cat "${b}_label" 2>/dev/null)|$(cat "$f" 2>/dev/null)"; done; done
echo "===BLOCKTHERMAL==="; for z in /sys/class/thermal/thermal_zone*; do [ -d "$z" ] || continue; echo "$(cat "$z/type" 2>/dev/null)|$(cat "$z/temp" 2>/dev/null)"; done
`

var (
	remoteBlocksMu    sync.Mutex
	remoteBlocksCache = map[string]map[string]string{}
	remoteBlocksAt    = map[string]time.Time{}
)

func fetchRemoteBlocks(force bool) map[string]string {
	nodeID := resolvedNodeID()
	remoteBlocksMu.Lock()
	defer remoteBlocksMu.Unlock()
	if !force {
		if cache, ok := remoteBlocksCache[nodeID]; ok && time.Since(remoteBlocksAt[nodeID]) < 4*time.Second {
			return cache
		}
	}
	res := HostShell(20*time.Second, remoteMetricsScript)
	blocks := map[string]string{}
	cur := ""
	buf := []string{}
	for _, line := range strings.Split(res.Stdout, "\n") {
		if strings.HasPrefix(line, "===BLOCK") && strings.HasSuffix(line, "===") {
			if cur != "" {
				blocks[cur] = strings.TrimSpace(strings.Join(buf, "\n"))
			}
			cur = strings.Trim(strings.TrimPrefix(line, "===BLOCK"), "=")
			buf = []string{}
			continue
		}
		buf = append(buf, line)
	}
	if cur != "" {
		blocks[cur] = strings.TrimSpace(strings.Join(buf, "\n"))
	}
	remoteBlocksCache[nodeID] = blocks
	remoteBlocksAt[nodeID] = time.Now()
	return blocks
}

func parseProcStatLine(line string) (int64, int64) {
	fields := strings.Fields(line)
	if len(fields) < 5 {
		return 0, 0
	}
	total, idle := int64(0), int64(0)
	for i, x := range fields[1:] {
		var n int64
		fmt.Sscanf(x, "%d", &n)
		total += n
		if i == 3 {
			idle = n
		}
	}
	return total, idle
}

func remoteTemps(blocks map[string]string) []map[string]any {
	out := []map[string]any{}
	seen := map[string]bool{}
	for _, line := range strings.Split(blocks["HWMON"], "\n") {
		parts := strings.Split(line, "|")
		if len(parts) != 3 {
			continue
		}
		var milli float64
		fmt.Sscanf(strings.TrimSpace(parts[2]), "%f", &milli)
		value := milli / 1000
		if value <= 0 || value >= 150 {
			continue
		}
		name := strings.TrimSpace(parts[1])
		if name == "" {
			name = strings.TrimSpace(parts[0])
		}
		if name == "" || seen[name] {
			continue
		}
		seen[name] = true
		kind := "other"
		low := strings.ToLower(parts[0] + " " + name)
		for _, kw := range []string{"cpu", "core", "package", "k10temp", "coretemp", "tdie", "soc", "tctl"} {
			if strings.Contains(low, kw) {
				kind = "cpu"
				break
			}
		}
		out = append(out, map[string]any{"name": name, "value": round1(value), "kind": kind, "source": parts[0]})
	}
	if len(out) == 0 {
		for _, line := range strings.Split(blocks["THERMAL"], "\n") {
			parts := strings.Split(line, "|")
			if len(parts) != 2 {
				continue
			}
			var milli float64
			fmt.Sscanf(strings.TrimSpace(parts[1]), "%f", &milli)
			value := milli / 1000
			if value <= 0 || value >= 150 {
				continue
			}
			name := strings.TrimSpace(parts[0])
			if name == "" || seen[name] {
				continue
			}
			seen[name] = true
			kind := "other"
			if strings.Contains(strings.ToLower(name), "cpu") || strings.Contains(strings.ToLower(name), "soc") {
				kind = "cpu"
			}
			out = append(out, map[string]any{"name": name, "value": round1(value), "kind": kind, "source": name})
		}
	}
	sort.SliceStable(out, func(i, j int) bool {
		ki, kj := out[i]["kind"].(string), out[j]["kind"].(string)
		if ki != kj {
			return ki == "cpu"
		}
		return out[i]["name"].(string) < out[j]["name"].(string)
	})
	if len(out) > 8 {
		out = out[:8]
	}
	return out
}

func (h *MetricsHub) remoteOverview() map[string]any {
	blocks := fetchRemoteBlocks(false)
	t0, i0 := parseProcStatLine(blocks["STAT0"])
	t1, i1 := parseProcStatLine(blocks["STAT1"])
	cpuPct := 0.0
	if t1 > t0 {
		cpuPct = 100 * (1 - float64(i1-i0)/float64(t1-t0))
	}
	memTotal, memAvail := int64(0), int64(0)
	for _, line := range strings.Split(blocks["MEM"], "\n") {
		if strings.HasPrefix(line, "MemTotal:") {
			fmt.Sscanf(strings.Fields(line)[1], "%d", &memTotal)
		}
		if strings.HasPrefix(line, "MemAvailable:") {
			fmt.Sscanf(strings.Fields(line)[1], "%d", &memAvail)
		}
	}
	memTotal *= 1024
	memAvail *= 1024
	if memAvail == 0 {
		memAvail = memTotal
	}
	memUsed := memTotal - memAvail
	memPct := 0.0
	if memTotal > 0 {
		memPct = float64(memUsed) / float64(memTotal) * 100
	}
	storage := map[string]any{"percent": 0.0, "total": int64(0), "used": int64(0), "free": int64(0)}
	df := strings.Fields(blocks["DF"])
	if len(df) >= 5 {
		var total, used, avail int64
		fmt.Sscanf(df[1], "%d", &total)
		fmt.Sscanf(df[2], "%d", &used)
		fmt.Sscanf(df[3], "%d", &avail)
		pct := 0.0
		if total > 0 {
			pct = float64(used) / float64(total) * 100
		}
		storage = map[string]any{
			"percent": round1(pct), "total": total * 1024, "used": used * 1024, "free": avail * 1024,
		}
	}
	load1, load5, load15 := 0.0, 0.0, 0.0
	ld := strings.Fields(blocks["LOAD"])
	if len(ld) >= 3 {
		fmt.Sscanf(ld[0], "%f", &load1)
		fmt.Sscanf(ld[1], "%f", &load5)
		fmt.Sscanf(ld[2], "%f", &load15)
	}
	ncores := int64(1)
	fmt.Sscanf(strings.TrimSpace(blocks["NPROC"]), "%d", &ncores)
	if ncores <= 0 {
		ncores = 1
	}
	loadPct := load1 / float64(ncores) * 100
	if loadPct > 100 {
		loadPct = 100
	}
	return map[string]any{
		"cpu": round1(cpuPct),
		"memory": map[string]any{
			"percent": round1(memPct), "total": memTotal, "used": memUsed, "available": memAvail,
		},
		"storage": storage,
		"load": map[string]any{
			"percent": round1(loadPct), "load1": round2(load1), "load5": round2(load5), "load15": round2(load15),
		},
		"temps": remoteTemps(blocks),
	}
}

func (h *MetricsHub) remoteNetBytes() (uint64, uint64) {
	blocks := fetchRemoteBlocks(false)
	var sent, recv uint64
	for _, line := range strings.Split(blocks["NET"], "\n") {
		idx := strings.Index(line, ":")
		if idx < 0 {
			continue
		}
		fields := strings.Fields(line[idx+1:])
		if len(fields) < 9 {
			continue
		}
		var r, s uint64
		fmt.Sscanf(fields[0], "%d", &r)
		fmt.Sscanf(fields[8], "%d", &s)
		recv += r
		sent += s
	}
	return sent, recv
}

func (h *MetricsHub) remoteNetwork() map[string]any {
	sent, recv := h.remoteNetBytes()
	now := time.Now()
	h.mu.Lock()
	prevSent, prevRecv := h.rNet.NetOut, h.rNet.NetIn
	prevAt := h.rNet.At
	h.rNet = ioCounters{At: now, NetOut: sent, NetIn: recv}
	h.mu.Unlock()
	elapsed := now.Sub(prevAt).Seconds()
	if prevAt.IsZero() || elapsed < 0.001 {
		elapsed = 1
	}
	up := float64(int64(sent)-int64(prevSent)) / elapsed
	down := float64(int64(recv)-int64(prevRecv)) / elapsed
	if up < 0 {
		up = 0
	}
	if down < 0 {
		down = 0
	}
	return map[string]any{
		"timestamp": now.UnixMilli(), "upload": up, "download": down,
		"total_sent": sent, "total_recv": recv,
	}
}

func (h *MetricsHub) remoteDiskIO() map[string]any {
	blocks := fetchRemoteBlocks(false)
	var read, write uint64
	for _, line := range strings.Split(blocks["DISK"], "\n") {
		f := strings.Fields(line)
		if len(f) < 2 {
			continue
		}
		var r, w uint64
		fmt.Sscanf(f[0], "%d", &r)
		fmt.Sscanf(f[1], "%d", &w)
		read += r * 512
		write += w * 512
	}
	now := time.Now()
	h.mu.Lock()
	prevRead, prevWrite := h.rDisk.DiskR, h.rDisk.DiskW
	prevAt := h.rDisk.At
	h.rDisk = ioCounters{At: now, DiskR: read, DiskW: write}
	h.mu.Unlock()
	elapsed := now.Sub(prevAt).Seconds()
	if prevAt.IsZero() || elapsed < 0.001 {
		elapsed = 1
	}
	rs := float64(int64(read)-int64(prevRead)) / elapsed
	ws := float64(int64(write)-int64(prevWrite)) / elapsed
	if rs < 0 {
		rs = 0
	}
	if ws < 0 {
		ws = 0
	}
	return map[string]any{"timestamp": now.UnixMilli(), "read": rs, "write": ws}
}

func (h *MetricsHub) remoteInfo() map[string]any {
	blocks := fetchRemoteBlocks(false)
	uname := strings.Split(blocks["UNAME"], "\n")
	get := func(i int) string {
		if i < len(uname) {
			return strings.TrimSpace(uname[i])
		}
		return ""
	}
	processor := ""
	if line := strings.TrimSpace(blocks["CPUINFO"]); line != "" {
		if idx := strings.Index(line, ":"); idx >= 0 {
			processor = strings.TrimSpace(line[idx+1:])
		}
	}
	ncores := int64(1)
	fmt.Sscanf(strings.TrimSpace(blocks["NPROC"]), "%d", &ncores)
	if ncores <= 0 {
		ncores = 1
	}
	btime := int64(0)
	for _, line := range strings.Split(blocks["BTIME"], "\n") {
		f := strings.Fields(line)
		if len(f) >= 2 && f[0] == "btime" {
			fmt.Sscanf(f[1], "%d", &btime)
		}
	}
	boot := time.Now()
	if btime > 0 {
		boot = time.Unix(btime, 0)
	}
	uptime := int64(0)
	if btime > 0 {
		uptime = time.Now().Unix() - btime
	}
	return map[string]any{
		"hostname": strings.TrimSpace(blocks["NAME"]), "system": defaultString(get(0), "Linux"),
		"release": get(1), "version": get(2), "machine": get(3), "processor": processor,
		"python_version": "go", "cpu_count": ncores, "cpu_count_physical": ncores,
		"boot_time": boot.Format("2006-01-02T15:04:05"), "uptime_seconds": uptime,
	}
}

func platformName(osName string) string {
	switch strings.ToLower(osName) {
	case "linux":
		return "Linux"
	case "windows":
		return "Windows"
	case "darwin":
		return "Darwin"
	}
	return osName
}

func round1(v float64) float64 { return float64(int64(v*10+0.5)) / 10 }

func maxi(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func maxu(a, b uint64) uint64 {
	if a > b {
		return a
	}
	return b
}

// Snapshot 返回最近一次采集缓存（无缓存时返回 nil）。
func (h *MetricsHub) Snapshot() map[string]any {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.cache
}

// AddClient 注册一个 WebSocket 客户端并回放缓存。
func (h *MetricsHub) AddClient(c *wsClient) {
	h.mu.Lock()
	h.clients[c] = struct{}{}
	cache := h.cache
	h.mu.Unlock()
	if cache != nil {
		_ = c.sendJSON(map[string]any{"type": "metrics", "data": cache})
	}
}

// RemoveClient 移除客户端。
func (h *MetricsHub) RemoveClient(c *wsClient) {
	h.mu.Lock()
	delete(h.clients, c)
	h.mu.Unlock()
}

func (c *wsClient) sendJSON(v any) error {
	data, err := json.Marshal(v)
	if err != nil {
		return err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.conn.WriteText(data)
}

// StartMetricsBackground 启动指标采集与历史落盘协程。
func StartMetricsBackground() {
	// 预热 CPU 采样基线，避免首帧恒为 0。
	_, _ = cpu.Percent(0, false)
	metricsHub.mu.Lock()
	metricsHub.ioRefreshLocked()
	metricsHub.mu.Unlock()

	go func() {
		failSleep := 5 * time.Second
		for {
			success := false
			var snapshot map[string]any
			func() {
				defer func() { _ = recover() }()
				readNodeScope(func() { snapshot = metricsHub.CollectAll() })
				success = true
			}()
			if success {
				metricsHub.mu.Lock()
				metricsHub.cache = snapshot
				metricsHub.fails = 0
				metricsHub.mu.Unlock()
				RecordMetricSample(snapshot)
				metricsHub.broadcast(map[string]any{"type": "metrics", "data": snapshot})
				failSleep = 5 * time.Second
				time.Sleep(2 * time.Second)
			} else {
				metricsHub.mu.Lock()
				metricsHub.fails++
				fails := metricsHub.fails
				metricsHub.mu.Unlock()
				metricsHub.broadcast(map[string]any{
					"type":   "unavailable",
					"reason": "当前管理节点数据采集失败（连续 " + itoa(fails) + " 次），请检查节点连通性或在「多节点管理」中切换主机",
				})
				time.Sleep(failSleep)
				failSleep *= 2
				if failSleep > 30*time.Second {
					failSleep = 30 * time.Second
				}
			}
			metricsHub.mu.Lock()
			metricsHub.mu.Unlock()
		}
	}()

	go func() {
		for {
			time.Sleep(metricsFlushInterval)
			func() {
				defer func() { _ = recover() }()
				FlushMetrics()
			}()
		}
	}()
}

func (h *MetricsHub) broadcast(v any) {
	h.mu.Lock()
	clients := make([]*wsClient, 0, len(h.clients))
	for c := range h.clients {
		clients = append(clients, c)
	}
	h.mu.Unlock()
	dead := []*wsClient{}
	for _, c := range clients {
		if err := c.sendJSON(v); err != nil {
			dead = append(dead, c)
		}
	}
	for _, c := range dead {
		h.RemoveClient(c)
		_ = c.conn.Close()
	}
}

// InstallCheck 检测是否以 README 要求的完整宿主机模式运行。
func InstallCheck() map[string]any {
	if !isRunningInContainer() {
		return map[string]any{"ok": true, "container": false, "mode": "host", "missing": []string{}}
	}
	missing := []string{}
	hostRoot := GetConfig().HostRoot
	if hostRoot == "" {
		missing = append(missing, "host_root")
	} else if !Exists(hostRoot + "/etc/passwd") {
		missing = append(missing, "host_mount")
	}
	if !Exists("/var/run/docker.sock") {
		missing = append(missing, "docker_sock")
	}
	if strings.TrimSpace(readFileTrim("/proc/1/comm")) != "systemd" {
		missing = append(missing, "pid_host")
	}
	if capeff()&(1<<21) == 0 {
		missing = append(missing, "privileged")
	}
	if strings.TrimSpace(os.Getenv("GRAW_HOST_DATA")) == "" {
		missing = append(missing, "host_data")
	}
	return map[string]any{"ok": len(missing) == 0, "container": true, "mode": "docker", "missing": missing}
}

func isRunningInContainer() bool {
	if Exists("/.dockerenv") || Exists("/run/.containerenv") {
		return true
	}
	content := readFileTrim("/proc/1/cgroup")
	for _, kw := range []string{"docker", "kubepods", "libpod", "podman"} {
		if strings.Contains(content, kw) {
			return true
		}
	}
	return false
}

func readFileTrim(path string) string {
	b, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(b))
}

func capeff() int64 {
	for _, line := range strings.Split(readFileTrim("/proc/1/status"), "\n") {
		if strings.HasPrefix(line, "CapEff:") {
			fields := strings.Fields(line)
			if len(fields) >= 2 {
				n, _ := parseHex(fields[1])
				return n
			}
		}
	}
	return 0
}

func parseHex(s string) (int64, error) {
	var n int64
	for _, c := range s {
		n <<= 4
		switch {
		case c >= '0' && c <= '9':
			n += int64(c - '0')
		case c >= 'a' && c <= 'f':
			n += int64(c-'a') + 10
		case c >= 'A' && c <= 'F':
			n += int64(c-'A') + 10
		default:
			return 0, errInvalidHex
		}
	}
	return n, nil
}

var errInvalidHex = &HTTPError{Status: 500, Detail: "invalid hex"}

// SelfAppVersion 读取容器镜像版本（GRAW_VERSION 环境变量），否则返回内置版本。
func SelfAppVersion() string {
	if v := strings.TrimSpace(os.Getenv("GRAW_VERSION")); v != "" {
		return v
	}
	return AppVersion
}
