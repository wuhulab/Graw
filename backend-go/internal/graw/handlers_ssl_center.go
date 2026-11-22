package graw

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"math/big"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// 证书中心增强：自签证书、证书详情/续签、ACME 账户、DNS 账户与 DNS-01 签发。
// 数据仍存放在 data/ssl.json；DNS 账户单独存 data/dns_accounts.json（凭据不回传明文）。

var dnsProviders = map[string]bool{
	"cloudflare": true, "route53": true, "digitalocean": true, "google": true,
	"ovh": true, "rfc2136": true, "aliyun": true, "dnspod": true, "huaweicloud": true,
}

func addSSLCert(cert map[string]any) error {
	data := loadSSLCerts()
	data["certs"] = append(sslCertsList(data), cert)
	return WriteJSONFile(DataPath("ssl.json"), data)
}

func findSSLCert(id string) map[string]any {
	for _, c := range sslCertsList(loadSSLCerts()) {
		if str(c["id"]) == id {
			return c
		}
	}
	return nil
}

// generateSelfSigned 生成自签证书（RSA 2048，支持域名与 IP SAN）。
func generateSelfSigned(domains []string, days int) ([]byte, []byte, error) {
	if days <= 0 {
		days = 365
	}
	if days > 3650 {
		days = 3650
	}
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return nil, nil, err
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		return nil, nil, err
	}
	tpl := &x509.Certificate{
		SerialNumber:          serial,
		Subject:               pkix.Name{CommonName: domains[0], Organization: []string{"Graw Panel"}},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().Add(time.Duration(days) * 24 * time.Hour),
		KeyUsage:              x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		BasicConstraintsValid: true,
	}
	for _, d := range domains {
		if ip := net.ParseIP(d); ip != nil {
			tpl.IPAddresses = append(tpl.IPAddresses, ip)
		} else {
			tpl.DNSNames = append(tpl.DNSNames, d)
		}
	}
	der, err := x509.CreateCertificate(rand.Reader, tpl, tpl, &key.PublicKey, key)
	if err != nil {
		return nil, nil, err
	}
	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(key)})
	return certPEM, keyPEM, nil
}

func certDetail(path string) map[string]any {
	b, err := os.ReadFile(HostFilePath(path))
	if err != nil {
		return map[string]any{"error": "证书文件不可读"}
	}
	block, _ := pem.Decode(b)
	if block == nil {
		return map[string]any{"error": "证书格式非法"}
	}
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return map[string]any{"error": "证书解析失败"}
	}
	return map[string]any{
		"subject": cert.Subject.CommonName, "issuer": cert.Issuer.CommonName,
		"serial":     cert.SerialNumber.String(),
		"not_before": cert.NotBefore.Format("2006-01-02 15:04:05"),
		"not_after":  cert.NotAfter.Format("2006-01-02 15:04:05"),
		"dns_names":  cert.DNSNames,
		"days_left":  int(time.Until(cert.NotAfter).Hours() / 24),
	}
}

// --- DNS 账户 ---

func loadDNSAccounts() []map[string]any {
	var items []map[string]any
	_ = ReadJSON(DataPath("dns_accounts.json"), &items)
	return items
}

func saveDNSAccounts(items []map[string]any) error {
	CaptureSnapshot("dns-accounts", "ssl", DataPath("dns_accounts.json"))
	return WriteJSONFile(DataPath("dns_accounts.json"), items)
}

func maskDNSAccount(item map[string]any) map[string]any {
	out := map[string]any{
		"id": str(item["id"]), "name": str(item["name"]), "provider": str(item["provider"]),
		"created_at": str(item["created_at"]),
	}
	keys := []string{}
	for k := range mapOf(item["credentials"]) {
		keys = append(keys, k)
	}
	out["credential_keys"] = keys
	return out
}

// --- ACME 账户（邮箱） ---

func acmeAccounts() []map[string]any {
	data := loadSSLCerts()
	arr, _ := data["acme_accounts"].([]any)
	out := []map[string]any{}
	for _, item := range arr {
		if m, ok := item.(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out
}

func registerSSLCenterRoutes(a *App) {
	a.Post("/api/ssl/selfsigned", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Name    string   `json:"name"`
			Domains []string `json:"domains"`
			Days    int      `json:"days"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if len(req.Domains) == 0 || len(req.Domains) > 32 {
			return NewHTTPError(400, "域名数量需为 1-32 个")
		}
		for _, d := range req.Domains {
			if net.ParseIP(strings.TrimSpace(d)) == nil && !certDomainRe.MatchString(d) {
				return NewHTTPError(400, "域名格式非法: "+d)
			}
		}
		if req.Days <= 0 {
			req.Days = 365
		}
		if req.Days > 3650 {
			return NewHTTPError(400, "有效期不能超过 3650 天")
		}
		certPEM, keyPEM, err := generateSelfSigned(req.Domains, req.Days)
		if err != nil {
			return NewHTTPError(500, "生成自签证书失败: "+err.Error())
		}
		id := "self_" + randomHex(8)
		dir := DataPath("ssl")
		_ = EnsureDir(dir)
		certPath := filepath.Join(dir, id+".crt")
		keyPath := filepath.Join(dir, id+".key")
		// 写入失败必须中止：否则 ssl.json 会记录一个指向不存在文件的证书。
		if werr := os.WriteFile(certPath, certPEM, 0o600); werr != nil {
			return NewHTTPError(500, "写入证书失败: "+werr.Error())
		}
		if werr := os.WriteFile(keyPath, keyPEM, 0o600); werr != nil {
			_ = os.Remove(certPath)
			return NewHTTPError(500, "写入私钥失败: "+werr.Error())
		}
		name := strings.TrimSpace(req.Name)
		if name == "" {
			name = req.Domains[0]
		}
		if len(name) > 64 {
			name = name[:64]
		}
		cert := map[string]any{
			"id": id, "name": name, "domains": req.Domains, "type": "selfsigned",
			"issuer": "Graw Self-Signed", "cert_path": certPath, "key_path": keyPath,
			"created_at": time.Now().Format("2006-01-02T15:04:05"),
			"expires_at": readCertExpiry(certPath), "auto_renew": false,
		}
		if err := addSSLCert(cert); err != nil {
			return err
		}
		return WriteJSON(w, 200, cert)
	})

	a.Get("/api/ssl/{cert_id}", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		cert := findSSLCert(PathParam(r, "cert_id"))
		if cert == nil {
			return NewHTTPError(404, "Certificate not found")
		}
		return WriteJSON(w, 200, map[string]any{"cert": cert, "detail": certDetail(str(cert["cert_path"]))})
	})

	a.Post("/api/ssl/{cert_id}/renew", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		cert := findSSLCert(PathParam(r, "cert_id"))
		if cert == nil {
			return NewHTTPError(404, "Certificate not found")
		}
		typ := str(cert["type"])
		if typ == "letsencrypt" {
			if HostWhich("certbot") == "" {
				return NewHTTPError(503, "服务器未安装 certbot")
			}
			res := HostCmd(180*time.Second, "certbot", "renew", "--cert-name", str(cert["name"]), "--force-renewal")
			if res.Code != 0 {
				return NewHTTPError(500, strings.TrimSpace(res.Stderr+res.Stdout))
			}
		} else {
			domains := []string{}
			for _, d := range toStringSlice(cert["domains"]) {
				if s := strings.TrimSpace(d); s != "" {
					domains = append(domains, s)
				}
			}
			if len(domains) == 0 {
				return NewHTTPError(400, "证书缺少域名，无法续签")
			}
			certPEM, keyPEM, err := generateSelfSigned(domains, 365)
			if err != nil {
				return NewHTTPError(500, "续签失败: "+err.Error())
			}
			// 自签续签：写盘失败应直接报错，避免返回「续签成功」但证书仍是旧的。
			if werr := os.WriteFile(HostFilePath(str(cert["cert_path"])), certPEM, 0o600); werr != nil {
				return NewHTTPError(500, "写入证书失败: "+werr.Error())
			}
			if werr := os.WriteFile(HostFilePath(str(cert["key_path"])), keyPEM, 0o600); werr != nil {
				return NewHTTPError(500, "写入私钥失败: "+werr.Error())
			}
		}
		cert["expires_at"] = readCertExpiry(str(cert["cert_path"]))
		// 回写 ssl.json 中的过期时间
		data := loadSSLCerts()
		certs := sslCertsList(data)
		for i, c := range certs {
			if str(c["id"]) == str(cert["id"]) {
				certs[i] = cert
			}
		}
		data["certs"] = certs
		if err := WriteJSONFile(DataPath("ssl.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "expires_at": cert["expires_at"]})
	})

	// ACME 账户（邮箱）管理：目前用于 DNS-01 / HTTP-01 签发时选择联系邮箱。
	a.Get("/api/ssl/acme-accounts", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, map[string]any{"accounts": acmeAccounts()})
	})
	a.Post("/api/ssl/acme-accounts", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Email string `json:"email"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		email := strings.TrimSpace(req.Email)
		if !strings.Contains(email, "@") || strings.ContainsAny(email, " \t\r\n") || len(email) > 254 {
			return NewHTTPError(400, "邮箱格式非法")
		}
		data := loadSSLCerts()
		arr, _ := data["acme_accounts"].([]any)
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok && str(m["email"]) == email {
				return WriteJSON(w, 200, m)
			}
		}
		item := map[string]any{"id": "acme_" + randomHex(6), "email": email, "created_at": time.Now().Format("2006-01-02T15:04:05")}
		data["acme_accounts"] = append(arr, item)
		if err := WriteJSONFile(DataPath("ssl.json"), data); err != nil {
			return err
		}
		return WriteJSON(w, 200, item)
	})
	a.Post("/api/ssl/acme-accounts/{account_id}/delete", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "account_id")
		data := loadSSLCerts()
		arr, _ := data["acme_accounts"].([]any)
		kept := []any{}
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok && str(m["id"]) == id {
				continue
			}
			kept = append(kept, item)
		}
		data["acme_accounts"] = kept
		return WriteJSONFile(DataPath("ssl.json"), data)
	})

	// DNS 账户：凭据只写不读，列表仅返回字段名。
	a.Get("/api/ssl/dns-accounts", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		items := []map[string]any{}
		for _, item := range loadDNSAccounts() {
			items = append(items, maskDNSAccount(item))
		}
		return WriteJSON(w, 200, map[string]any{"accounts": items, "providers": dnsProviderList()})
	})
	a.Post("/api/ssl/dns-accounts", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Name        string `json:"name"`
			Provider    string `json:"provider"`
			Credentials string `json:"credentials"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		provider := strings.ToLower(strings.TrimSpace(req.Provider))
		if !dnsProviders[provider] {
			return NewHTTPError(400, "不支持的 DNS 服务商: "+provider)
		}
		creds := map[string]string{}
		if err := json.Unmarshal([]byte(req.Credentials), &creds); err != nil || len(creds) == 0 {
			return NewHTTPError(400, "凭据必须是 JSON 对象，例如 {\"CLOUDFLARE_API_TOKEN\":\"...\"}")
		}
		name := strings.TrimSpace(req.Name)
		if name == "" {
			name = provider
		}
		items := loadDNSAccounts()
		item := map[string]any{
			"id": "dns_" + randomHex(6), "name": name, "provider": provider,
			"credentials": creds, "created_at": time.Now().Format("2006-01-02T15:04:05"),
		}
		items = append(items, item)
		if err := saveDNSAccounts(items); err != nil {
			return err
		}
		return WriteJSON(w, 200, maskDNSAccount(item))
	})
	a.Post("/api/ssl/dns-accounts/{account_id}/delete", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		id := PathParam(r, "account_id")
		items := loadDNSAccounts()
		kept := []map[string]any{}
		for _, item := range items {
			if str(item["id"]) != id {
				kept = append(kept, item)
			}
		}
		if len(kept) == len(items) {
			return NewHTTPError(404, "DNS 账户不存在")
		}
		return saveDNSAccounts(kept)
	})

	// DNS-01 签发：需要 certbot 及对应 dns 插件（例如 certbot-dns-cloudflare）。
	a.Post("/api/ssl/letsencrypt/dns", perm("sites"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Domains   []string `json:"domains"`
			Email     string   `json:"email"`
			AccountID string   `json:"account_id"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if HostWhich("certbot") == "" {
			return NewHTTPError(503, "服务器未安装 certbot")
		}
		if len(req.Domains) == 0 || len(req.Domains) > 32 {
			return NewHTTPError(400, "域名数量需为 1-32 个")
		}
		for _, d := range req.Domains {
			if !certDomainRe.MatchString(d) {
				return NewHTTPError(400, "域名格式非法: "+d)
			}
		}
		if !strings.Contains(req.Email, "@") || strings.ContainsAny(req.Email, " \t\r\n") {
			return NewHTTPError(400, "邮箱格式非法")
		}
		var account map[string]any
		for _, item := range loadDNSAccounts() {
			if str(item["id"]) == req.AccountID {
				account = item
				break
			}
		}
		if account == nil {
			return NewHTTPError(400, "请选择 DNS 账户")
		}
		provider := str(account["provider"])
		tmpDir := DataPath("tmp")
		_ = os.MkdirAll(tmpDir, 0o700)
		credFile := filepath.Join(tmpDir, "dns_"+randomHex(6)+".ini")
		lines := []string{}
		for k, v := range mapOf(account["credentials"]) {
			if s := strings.TrimSpace(str(v)); s != "" {
				lines = append(lines, k+" = "+s)
			}
		}
		if err := os.WriteFile(credFile, []byte(strings.Join(lines, "\n")+"\n"), 0o600); err != nil {
			return NewHTTPError(500, "写入 DNS 凭据失败")
		}
		defer os.Remove(credFile)
		// certbot 经 HostCmd 在宿主机（容器部署下为 chroot）执行，凭据文件必须
		// 使用宿主机可见路径，否则 certbot 读不到 data/tmp 里的 INI。
		args := []string{"certonly", "--dns-" + provider, "--dns-" + provider + "-credentials", HostVisiblePath(credFile),
			"--agree-tos", "-m", req.Email, "-n", "--preferred-challenges", "dns"}
		for _, d := range req.Domains {
			args = append(args, "-d", d)
		}
		res := HostCmd(300*time.Second, "certbot", args...)
		if res.Code != 0 {
			msg := strings.TrimSpace(res.Stderr + res.Stdout)
			if strings.Contains(msg, "unrecognized arguments") || strings.Contains(msg, "Unrecognized") {
				msg += "\n提示：请先在服务器安装 certbot-dns-" + provider + " 插件"
			}
			return NewHTTPError(500, msg)
		}
		first := req.Domains[0]
		cert := map[string]any{
			"id": "le_" + randomHex(6), "name": first, "domains": req.Domains,
			"type": "letsencrypt", "issuer": "Let's Encrypt",
			"cert_path":  "/etc/letsencrypt/live/" + first + "/fullchain.pem",
			"key_path":   "/etc/letsencrypt/live/" + first + "/privkey.pem",
			"created_at": time.Now().Format("2006-01-02T15:04:05"),
			"expires_at": readCertExpiry("/etc/letsencrypt/live/" + first + "/fullchain.pem"),
		}
		if err := addSSLCert(cert); err != nil {
			return err
		}
		return WriteJSON(w, 200, cert)
	})
}

func dnsProviderList() []string {
	out := []string{}
	for k := range dnsProviders {
		out = append(out, k)
	}
	return out
}
