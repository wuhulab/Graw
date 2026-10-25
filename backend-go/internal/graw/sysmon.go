package graw

// sysmon.go —— 系统指标采集中心。
//
// 职责：为「系统监控」窗口和 /api/system/* 提供 CPU / 内存 / 磁盘 / 负载 / 温度 / 网络 / IO 快照，
// 由 MetricsHub 缓存最近一帧并广播给 /api/system/ws 订阅者；StartMetricsBackground 负责常驻采集循环
// （约 2 秒一帧），每帧顺带把采样交给 metrics_store.go 入历史缓冲。
//
// 采集流程：overview / network / diskio / info 四个子采集合并成一次 CollectAll。
//   - 本机：直接用 gopsutil 读内核计数器；容器模式下磁盘路径经 HostPath 映射到宿主机根。
//   - 远程 SSH 节点：gopsutil 读不到别的机器，改为「一条 shell 脚本一次 SSH 取回全部 /proc 数据」
//     再由本地解析（fetchRemoteBlocks），4 秒内复用缓存，避免每帧多次连远端。
//
// 安全/性能注意：
//   - 网络/磁盘 IO 是累计字节数的差分，依赖上一次采样时间基线；首次或切换节点时速率从 0 起算。
//   - 远端脚本只读 /proc、/sys，不做写操作；解析失败降级为 0 值而不是让整帧失败。
//   - MetricsHub.mu 同时保护客户端集合、缓存与差分母线；广播先拷出客户端列表再发送，避免慢连接占锁。

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

// ioCounters 是一次计数器基线：At 为采样时刻，其余为网卡/磁盘累计字节数。
// 速率 = (本次累计 - 上次累计) / (本次 At - 上次 At)，网络与磁盘各持一份；
// 本机用 lastNet/lastDisk，远程节点用 rNet/rDisk，两套基线互不干扰。
type ioCounters struct {
	At     time.Time
	NetIn  uint64
	NetOut uint64
	DiskR  uint64
	DiskW  uint64
}

// MetricsHub 负责统一采集、广播与缓存系统指标。
// mu 同时保护 clients/cache/fails 与四条差分母线；cache 保存最近一次成功采集的整帧，
// 供 Snapshot 与新增 WS 客户端回放，避免新连上来的窗口先看到空白。
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

// metricsHub 是进程级单例。clients 必须初始化：AddClient 直接向该 map 写入，nil map 会 panic。
var metricsHub = &MetricsHub{clients: map[*wsClient]struct{}{}}

// wsClient 是一个监控 WS 订阅者；mu 让同一连接上的并发写入（广播/回放）串行，避免交叉帧。
type wsClient struct {
	conn *websocketConn
	mu   sync.Mutex
}

// websocketConn 是 ws.go 中 wsConn 的别名，为避免在 sysmon 中暴露 gorilla/websocket 类型而设。
// 指向同一类型，因此这里可直接调用 WriteText / Close。
type websocketConn = wsConn

// overview 采集 CPU/内存/磁盘/负载/温度，返回概览卡片所需的合并 map。
// 当前管理节点是裸 SSH 主机时转 remoteOverview；否则全部用 gopsutil 读本机。
// 各子项采集失败时降级为 0 值，保证接口始终返回完整结构，前端不必处理缺字段。
func (h *MetricsHub) overview() map[string]any {
	// 远程节点：本机 gopsutil 读不到对端数据，改走一次 SSH 脚本。
	if remoteActive() {
		return h.remoteOverview()
	}
	// 0 表示与上次调用比较（非阻塞瞬时值），因此启动时需先预热一次基线，否则首帧为 0。
	cpuPct := 0.0
	if vals, err := cpu.Percent(0, false); err == nil && len(vals) > 0 {
		cpuPct = vals[0]
	}
	// Available 已扣除可回收缓存，比 Free 更贴近「用户真正可用」的内存。
	m, err := mem.VirtualMemory()
	memTotal, memAvail, memUsed, memPct := uint64(0), uint64(0), uint64(0), 0.0
	if err == nil {
		memTotal = m.Total
		memAvail = m.Available
		memUsed = m.Total - m.Available
		// total 为 0（极少数虚拟环境）时用 1 兜底，避免除零得到 NaN。
		memPct = float64(memUsed) / float64(maxu(memTotal, 1)) * 100
	}
	// 容器内根路径要经 HostPath 映射到挂载的宿主机根；Windows 主机直接看 C: 盘。
	diskPath := "/"
	if isWindows() {
		diskPath = `C:\`
	} else {
		diskPath = HostPath("/")
	}
	// 采集失败时保持 0 值结构，前端显示 0% 而不是报错。
	storage := map[string]any{"percent": 0.0, "total": uint64(0), "used": uint64(0), "free": uint64(0)}
	if du, err := disk.Usage(diskPath); err == nil {
		storage = map[string]any{
			"percent": round1(du.UsedPercent), "total": du.Total, "used": du.Used, "free": du.Free,
		}
	}
	// Counts(true) 取逻辑核（含超线程）；取不到才退回 runtime.NumCPU()。
	ncpu := runtime.NumCPU()
	if n, err := cpu.Counts(true); err == nil && n > 0 {
		ncpu = n
	}
	// load.Avg() 在 Windows 等平台不可用，此时用 CPU 使用率折算 load1，保证卡片仍有数值。
	load1, load5, load15 := cpuPct/100*float64(ncpu), 0.0, 0.0
	if la, err := load.Avg(); err == nil {
		load1, load5, load15 = la.Load1, la.Load5, la.Load15
	}
	// 换成「每核负载百分比」并封顶 100，避免多核机器把负载显示成几百。
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

// network 计算本机网络收发速率（字节/秒）与累计收发量。
// 速率为两次采样之差除以实际间隔；计数器回绕或节点切换导致负增量时统一归零。
func (h *MetricsHub) network() map[string]any {
	if remoteActive() {
		return h.remoteNetwork()
	}
	var sent, recv uint64
	// false 表示把全部网卡汇总为一条记录，与前端只展示一条总速率的模型一致。
	if counters, err := gnet.IOCounters(false); err == nil && len(counters) > 0 {
		sent = counters[0].BytesSent
		recv = counters[0].BytesRecv
	}
	now := time.Now()
	// 临界区只做「读旧基线 + 写新基线」，把 syscall 等耗时操作留在锁外。
	h.mu.Lock()
	prevSent, prevRecv := h.lastNet.NetOut, h.lastNet.NetIn
	prevAt := h.lastNet.At
	h.lastNet = ioCounters{At: now, NetOut: sent, NetIn: recv}
	h.mu.Unlock()
	elapsed := now.Sub(prevAt).Seconds()
	// 首次采样（零值时间）或间隔过小时按 1 秒兜底，防止除零和瞬时尖峰。
	if prevAt.IsZero() || elapsed < 0.001 {
		elapsed = 1
	}
	up := float64(int64(sent)-int64(prevSent)) / elapsed
	down := float64(int64(recv)-int64(prevRecv)) / elapsed
	// 32 位计数器回绕或换了管理节点都会得到负差值，按 0 处理。
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

// diskio 计算本机磁盘读写速率（字节/秒），做法与 network 一致：累计计数差分。
// 遍历所有块设备累加读写字节；采集失败时计数为 0，速率随之归零。
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
// 采集循环启动前先跑一次，让第一帧 network/diskio 就有有效的 prevAt；
// 否则首帧只会走 1 秒兜底、把「开机以来的累计流量」当成 1 秒内的速率。
// 读不到计数器时也会写入当前时刻，后续差分仍从新基线算起。
func (h *MetricsHub) ioRefreshLocked() {
	now := time.Now()
	if counters, err := gnet.IOCounters(false); err == nil && len(counters) > 0 {
		h.lastNet = ioCounters{At: now, NetOut: counters[0].BytesSent, NetIn: counters[0].BytesRecv}
	} else {
		// 极简容器里可能没有网卡计数，也记下时间戳，避免后续 elapsed 落在零值分支。
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

// info 返回主机基础信息：主机名/系统/内核/CPU 型号/核数/启动时间/运行时长。
// 远程节点转 remoteInfo；本机 gopsutil 调用失败时用 runtime.GOOS/GOARCH 兜底，保证字段非空。
func (h *MetricsHub) info() map[string]any {
	if remoteActive() {
		return h.remoteInfo()
	}
	// host.Info 失败时保留零值，下面用 runtime 常量补齐系统名与架构。
	info, _ := host.Info()
	// logical 为逻辑核（含超线程），physical 为物理核；两者都先退回 NumCPU，再尝试精确值。
	logical := runtime.NumCPU()
	physical := runtime.NumCPU()
	if n, err := cpu.Counts(true); err == nil {
		logical = n
	}
	if n, err := cpu.Counts(false); err == nil && n > 0 {
		physical = n
	}
	// 取第一颗 CPU 的型号作为展示名（多路机器也只显示一组型号）。
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
		// 字段名沿用 Python 旧版契约，值为 Go 运行时版本；远程节点固定返回 "go"。
		"python_version": runtime.Version(), "cpu_count": logical,
		"cpu_count_physical": physical,
		"boot_time":          bootTime.Format("2006-01-02T15:04:05"),
		"uptime_seconds":     uptime,
	}
}

// CollectAll 采集四项合并指标（overview/network/diskio/info），是采集循环的唯一入口。
// 远程节点先强制刷新一次远端脚本结果，四个子采集共享同一次 SSH 输出，避免 4 倍连接开销。
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

// collectTemps 读取本机传感器温度，过滤非法值并最多返回 8 条。
// 只保留 0~150℃ 的读数；同名传感器去重；CPU 相关项排在前面，便于前端优先展示。
// 容器或无 hwmon 的机器上 sensors 常返回错误/空列表，此时返回空切片而非 nil（JSON 序列化为 []）。
func collectTemps() []map[string]any {
	out := []map[string]any{}
	seen := map[string]bool{}
	if list, err := sensors.SensorsTemperatures(); err == nil {
		for _, s := range list {
			temp := s.Temperature
			// 0 或 >150℃ 多是传感器缺失/未校准的占位值，直接丢弃。
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
			// 按名称关键字粗分 CPU / 其他，前端据此决定用不同颜色展示。
			kind := "other"
			low := strings.ToLower(name)
			for _, kw := range []string{"cpu", "core", "package", "k10temp", "coretemp", "tdie", "soc", "tctl"} {
				if strings.Contains(low, kw) {
					kind = "cpu"
					break
				}
			}
			// 截断超长传感器名（部分 hwmon 路径很长），避免撑爆前端卡片。
			if len(name) > 48 {
				name = name[:48]
			}
			out = append(out, map[string]any{
				"name": name, "value": round1(temp),
				"kind": kind, "source": s.SensorKey,
			})
		}
	}
	// CPU 类优先；同类按名称排序，保证每次刷新顺序稳定（避免图表/列表跳动）。
	sort.SliceStable(out, func(i, j int) bool {
		ki, kj := out[i]["kind"].(string), out[j]["kind"].(string)
		if ki != kj {
			return ki == "cpu"
		}
		return out[i]["name"].(string) < out[j]["name"].(string)
	})
	// 传感器可能几十个，只保留前 8 条控制 payload 大小。
	if len(out) > 8 {
		out = out[:8]
	}
	return out
}

// ---------------------------------------------------------------------------
// 裸 SSH 远端指标采集：单次连接执行一段脚本，取回 CPU/内存/磁盘/负载/网络/IO/温度。
// ---------------------------------------------------------------------------
// remoteMetricsScript 是裸 SSH 节点的「一次连接取全部指标」脚本。
// 用 ===BLOCKxxx=== 标记分隔各 /proc、/sys 数据块，stdout 由 fetchRemoteBlocks 按标记切分解析。
// 关键设计：
//   - STAT0/STAT1 之间 sleep 0.12s：CPU 使用率必须靠两次 /proc/stat 差分，间隔太短误差大，
//     太长又会拖慢整帧；0.12s 是精度与耗时的折中。
//   - 所有读取都带 2>/dev/null 与 || continue：远端缺文件/无权限时静默降级，不让整段脚本中断。
//   - 纯只读操作、只用 POSIX sh 与 awk：兼容 BusyBox（Alpine）等精简系统，不依赖 python/jq。
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
echo "===BLOCKDISK==="; cat /proc/diskstats 2>/dev/null | awk '{print $3, $6, $10}'
echo "===BLOCKNAME==="; hostname 2>/dev/null | head -1
echo "===BLOCKUNAME==="; uname -s 2>/dev/null; uname -r 2>/dev/null; uname -v 2>/dev/null; uname -m 2>/dev/null
echo "===BLOCKCPUINFO==="; grep 'model name' /proc/cpuinfo 2>/dev/null | head -1
echo "===BLOCKCORES==="; awk '/^physical id/{p=$4} /^core id/{print p"-"$4}' /proc/cpuinfo 2>/dev/null | sort -u | wc -l
echo "===BLOCKHWMON==="; for d in /sys/class/hwmon/hwmon*; do [ -d "$d" ] || continue; n=$(cat "$d/name" 2>/dev/null); for f in "$d"/temp*_input; do [ -f "$f" ] || continue; b=${f%_input}; echo "$n|$(cat "${b}_label" 2>/dev/null)|$(cat "$f" 2>/dev/null)"; done; done
echo "===BLOCKTHERMAL==="; for z in /sys/class/thermal/thermal_zone*; do [ -d "$z" ] || continue; echo "$(cat "$z/type" 2>/dev/null)|$(cat "$z/temp" 2>/dev/null)"; done
`

// 远端指标块缓存按节点 ID 分开保存：同一面板可能管理多台子节点，避免彼此数据串味。
// remoteBlocksAt 记录取回时刻，TTL 见 fetchRemoteBlocks；整个「读缓存→执行→写缓存」都在锁内完成。
var (
	remoteBlocksMu    sync.Mutex
	remoteBlocksCache = map[string]map[string]string{}
	remoteBlocksAt    = map[string]time.Time{}
)

// fetchRemoteBlocks 在当前节点执行远端采集脚本，把输出切成「块名 → 文本」返回。
// force=true 时忽略缓存（采集循环每帧刷新一次）；false 时 4 秒内复用缓存，
// 让分开调用的 overview/network/diskio/info 接口共享同一次 SSH 结果，而不是各连一次远端。
// 执行失败（超时/SSH 不可达）仍返回空 map，调用方解析出 0 值，避免整帧监控失败。
func fetchRemoteBlocks(force bool) map[string]string {
	nodeID := resolvedNodeID()
	// 整个流程串行化：并发请求同时未命中缓存时，也只会有一个真正去连远端。
	remoteBlocksMu.Lock()
	defer remoteBlocksMu.Unlock()
	if !force {
		// TTL 略大于前端 2 秒推送间隔：正常刷屏能命中缓存，数据又不会太旧。
		if cache, ok := remoteBlocksCache[nodeID]; ok && time.Since(remoteBlocksAt[nodeID]) < 4*time.Second {
			return cache
		}
	}
	// 20 秒超时：脚本内含 0.12s sleep，给慢速链路/高负载机器留足余量。
	res := HostShell(20*time.Second, remoteMetricsScript)
	blocks := map[string]string{}
	cur := ""
	buf := []string{}
	for _, line := range strings.Split(res.Stdout, "\n") {
		// 按 ===BLOCKxxx=== 标记切块；标记行之前的输出（登录 banner 等）落在空块名里被丢弃。
		if strings.HasPrefix(line, "===BLOCK") && strings.HasSuffix(line, "===") {
			if cur != "" {
				blocks[cur] = strings.TrimSpace(strings.Join(buf, "\n"))
			}
			// 去掉前缀与尾部等号，得到 STAT0 / MEM / DF 这类短名。
			cur = strings.Trim(strings.TrimPrefix(line, "===BLOCK"), "=")
			buf = []string{}
			continue
		}
		buf = append(buf, line)
	}
	if cur != "" {
		blocks[cur] = strings.TrimSpace(strings.Join(buf, "\n"))
	}
	// 先写数据再登记时间戳，两者都在锁内，读侧不会看到「有时间戳却没有数据」的中间态。
	remoteBlocksCache[nodeID] = blocks
	remoteBlocksAt[nodeID] = time.Now()
	return blocks
}

// parseProcStatLine 解析 /proc/stat 首行（形如 "cpu  user nice system idle ..."），
// 返回总 jiffies 与 idle jiffies。字段顺序中第 4 个（索引 3）为 idle，总量按全部字段求和。
// 格式异常或字段不足时返回 (0,0)，调用方据此跳过本次差分（结果显示 0% 而不是乱跳）。
func parseProcStatLine(line string) (int64, int64) {
	fields := strings.Fields(line)
	if len(fields) < 5 {
		return 0, 0
	}
	total, idle := int64(0), int64(0)
	// fields[1:] 跳过 "cpu" 标签；i==3 即原始第 5 列 idle（不含 iowait）。
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

// remoteTemps 把远端 HWMON/THERMAL 块解析成温度列表（结构与 collectTemps 保持一致）。
// 优先用 hwmon 的 temp*_input（带 label 更可读），一个都没有时才退回 thermal_zone。
// hwmon 行格式为 "chip|label|毫摄氏度"，thermal 行格式为 "type|毫摄氏度"。
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
		// 内核温度单位是毫摄氏度，统一换算成摄氏度再展示。
		value := milli / 1000
		if value <= 0 || value >= 150 {
			continue
		}
		// 有 label 时用 label，否则退回 chip 名（如 k10temp / coretemp）。
		name := strings.TrimSpace(parts[1])
		if name == "" {
			name = strings.TrimSpace(parts[0])
		}
		if name == "" || seen[name] {
			continue
		}
		seen[name] = true
		// chip 名与 label 一起参与关键字匹配，提升 CPU 温度识别率。
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
	// hwmon 一条都没有时退回 thermal_zone（部分 VPS/精简内核没有 hwmon 目录）。
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
	// 与 collectTemps 相同：CPU 类优先、同类按名排序、最多 8 条。
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

// remoteOverview 用远端脚本块拼出与本机 overview 完全相同的结构，供前端无差别渲染。
// CPU 用两次 /proc/stat 差分；内存与 df 的数值都是 kB，需 ×1024 转成字节。
// 缓存 false 表示与其它子采集共享 4 秒内的同一次 SSH 结果。
func (h *MetricsHub) remoteOverview() map[string]any {
	blocks := fetchRemoteBlocks(false)
	t0, i0 := parseProcStatLine(blocks["STAT0"])
	t1, i1 := parseProcStatLine(blocks["STAT1"])
	cpuPct := 0.0
	// 两次采样之间必须真的产生了 tick（t1>t0），否则无法算利用率，给 0 避免 NaN。
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
	// /proc/meminfo 的单位是 kB，×1024 才是字节，与前端口径一致。
	memTotal *= 1024
	memAvail *= 1024
	// 老内核没有 MemAvailable，退化成「全部可用」，否则会算出接近 100% 的假占用。
	if memAvail == 0 {
		memAvail = memTotal
	}
	memUsed := memTotal - memAvail
	memPct := 0.0
	if memTotal > 0 {
		memPct = float64(memUsed) / float64(memTotal) * 100
	}
	storage := map[string]any{"percent": 0.0, "total": int64(0), "used": int64(0), "free": int64(0)}
	// df -kP 的列依次为：文件系统 总 已用 可用 使用率 挂载点，数值以 1K 块为单位。
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
	// loadavg 前三个浮点数即 1/5/15 分钟负载。
	load1, load5, load15 := 0.0, 0.0, 0.0
	ld := strings.Fields(blocks["LOAD"])
	if len(ld) >= 3 {
		fmt.Sscanf(ld[0], "%f", &load1)
		fmt.Sscanf(ld[1], "%f", &load5)
		fmt.Sscanf(ld[2], "%f", &load15)
	}
	// nproc 取不到时按 1 核算，防止 loadPct 除零。
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

// remoteNetBytes 汇总 /proc/net/dev 各接口的收发累计字节，返回 (sent, recv)。
// 行格式为 "iface: 收字节 收包 ... 发字节 ..."：字段 0 是接收、字段 8 是发送。
// 未过滤 lo 等接口，与本机 gopsutil 汇总全部网卡的口径保持一致。
func (h *MetricsHub) remoteNetBytes() (uint64, uint64) {
	blocks := fetchRemoteBlocks(false)
	var sent, recv uint64
	for _, line := range strings.Split(blocks["NET"], "\n") {
		// 冒号前是接口名（名字里可能带空格/点，用首个冒号切分最稳）。
		idx := strings.Index(line, ":")
		if idx < 0 {
			continue
		}
		fields := strings.Fields(line[idx+1:])
		// 每行 16 个数值字段，少于 9 个说明该行残缺，跳过。
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

// remoteNetwork 与 network 等价，只是改用远端累计字节和独立的 rNet 基线。
// 依赖 fetchRemoteBlocks 的 4 秒缓存，因此与同帧的其它子采集不会重复 SSH。
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

// remoteDiskIO 解析 /proc/diskstats 的扇区计数并换算成与本机一致的读写速率。
// 脚本已 awk 只取第 3、6、10 列（设备名、读/写扇区数），每扇区固定 512 字节。
// diskstats 同时包含整盘与其分区的行，直接累加会重复计数，
// 因此先剔除「名字以某个整盘名 + 数字结尾」的分区行（sda1 / nvme0n1p1 / mmcblk0p1）。
func (h *MetricsHub) remoteDiskIO() map[string]any {
	blocks := fetchRemoteBlocks(false)
	lines := strings.Split(blocks["DISK"], "\n")
	// 第一遍：解析出每行的设备名与累计扇区数。
	type diskRow struct {
		name        string
		readSector  uint64
		writeSector uint64
	}
	rows := make([]diskRow, 0, len(lines))
	names := map[string]bool{}
	for _, line := range lines {
		f := strings.Fields(line)
		if len(f) < 3 {
			continue
		}
		var r, w uint64
		if _, err := fmt.Sscanf(f[1]+" "+f[2], "%d %d", &r, &w); err != nil {
			continue
		}
		rows = append(rows, diskRow{name: f[0], readSector: r, writeSector: w})
		names[f[0]] = true
	}
	read, write := uint64(0), uint64(0)
	for _, row := range rows {
		if isDiskPartition(row.name, names) {
			continue
		}
		// 内核暴露的是 512 字节扇区数，×512 得到字节。
		read += row.readSector * 512
		write += row.writeSector * 512
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

// isDiskPartition 判断设备名是否是同一份 diskstats 里某个整盘设备的分区。
// 分区名规则：整盘名 + 数字（sda1、mmcblk0p1）；nvme/mmcblk 还会多一个 p 分隔符。
// 判断依据是「另一个已出现的设备名做前缀，剩余部分是纯数字（可带 p）」，不依赖固定命名。
func isDiskPartition(name string, names map[string]bool) bool {
	for full := range names {
		if full == name || len(full) >= len(name) || !strings.HasPrefix(name, full) {
			continue
		}
		suffix := strings.TrimPrefix(strings.TrimPrefix(name, full), "p")
		if suffix == "" {
			continue
		}
		digits := true
		for _, c := range suffix {
			if c < '0' || c > '9' {
				digits = false
				break
			}
		}
		if digits {
			return true
		}
	}
	return false
}

// remoteInfo 用 UNAME/NPROC/CPUINFO/BTIME/CORES 块拼出与本机 info 相同的结构。
// cpu_count_physical 优先取 /proc/cpuinfo 里 physical id + core id 去重后的物理核数，
// 取不到（ARM 等无该字段的精简系统）时退回 nproc 的逻辑核数，避免字段缺失显示为 0。
func (h *MetricsHub) remoteInfo() map[string]any {
	blocks := fetchRemoteBlocks(false)
	// UNAME 四行依次为：kernel name / release / version / machine。
	uname := strings.Split(blocks["UNAME"], "\n")
	get := func(i int) string {
		if i < len(uname) {
			return strings.TrimSpace(uname[i])
		}
		return ""
	}
	// /proc/cpuinfo 的 model name 行形如 "model name\t: Intel(R) ..."，取冒号后内容。
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
	// 物理核数由脚本去重 physical id + core id 后统计；为 0（远端无该字段）时退回逻辑核数。
	physical := int64(0)
	fmt.Sscanf(strings.TrimSpace(blocks["CORES"]), "%d", &physical)
	if physical <= 0 || physical > ncores {
		physical = ncores
	}
	// btime 行形如 "btime 1700000000"，是开机时刻的 Unix 秒。
	btime := int64(0)
	for _, line := range strings.Split(blocks["BTIME"], "\n") {
		f := strings.Fields(line)
		if len(f) >= 2 && f[0] == "btime" {
			fmt.Sscanf(f[1], "%d", &btime)
		}
	}
	// 取不到 btime 时启动时间退回「现在」、运行时长 0，避免展示 1970 年。
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
		"python_version": "go", "cpu_count": ncores, "cpu_count_physical": physical,
		"boot_time": boot.Format("2006-01-02T15:04:05"), "uptime_seconds": uptime,
	}
}

// platformName 把 gopsutil 返回的 GOOS 风格系统名规范成前端展示用的首字母大写形式。
// 未知系统原样返回，避免丢失信息。
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

// round1 四舍五入保留 1 位小数（百分比/温度展示用）。
func round1(v float64) float64 { return float64(int64(v*10+0.5)) / 10 }

// maxi 返回两个 int 中较大者，用于核数兜底，防止后续除零。
func maxi(a, b int) int {
	if a > b {
		return a
	}
	return b
}

// maxu 返回两个 uint64 中较大者，用于内存总量兜底，防止百分比除零。
func maxu(a, b uint64) uint64 {
	if a > b {
		return a
	}
	return b
}

// Snapshot 返回最近一次采集缓存（无缓存时返回 nil）。
// 返回的 map 与内部 cache 共享引用，调用方只能读取，不要原地修改。
func (h *MetricsHub) Snapshot() map[string]any {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.cache
}

// AddClient 注册一个 WebSocket 客户端并回放缓存。
// 回放在锁外发送，避免写 WS（可能阻塞）时一直占着 h.mu 拖慢采集；发送失败不额外处理，
// 后续 broadcast 会把它当作死连接清理。
func (h *MetricsHub) AddClient(c *wsClient) {
	h.mu.Lock()
	h.clients[c] = struct{}{}
	cache := h.cache
	h.mu.Unlock()
	// 新窗口连上来先给一帧当前数据，避免等下一个采样周期才出图。
	if cache != nil {
		_ = c.sendJSON(map[string]any{"type": "metrics", "data": cache})
	}
}

// RemoveClient 移除客户端（连接本身由调用方负责关闭）。
func (h *MetricsHub) RemoveClient(c *wsClient) {
	h.mu.Lock()
	delete(h.clients, c)
	h.mu.Unlock()
}

// sendJSON 把消息序列化成 JSON 文本帧写出。
// c.mu 保证同一连接上的并发写入串行（广播与缓存回放可能同时发生），避免写出交叉帧。
func (c *wsClient) sendJSON(v any) error {
	data, err := json.Marshal(v)
	if err != nil {
		return err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.conn.WriteText(data)
}

// StartMetricsBackground 启动两个常驻协程：指标采集广播（约 2 秒一帧）与历史采样落盘（每 2 秒）。
// 由 main.go 在进程启动时调用一次；协程随进程退出结束，不做优雅停机。
// 采集循环带 panic 恢复：单个子采集 panic 只会把该帧判为失败，不会拖垮整个进程。
func StartMetricsBackground() {
	// 预热 CPU 采样基线，避免首帧恒为 0。
	_, _ = cpu.Percent(0, false)
	// 同一时刻预热网络/磁盘累计基线，使首帧速率可信（而不是把开机累计量当成 1 秒流量）。
	metricsHub.mu.Lock()
	metricsHub.ioRefreshLocked()
	metricsHub.mu.Unlock()

	go func() {
		// 失败退避从 5 秒起、按倍数增长、30 秒封顶；成功后重置。
		failSleep := 5 * time.Second
		for {
			success := false
			var snapshot map[string]any
			func() {
				// 任何 panic（解析越界等）都在此兜住，按「失败帧」处理。
				defer func() { _ = recover() }()
				// RLock 期间禁止 withNodeScope 改写当前节点，保证一帧数据全部来自同一节点。
				readNodeScope(func() { snapshot = metricsHub.CollectAll() })
				success = true
			}()
			if success {
				// 成功帧：先更新内存缓存，前端/新连接立刻能取到与广播一致的数据。
				metricsHub.mu.Lock()
				metricsHub.cache = snapshot
				metricsHub.fails = 0
				metricsHub.mu.Unlock()
				// 只入内存缓冲，真正落盘交给下方独立协程批量写，避免每帧同步 IO。
				RecordMetricSample(snapshot)
				// 广播给所有订阅者；发送失败的连接在 broadcast 内被摘除。
				metricsHub.broadcast(map[string]any{"type": "metrics", "data": snapshot})
				failSleep = 5 * time.Second
				// 正常节奏 2 秒一帧，与前端图表刷新频率匹配。
				time.Sleep(2 * time.Second)
			} else {
				metricsHub.mu.Lock()
				metricsHub.fails++
				fails := metricsHub.fails
				metricsHub.mu.Unlock()
				// 失败帧通知前端原因与连续失败次数，引导用户检查节点连通性。
				metricsHub.broadcast(map[string]any{
					"type":   "unavailable",
					"reason": "当前管理节点数据采集失败（连续 " + itoa(fails) + " 次），请检查节点连通性或在「多节点管理」中切换主机",
				})
				// 指数退避：远端不可达时避免每 2 秒打一次 SSH/采集。
				time.Sleep(failSleep)
				failSleep *= 2
				if failSleep > 30*time.Second {
					failSleep = 30 * time.Second
				}
			}
		}
	}()

	go func() {
		for {
			// 固定 2 秒批量刷一次，把多帧合并成一次 append 写入，降低磁盘抖动。
			time.Sleep(metricsFlushInterval)
			func() {
				// 落盘 panic 同样兜住，避免整个历史记录协程退出。
				defer func() { _ = recover() }()
				FlushMetrics()
			}()
		}
	}()
}

// broadcast 向所有订阅者发送同一帧。
// 先在锁内拷出客户端列表，再在锁外逐个发送，避免慢连接长期占着 h.mu 阻塞采集；
// 写失败的连接视为已断开，统一移出集合并关闭。
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

// InstallCheck 检测是否以 README 要求的完整宿主机模式运行（非容器内直接判为 ok）。
// 逐项检查：host_root 配置、宿主机根挂载、docker.sock、pid=host（/proc/1/comm 为 systemd）、
// CAP_SYS_ADMIN（CapEff 第 21 位）、GRAW_HOST_DATA 环境变量；返回缺失项供前端提示。
func InstallCheck() map[string]any {
	// 直接在宿主机上运行（非容器）时这些检查都不适用，视为完整模式。
	if !isRunningInContainer() {
		return map[string]any{"ok": true, "container": false, "mode": "host", "missing": []string{}}
	}
	missing := []string{}
	hostRoot := GetConfig().HostRoot
	if hostRoot == "" {
		missing = append(missing, "host_root")
	} else if !Exists(hostRoot + "/etc/passwd") {
		// 有配置但挂载里看不到宿主机 /etc/passwd，说明宿主机根没挂进来。
		missing = append(missing, "host_mount")
	}
	if !Exists("/var/run/docker.sock") {
		missing = append(missing, "docker_sock")
	}
	// pid: host 时 1 号进程应是宿主机的 systemd；否则看到的是容器自己的 entrypoint。
	if strings.TrimSpace(readFileTrim("/proc/1/comm")) != "systemd" {
		missing = append(missing, "pid_host")
	}
	// CapEff 第 21 位 = CAP_SYS_ADMIN，是 privileged 容器的标志性能力。
	if capeff()&(1<<21) == 0 {
		missing = append(missing, "privileged")
	}
	if strings.TrimSpace(os.Getenv("GRAW_HOST_DATA")) == "" {
		missing = append(missing, "host_data")
	}
	return map[string]any{"ok": len(missing) == 0, "container": true, "mode": "docker", "missing": missing}
}

// isRunningInContainer 通过标志文件与 /proc/1/cgroup 关键字判断是否运行在容器内。
// 覆盖 Docker / Kubernetes / libpod(Podman) 的常见写法；读不到任何证据时按非容器处理。
func isRunningInContainer() bool {
	// Docker 与 Podman/CRI-O 会留下标志文件，命中即可快速返回。
	if Exists("/.dockerenv") || Exists("/run/.containerenv") {
		return true
	}
	// cgroup v1/v2 里通常带运行时名字，是标志文件缺失时的兜底判断。
	content := readFileTrim("/proc/1/cgroup")
	for _, kw := range []string{"docker", "kubepods", "libpod", "podman"} {
		if strings.Contains(content, kw) {
			return true
		}
	}
	return false
}

// readFileTrim 读取小文件并去掉首尾空白；失败返回空串（调用方据此判空）。
func readFileTrim(path string) string {
	b, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(b))
}

// capeff 从 /proc/1/status 的 CapEff 行解析 1 号进程的有效能力位图（十六进制）。
// 读不到或格式异常返回 0，InstallCheck 据此报告缺少 privileged。
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

// parseHex 把十六进制字符串解析为 int64；遇到非十六进制字符返回 errInvalidHex。
// 手写逐位解析（而非 strconv）以便对不定长输入也保持行为稳定。
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

// errInvalidHex 是 parseHex 解析失败时的错误；capeff 会忽略它并当作「无该能力」。
var errInvalidHex = &HTTPError{Status: 500, Detail: "invalid hex"}

// SelfAppVersion 读取容器镜像版本（GRAW_VERSION 环境变量），否则返回内置版本。
func SelfAppVersion() string {
	if v := strings.TrimSpace(os.Getenv("GRAW_VERSION")); v != "" {
		return v
	}
	return AppVersion
}
