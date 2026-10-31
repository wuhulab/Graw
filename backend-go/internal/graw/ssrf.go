package graw

// ssrf.go —— 统一的出站 HTTP / 裸主机 SSRF 防护（对应 Python 旧版 app/ssrf_guard.py）。
//
// 背景：通知渠道 Webhook、站点可用性探测、备份 WebDAV、网络储存（FTP/SMB/S3/WebDAV）、
// 服务端口监控等多处功能接受用户提供的地址并发起出站连接。若只校验 scheme，
// 攻击者可令面板访问回环 / 链路本地（含云元数据 169.254.169.254）/ 私网地址，
// 造成内网探测或云凭据泄露。
//
// 设计要点（与 Python 基线一致）：
//  1. 仅 http/https；解析主机得到的全部 IP 逐一判定，任一命中受保护区间即拒绝；
//  2. 回环 / 链路本地 / 组播 / 保留 / 未指定地址始终拒绝；
//  3. allowPrivate 控制是否放行 RFC1918/ULA 私网（内网存储、SMTP 本机服务等场景）；
//  4. 实际连接复用「已校验的解析结果」（自定义 DialContext），消除 DNS rebinding TOCTOU；
//  5. 重定向默认不跟随；需要跟随的场景逐跳复用同一校验。
//
// 注意：不要用 net/http 的默认 Client 访问用户提供的地址——它既不做地址判定，
// 也会在每次重定向时重新解析 DNS。

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"time"
)

// ssrfHostRe 裸主机名/IP 的字符白名单（与 Python _HOST_RE 对齐）。
var ssrfHostRe = regexp.MustCompile(`^[A-Za-z0-9.\-]{1,255}$`)

// ssrfBlockedReason 返回「始终拒绝」的地址类别，空串表示未命中。
// allowLoopback 为 true 时放行回环（本机 SMTP 等合法场景）。
func ssrfBlockedReason(ip net.IP, allowLoopback bool) string {
	if ip == nil {
		return "invalid"
	}
	if ip.IsLoopback() {
		if allowLoopback {
			return ""
		}
		return "loopback"
	}
	if ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() {
		return "link_local"
	}
	if ip.IsMulticast() || ip.IsInterfaceLocalMulticast() {
		return "multicast"
	}
	if ip.IsUnspecified() {
		return "unspecified"
	}
	if v4 := ip.To4(); v4 != nil {
		// 240.0.0.0/4 保留、0.0.0.0/8 未指定（后者已被 IsUnspecified 覆盖一部分）。
		if v4[0] >= 240 {
			return "reserved"
		}
		if v4[0] == 0 {
			return "unspecified"
		}
	} else if !ip.IsGlobalUnicast() {
		return "reserved"
	}
	return ""
}

// ssrfAssertIP 对单个解析结果执行判定。
// allowPrivate=false 时还要求地址是公网（拒绝 RFC1918/ULA、CGNAT、文档网段等）。
func ssrfAssertIP(ip net.IP, host string, allowPrivate, allowLoopback bool) error {
	if reason := ssrfBlockedReason(ip, allowLoopback); reason != "" {
		return fmt.Errorf("目标主机 %s 解析为受保护地址（%s: %s），已拒绝（SSRF 防护）", host, reason, ip)
	}
	if !allowPrivate && !ssrfIsPublic(ip) {
		return fmt.Errorf("目标主机 %s 不是公网地址（%s），已拒绝（SSRF 防护）", host, ip)
	}
	return nil
}

// ssrfIsPublic 判断是否公网地址（用于 allowPrivate=false 的严格模式）。
func ssrfIsPublic(ip net.IP) bool {
	if ip == nil || ip.IsPrivate() || ip.IsLoopback() || ip.IsUnspecified() ||
		ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() || ip.IsMulticast() {
		return false
	}
	if v4 := ip.To4(); v4 != nil {
		// 100.64.0.0/10（运营商级 NAT）与 240/4 保留段不算公网。
		if v4[0] == 100 && v4[1] >= 64 && v4[1] <= 127 {
			return false
		}
		if v4[0] >= 240 || v4[0] == 0 {
			return false
		}
	}
	return ip.IsGlobalUnicast()
}

// ssrfNormalizeHost 从裸主机 / host:port / URL 中提取主机名（去除 IPv6 方括号）。
func ssrfNormalizeHost(raw string) string {
	h := strings.TrimSpace(raw)
	if h == "" {
		return ""
	}
	if strings.Contains(h, "://") {
		if u, err := url.Parse(h); err == nil && u.Hostname() != "" {
			return u.Hostname()
		}
	}
	// 方括号 IPv6：[::1] 或 [::1]:8080
	if strings.HasPrefix(h, "[") {
		if end := strings.IndexByte(h, ']'); end > 0 {
			return h[1:end]
		}
	}
	// 裸 IPv6 字面量（含多个冒号）不做端口拆分。
	if ip := net.ParseIP(h); ip != nil {
		return h
	}
	if i := strings.LastIndexByte(h, ':'); i > 0 && strings.Count(h, ":") == 1 {
		h = h[:i]
	}
	return h
}

// ssrfResolve 解析主机名；IP 字面量直接返回。
func ssrfResolve(host string) ([]net.IP, error) {
	if ip := net.ParseIP(host); ip != nil {
		return []net.IP{ip}, nil
	}
	ips, err := net.LookupIP(host)
	if err != nil || len(ips) == 0 {
		return nil, fmt.Errorf("无法解析主机名: %s", host)
	}
	return ips, nil
}

// AssertSafeHost 校验裸主机（FTP/SMB/S3/端口探测/SMTP）。
// allowPrivate=true 时允许内网（内网存储场景）；allowLoopback=true 时允许本机服务。
// 主机名解析失败按「连不上即无风险」放行（与 Python strict_dns=False 一致），
// IP 字面量与能解析到受保护地址的主机名仍会在这里被拒绝。
func AssertSafeHost(host string, allowPrivate, allowLoopback bool) error {
	h := ssrfNormalizeHost(host)
	if h == "" {
		return errors.New("主机不能为空（SSRF 防护）")
	}
	if ip := net.ParseIP(h); ip != nil {
		return ssrfAssertIP(ip, h, allowPrivate, allowLoopback)
	}
	if !ssrfHostRe.MatchString(h) {
		return fmt.Errorf("主机地址包含非法字符（SSRF 防护）")
	}
	ips, err := ssrfResolve(h)
	if err != nil {
		return nil
	}
	for _, ip := range ips {
		if err := ssrfAssertIP(ip, h, allowPrivate, allowLoopback); err != nil {
			return err
		}
	}
	return nil
}

// AssertSafeHTTPURL 校验出站 http(s) URL（通知 Webhook / 可用性探测 / WebDAV / 应用商店）。
// 主机名无法解析时 fail-closed（与 Python assert_safe_http_url 一致）。
func AssertSafeHTTPURL(rawURL string, allowPrivate bool) error {
	u, err := url.Parse(strings.TrimSpace(rawURL))
	if err != nil || u.Host == "" {
		return errors.New("URL 缺少主机名（SSRF 防护）")
	}
	scheme := strings.ToLower(u.Scheme)
	if scheme != "http" && scheme != "https" {
		return errors.New("仅支持 http/https 地址（SSRF 防护）")
	}
	host := u.Hostname()
	if host == "" {
		return errors.New("URL 缺少主机名（SSRF 防护）")
	}
	ips, err := ssrfResolve(host)
	if err != nil {
		return fmt.Errorf("%v（SSRF 防护）", err)
	}
	for _, ip := range ips {
		if err := ssrfAssertIP(ip, host, allowPrivate, false); err != nil {
			return err
		}
	}
	return nil
}

// ssrfDialContext 解析并逐个校验目标 IP 后拨号；实际连接使用同一批已校验 IP，
// 消除「校验时公网、连接时内网」的 DNS rebinding 窗口。
func ssrfDialContext(allowPrivate, allowLoopback bool, timeout time.Duration) func(ctx context.Context, network, addr string) (net.Conn, error) {
	return func(ctx context.Context, network, addr string) (net.Conn, error) {
		host, port, err := net.SplitHostPort(addr)
		if err != nil {
			host, port = addr, ""
		}
		ips, err := ssrfResolve(strings.Trim(host, "[]"))
		if err != nil {
			return nil, err
		}
		var lastErr error
		for _, ip := range ips {
			if err := ssrfAssertIP(ip, host, allowPrivate, allowLoopback); err != nil {
				return nil, err
			}
			// IPv6 字面量需要方括号才能拼出 host:port。
			dialAddr := ip.String()
			if ip.To4() == nil {
				dialAddr = "[" + dialAddr + "]"
			}
			if port != "" {
				dialAddr = net.JoinHostPort(strings.Trim(dialAddr, "[]"), port)
			}
			d := net.Dialer{Timeout: timeout}
			conn, derr := d.DialContext(ctx, network, dialAddr)
			if derr == nil {
				return conn, nil
			}
			lastErr = derr
		}
		if lastErr == nil {
			lastErr = errors.New("目标主机无可连接地址")
		}
		return nil, lastErr
	}
}

// NewSafeHTTPClient 创建带 SSRF 防护的 HTTP 客户端。
// timeout 同时用于整体请求超时与拨号超时；followRedirects=false 时 3xx 原样返回
// （调用方应把它当作错误，避免 30x 跳转绕过初始 URL 校验）。
func NewSafeHTTPClient(timeout time.Duration, allowPrivate, allowLoopback, followRedirects bool) *http.Client {
	if timeout <= 0 {
		timeout = 15 * time.Second
	}
	tr := &http.Transport{
		DialContext:         ssrfDialContext(allowPrivate, allowLoopback, 10*time.Second),
		TLSHandshakeTimeout: 10 * time.Second,
		// 只保留 HTTP/1.1：部分内网 WebDAV 服务对 h2 支持不佳，且禁用 30x 自动跟随更直观。
		ForceAttemptHTTP2: false,
		TLSClientConfig:   &tls.Config{MinVersion: tls.VersionTLS12},
	}
	client := &http.Client{Transport: tr, Timeout: timeout}
	if followRedirects {
		client.CheckRedirect = func(req *http.Request, via []*http.Request) error {
			if len(via) > 5 {
				return errors.New("重定向次数过多")
			}
			return AssertSafeHTTPURL(req.URL.String(), allowPrivate)
		}
	} else {
		client.CheckRedirect = func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		}
	}
	return client
}

// NotifyAllowPrivateNet 通知渠道是否允许访问内网（GRAW_NOTIFY_ALLOW_PRIVATE_NET=1）。
func NotifyAllowPrivateNet() bool { return envFlag("GRAW_NOTIFY_ALLOW_PRIVATE_NET") }

// UptimeAllowPrivateNet 站点可用性是否允许内网地址（GRAW_UPTIME_ALLOW_PRIVATE_NET=1）。
func UptimeAllowPrivateNet() bool { return envFlag("GRAW_UPTIME_ALLOW_PRIVATE_NET") }

// SvcMonitorAllowPrivateNet 服务监控是否允许内网地址（GRAW_SVCMON_ALLOW_PRIVATE_NET，默认允许）。
// 与服务监控的既有语义一致：默认 1，只有显式设为 0 才收紧为仅公网。
func SvcMonitorAllowPrivateNet() bool {
	v := strings.TrimSpace(strings.ToLower(os.Getenv("GRAW_SVCMON_ALLOW_PRIVATE_NET")))
	return v != "0" && v != "false" && v != "off"
}

// envFlag 读取「=1/true/yes/on」形式的布尔环境变量。
func envFlag(name string) bool {
	v := strings.TrimSpace(strings.ToLower(os.Getenv(name)))
	return v == "1" || v == "true" || v == "yes" || v == "on"
}
