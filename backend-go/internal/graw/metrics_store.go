package graw

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

// 历史监控采样存储（data/metrics/YYYY-MM-DD.ndjson，保留 7 天）。
const metricsRetentionDays = 7

var (
	metricsStoreMu       sync.Mutex
	metricsPending       []map[string]any
	metricsLastSpaceChk  time.Time
	metricsDiskWarned    bool
	metricsFlushInterval = 2 * time.Second
)

func metricsDayPath(day string) string {
	return filepath.Join(GetConfig().MetricsDir, day+".ndjson")
}

func cleanupMetricsFiles() {
	dir := GetConfig().MetricsDir
	_ = os.MkdirAll(dir, 0o700)
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
		if name[:10] < cutoff {
			_ = os.Remove(filepath.Join(dir, name))
		}
	}
}

func metricsCanWrite() bool {
	now := time.Now()
	if now.Sub(metricsLastSpaceChk) < 30*time.Second {
		return !metricsDiskWarned
	}
	metricsLastSpaceChk = now
	metricsDiskWarned = false
	return true
}

// RecordMetricSample 从一帧合并指标中提取紧凑采样行并入缓冲。
func RecordMetricSample(sample map[string]any) {
	if sample == nil {
		return
	}
	overview, ok := sample["overview"].(map[string]any)
	if !ok {
		return
	}
	row := map[string]any{"ts": float64(time.Now().Unix())}
	row["cpu"] = num(overview["cpu"])
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
	if temps, ok := overview["temps"].([]map[string]any); ok && len(temps) > 0 {
		for _, t := range temps {
			if kind, _ := t["kind"].(string); kind == "cpu" {
				row["temp"] = num(t["value"])
				break
			}
		}
	}
	metricsStoreMu.Lock()
	if len(metricsPending) < 1200 {
		metricsPending = append(metricsPending, row)
	}
	metricsStoreMu.Unlock()
}

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
func FlushMetrics() {
	if !metricsCanWrite() {
		return
	}
	metricsStoreMu.Lock()
	if len(metricsPending) == 0 {
		metricsStoreMu.Unlock()
		return
	}
	batch := metricsPending
	metricsPending = nil
	metricsStoreMu.Unlock()

	cleanupMetricsFiles()
	path := metricsDayPath(time.Now().Format("2006-01-02"))
	_ = os.MkdirAll(filepath.Dir(path), 0o700)
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		metricsStoreMu.Lock()
		metricsPending = append(batch, metricsPending...)
		metricsStoreMu.Unlock()
		return
	}
	for _, row := range batch {
		b, _ := json.Marshal(row)
		_, _ = f.Write(append(b, '\n'))
	}
	_ = f.Close()
	if st, err := os.Stat(path); err == nil && st.Size() > 500*1024*1024 {
		truncateMetricsFile(path)
	}
}

func truncateMetricsFile(path string) {
	data, err := os.ReadFile(path)
	if err != nil {
		return
	}
	lines := strings.Split(strings.TrimRight(string(data), "\n"), "\n")
	if len(lines) < 2 {
		return
	}
	half := lines[len(lines)/2:]
	_ = atomicWrite(path, []byte(strings.Join(half, "\n")+"\n"), 0o600)
}

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
		if err := dec.Decode(&row); err != nil {
			break
		}
		if _, ok := row["ts"]; ok {
			rows = append(rows, row)
		}
	}
	return rows
}

func iterMetricRows(start, end float64) []map[string]any {
	t := time.Unix(int64(start), 0)
	last := time.Unix(int64(end), 0)
	rows := []map[string]any{}
	for !t.After(last) {
		rows = append(rows, loadMetricsDay(t.Format("2006-01-02"))...)
		t = t.AddDate(0, 0, 1)
	}
	filtered := rows[:0]
	for _, row := range rows {
		ts := num(row["ts"])
		if ts >= start && ts <= end {
			filtered = append(filtered, row)
		}
	}
	sort.Slice(filtered, func(i, j int) bool { return num(filtered[i]["ts"]) < num(filtered[j]["ts"]) })
	return filtered
}

// MetricsHistory 查询历史采样（可按 bucket 秒聚合平均）。
func MetricsHistory(start, end float64, bucket int) map[string]any {
	if end < start {
		start, end = end, start
	}
	rows := iterMetricRows(start, end)
	if bucket <= 0 {
		return map[string]any{
			"points": rows, "buckets": len(rows), "raw": len(rows),
			"retention_days": metricsRetentionDays,
		}
	}
	keys := []string{"cpu", "mem", "disk", "load1", "net_up", "net_down", "disk_read", "disk_write", "temp"}
	points := []map[string]any{}
	curStart := int64(start/float64(bucket)) * int64(bucket)
	bucketEnd := curStart + int64(bucket)
	acc := map[string]float64{}
	cnt := map[string]int{}
	count := 0
	flushBucket := func() {
		if count == 0 {
			return
		}
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
		if int64(ts) >= bucketEnd {
			flushBucket()
			curStart = int64(ts/float64(bucket)) * int64(bucket)
			bucketEnd = curStart + int64(bucket)
			acc = map[string]float64{}
			cnt = map[string]int{}
			count = 0
		}
		if ts < start || ts > end {
			continue
		}
		for _, k := range keys {
			if v, ok := row[k]; ok && v != nil {
				acc[k] += num(v)
				cnt[k]++
			}
		}
		count++
	}
	flushBucket()
	return map[string]any{
		"points": points, "buckets": len(points), "raw": len(rows),
		"retention_days": metricsRetentionDays,
	}
}

func round2(v float64) float64 {
	return float64(int64(v*100+0.5)) / 100
}

func MetricsStatus() map[string]any {
	cleanupMetricsFiles()
	entries, _ := os.ReadDir(GetConfig().MetricsDir)
	days := []string{}
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".ndjson") {
			days = append(days, e.Name())
		}
	}
	sort.Strings(days)
	var earliest, latest any
	if len(days) > 0 {
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

// ClearMetrics 删除全部历史采样文件。
func ClearMetrics() {
	entries, _ := os.ReadDir(GetConfig().MetricsDir)
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".ndjson") {
			_ = os.Remove(filepath.Join(GetConfig().MetricsDir, e.Name()))
		}
	}
}
