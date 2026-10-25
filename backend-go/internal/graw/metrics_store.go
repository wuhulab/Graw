package graw

// metrics_store.go —— 历史监控采样存储（NDJSON 按天分文件）。
//
// 数据流：采集协程每帧调用 RecordMetricSample，把合并指标压成一行紧凑字段放进内存缓冲；
// FlushMetrics 每 2 秒批量追加写入 data/metrics/YYYY-MM-DD.ndjson；查询侧读取相关天的文件后
// 按时间过滤、（可选）按 bucket 秒聚合平均。
//
// 设计要点：
//   - 按天一个文件而不是单文件：避免长期运行后单文件无限增长，也让「按时间范围只读相关天」成为可能；
//     文件名即日期，清理时按名字排序即可（cleanupMetricsFiles，保留约 7 天）。
//   - 先入内存缓冲再批量落盘：每帧同步写盘会把磁盘抖动带进采集循环；批量 append 只做一次 open/write。
//   - 写盘失败把 batch 放回缓冲头部重试，宁可在内存里多攒一点，也不丢采样点。
//   - 目录 0700、文件 0600：指标不含凭据，但会暴露主机负载/流量画像，沿用 data 目录的收紧权限。
//
// 注意：分天与查询边界都按「本地时区日期」划分，跨时区改系统时间会让采样归属到另一天。

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

// metricsRetentionDays 历史采样保留天数（默认 7 天），查询接口会把它回传前端做提示。
// 清理时实际删除「早于 7+1 天」的文件（见 cleanupMetricsFiles），多留一天做跨日缓冲。
const metricsRetentionDays = 7

// 存储全局状态，由 metricsStoreMu 保护：
//   - metricsPending 是待落盘的内存缓冲，上限 1200 行（约 2 秒一帧 × 40 分钟），防止落盘长期失败时无限增长；
//   - metricsFlushInterval 是批量落盘周期，与采集循环的 2 秒节奏对齐。
//
// 落盘失败由 FlushMetrics 的 OpenFile 错误直接感知并把批次放回缓冲，
// 这里不再维护额外的「磁盘可写」标志，避免出现永远为 true 的假检测。
var (
	metricsStoreMu       sync.Mutex
	metricsPending       []map[string]any
	metricsFlushInterval = 2 * time.Second
)

// metricsDayPath 返回某天（YYYY-MM-DD）的 NDJSON 文件路径，目录来自配置的 MetricsDir。
func metricsDayPath(day string) string {
	return filepath.Join(GetConfig().MetricsDir, day+".ndjson")
}

// cleanupMetricsFiles 删除超过保留期的按天文件，并在目录不存在时按 0700 补齐。
// 用文件名前 10 字符（日期）做字符串比较，不解析时间；非 .ndjson 或名字过短的文件跳过。
// 读目录失败静默返回：清理是尽力而为，不能影响采集与查询主流程。
func cleanupMetricsFiles() {
	dir := GetConfig().MetricsDir
	_ = os.MkdirAll(dir, 0o700)
	// 多留 1 天：跨日或时钟回拨时，避免刚写入的「昨天」文件被立刻删掉。
	cutoff := time.Now().AddDate(0, 0, -(metricsRetentionDays + 1)).Format("2006-01-02")
	entries, err := os.ReadDir(dir)
	if err != nil {
		return
	}
	for _, e := range entries {
		name := e.Name()
		if !strings.HasSuffix(name, ".ndjson") || len(name) < 10 {
			continue
		}
		// 日期格式定长且字典序等于时间序，可以直接按字符串比较。
		if name[:10] < cutoff {
			_ = os.Remove(filepath.Join(dir, name))
		}
	}
}

// RecordMetricSample 从一帧合并指标中提取紧凑采样行并入缓冲。
// 只挑趋势图需要的字段（cpu/mem/disk/load1/网络/IO/CPU 温度），丢弃主机信息等静态内容，
// 单行约百字节，7 天数据量可控。sample 为空或缺少 overview 时直接返回。
func RecordMetricSample(sample map[string]any) {
	if sample == nil {
		return
	}
	overview, ok := sample["overview"].(map[string]any)
	if !ok {
		return
	}
	// ts 用秒级 Unix 浮点，与查询接口的 start/end 口径一致。
	row := map[string]any{"ts": float64(time.Now().Unix())}
	// num() 兼容 float/int/json.Number/string 多种数值来源，取不到按 0 处理。
	row["cpu"] = num(overview["cpu"])
	// 以下子结构缺失时统一填 0，保证每行字段齐全，聚合时不会因缺字段漏统计。
	if mem, ok := overview["memory"].(map[string]any); ok {
		row["mem"] = num(mem["percent"])
	} else {
		row["mem"] = 0.0
	}
	if st, ok := overview["storage"].(map[string]any); ok {
		row["disk"] = num(st["percent"])
	} else {
		row["disk"] = 0.0
	}
	if ld, ok := overview["load"].(map[string]any); ok {
		row["load1"] = num(ld["load1"])
	} else {
		row["load1"] = 0.0
	}
	// 网络/IO 记录的是「字节/秒」速率，不是累计值，便于直接画趋势。
	if nw, ok := sample["network"].(map[string]any); ok {
		row["net_up"] = num(nw["upload"])
		row["net_down"] = num(nw["download"])
	} else {
		row["net_up"], row["net_down"] = 0.0, 0.0
	}
	if di, ok := sample["diskio"].(map[string]any); ok {
		row["disk_read"] = num(di["read"])
		row["disk_write"] = num(di["write"])
	} else {
		row["disk_read"], row["disk_write"] = 0.0, 0.0
	}
	// 只记录第一条 CPU 类温度：多条传感器对趋势图没有额外价值，也避免行变胖。
	if temps, ok := overview["temps"].([]map[string]any); ok && len(temps) > 0 {
		for _, t := range temps {
			if kind, _ := t["kind"].(string); kind == "cpu" {
				row["temp"] = num(t["value"])
				break
			}
		}
	}
	// 缓冲上限 1200 行：落盘长期失败时丢弃新点，而不是无限占内存。
	metricsStoreMu.Lock()
	if len(metricsPending) < 1200 {
		metricsPending = append(metricsPending, row)
	}
	metricsStoreMu.Unlock()
}

// num 把 JSON/接口来的数值宽松地转成 float64，无法转换时返回 0。
// 覆盖 float/int/json.Number/字符串（前端可能把数字当字符串传）。
func num(v any) float64 {
	switch x := v.(type) {
	case float64:
		return x
	case float32:
		return float64(x)
	case int:
		return float64(x)
	case int64:
		return float64(x)
	case json.Number:
		f, _ := x.Float64()
		return f
	case string:
		f, _ := strconv.ParseFloat(x, 64)
		return f
	}
	return 0
}

// FlushMetrics 把缓冲采样追加写入当天 NDJSON 文件。
// 流程：取批量 → 顺带清理过期文件 → 按本地日期打开当天文件 → 逐行写 → 关闭 → 超大则截断。
// 打开失败时把本批放回缓冲头部等待下次重试；单行写错误忽略（宁可少一行，也不阻塞循环）。
func FlushMetrics() {
	metricsStoreMu.Lock()
	if len(metricsPending) == 0 {
		metricsStoreMu.Unlock()
		return
	}
	// 在锁内整个切片换出，写盘期间采集协程可以继续往新缓冲追加。
	batch := metricsPending
	metricsPending = nil
	metricsStoreMu.Unlock()

	// 每次落盘顺带清理过期文件：约 2 秒一次的空目录遍历，成本可忽略。
	cleanupMetricsFiles()
	path := metricsDayPath(time.Now().Format("2006-01-02"))
	_ = os.MkdirAll(filepath.Dir(path), 0o700)
	// 末尾追加、不存在则创建；0600 与 data 目录的权限策略一致。
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		// 落盘失败：把本批拼回缓冲头部，保证不丢点且顺序仍是旧的在前。
		metricsStoreMu.Lock()
		metricsPending = append(batch, metricsPending...)
		metricsStoreMu.Unlock()
		return
	}
	// 一行一个 JSON（NDJSON），读取端用 json.Decoder 流式解码，无需整体解析。
	for _, row := range batch {
		b, _ := json.Marshal(row)
		_, _ = f.Write(append(b, '\n'))
	}
	_ = f.Close()
	// 单日文件超过 500MB（异常高频或超长运行）时砍掉前半段，避免后续读取把内存吃满。
	if st, err := os.Stat(path); err == nil && st.Size() > 500*1024*1024 {
		truncateMetricsFile(path)
	}
}

// truncateMetricsFile 把文件从中间截断、只保留后半段行，用于单日文件异常膨胀。
// 通过 atomicWrite 先写临时文件再原子替换，避免查询侧读到写了一半的内容。
// 注意：整个文件会一次性读入内存，500MB 上限下峰值内存约等于文件大小。
func truncateMetricsFile(path string) {
	data, err := os.ReadFile(path)
	if err != nil {
		return
	}
	lines := strings.Split(strings.TrimRight(string(data), "\n"), "\n")
	if len(lines) < 2 {
		return
	}
	// 保留后半段：丢掉最老的采样，趋势图仍然连续。
	half := lines[len(lines)/2:]
	_ = atomicWrite(path, []byte(strings.Join(half, "\n")+"\n"), 0o600)
}

// loadMetricsDay 读取某天的全部采样行；文件不存在或解码中断时返回已读到的部分。
// 用 json.Decoder 流式解码 NDJSON，不会把整份文件先读成字符串再切分。
// 没有 "ts" 字段的行被丢弃：可能是旧版本残留或写坏的半行。
func loadMetricsDay(day string) []map[string]any {
	f, err := os.Open(metricsDayPath(day))
	if err != nil {
		return nil
	}
	defer f.Close()
	rows := []map[string]any{}
	dec := json.NewDecoder(f)
	for {
		var row map[string]any
		// 解码错误即认为该行（或文件尾）损坏，停止读取并保留已解析部分。
		if err := dec.Decode(&row); err != nil {
			break
		}
		if _, ok := row["ts"]; ok {
			rows = append(rows, row)
		}
	}
	return rows
}

// iterMetricRows 读取 [start, end] 区间内的全部采样行并按 ts 升序返回。
// 按天循环加载对应文件（含首尾两天），再按时间戳过滤，避免把区间外数据带进结果。
// 返回 nil 表示区间内没有任何可读文件。
func iterMetricRows(start, end float64) []map[string]any {
	t := time.Unix(int64(start), 0)
	last := time.Unix(int64(end), 0)
	rows := []map[string]any{}
	// 逐天推进（用 AddDate 而非 +24h），跨夏令时也不会漏掉或重复某天。
	for !t.After(last) {
		rows = append(rows, loadMetricsDay(t.Format("2006-01-02"))...)
		t = t.AddDate(0, 0, 1)
	}
	// rows[:0] 原地复用底层数组，避免再分配一份等长切片。
	filtered := rows[:0]
	for _, row := range rows {
		ts := num(row["ts"])
		if ts >= start && ts <= end {
			filtered = append(filtered, row)
		}
	}
	// 按天拼接的顺序通常已递增，这里再排序一次，兼容跨日边界/时钟回拨。
	sort.Slice(filtered, func(i, j int) bool { return num(filtered[i]["ts"]) < num(filtered[j]["ts"]) })
	return filtered
}

// MetricsHistory 查询历史采样（可按 bucket 秒聚合平均）。start/end 为秒级 Unix 时间戳。
// bucket<=0 时返回原始行；否则每 bucket 秒输出一个点，各字段取该桶内的算术平均
// （桶内没有样本的字段填 nil，前端可断线处理）。返回 points/buckets/raw/retention_days。
func MetricsHistory(start, end float64, bucket int) map[string]any {
	// 容忍前端把 start/end 传反。
	if end < start {
		start, end = end, start
	}
	rows := iterMetricRows(start, end)
	// bucket=0 表示要原始点，不做聚合。
	if bucket <= 0 {
		return map[string]any{
			"points": rows, "buckets": len(rows), "raw": len(rows),
			"retention_days": metricsRetentionDays,
		}
	}
	// 聚合字段白名单，与 RecordMetricSample 写入的列一一对应。
	keys := []string{"cpu", "mem", "disk", "load1", "net_up", "net_down", "disk_read", "disk_write", "temp"}
	points := []map[string]any{}
	// 桶起点对齐到 bucket 整数倍，保证同一时间窗在不同查询里桶边界一致。
	curStart := int64(start/float64(bucket)) * int64(bucket)
	bucketEnd := curStart + int64(bucket)
	acc := map[string]float64{}
	cnt := map[string]int{}
	count := 0
	flushBucket := func() {
		if count == 0 {
			return
		}
		// 桶时间取中点，避免折线图整体左移半个桶。
		point := map[string]any{"ts": float64(curStart) + float64(bucket)/2}
		for _, k := range keys {
			if cnt[k] > 0 {
				point[k] = round2(acc[k] / float64(cnt[k]))
			} else {
				point[k] = nil
			}
		}
		points = append(points, point)
	}
	for _, row := range rows {
		ts := num(row["ts"])
		// rows 已按 ts 升序，越过当前桶末尾就结账并开新桶。
		if int64(ts) >= bucketEnd {
			flushBucket()
			curStart = int64(ts/float64(bucket)) * int64(bucket)
			bucketEnd = curStart + int64(bucket)
			acc = map[string]float64{}
			cnt = map[string]int{}
			count = 0
		}
		// 桶边界推进后再跳过区间外数据，保证不把 [start,end] 之外的样本算进平均。
		if ts < start || ts > end {
			continue
		}
		// 每个字段独立计数：某行缺该字段时它不参与这一项的平均。
		for _, k := range keys {
			if v, ok := row[k]; ok && v != nil {
				acc[k] += num(v)
				cnt[k]++
			}
		}
		count++
	}
	// 收尾：把最后一个未写满的桶也输出。
	flushBucket()
	return map[string]any{
		"points": points, "buckets": len(points), "raw": len(rows),
		"retention_days": metricsRetentionDays,
	}
}

// round2 四舍五入保留 2 位小数（图表数值展示用）。
func round2(v float64) float64 {
	return float64(int64(v*100+0.5)) / 100
}

// MetricsStatus 汇总历史采样存储状态：保留天数、有数据的日期列表、最早/最晚时间戳。
// 会先跑一次过期清理，保证返回的 days 与实际可查范围一致。
// 注意：earliest/latest 需要完整解码首尾两个文件（loadMetricsDay），文件很大时开销不低。
func MetricsStatus() map[string]any {
	cleanupMetricsFiles()
	entries, _ := os.ReadDir(GetConfig().MetricsDir)
	days := []string{}
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".ndjson") {
			days = append(days, e.Name())
		}
	}
	// 文件名即日期，排序后首尾就是最早/最晚的有效文件。
	sort.Strings(days)
	var earliest, latest any
	if len(days) > 0 {
		// 只解析排序后的第一天与最后一天；空文件得到 nil，前端按「无数据」展示。
		head := loadMetricsDay(strings.TrimSuffix(days[0], ".ndjson"))
		tail := loadMetricsDay(strings.TrimSuffix(days[len(days)-1], ".ndjson"))
		if len(head) > 0 {
			earliest = head[0]["ts"]
		}
		if len(tail) > 0 {
			latest = tail[len(tail)-1]["ts"]
		}
	}
	return map[string]any{
		"retention_days": metricsRetentionDays,
		"days":           days,
		"earliest":       earliest,
		"latest":         latest,
	}
}

// ClearMetrics 删除 MetricsDir 下全部 .ndjson 历史采样文件（不删目录本身）。
// 删除失败忽略：后续清理或落盘会按需重建。
// 同时丢弃内存中的 metricsPending，否则下一轮落盘会把最近约 2 秒的旧采样写回新文件。
func ClearMetrics() {
	metricsStoreMu.Lock()
	metricsPending = nil
	metricsStoreMu.Unlock()
	entries, _ := os.ReadDir(GetConfig().MetricsDir)
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".ndjson") {
			_ = os.Remove(filepath.Join(GetConfig().MetricsDir, e.Name()))
		}
	}
}
