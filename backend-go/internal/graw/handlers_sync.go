package graw

import (
	"bytes"
	"crypto/tls"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// 资源 / 证书同步（P2）：
//   - 导出当前节点选中的站点、证书（含 PEM）与编排模板为一个 JSON bundle；
//   - 导入到当前节点，或推送到多个 Agent 子节点。
//
// 安全边界：
//   - 导出/导入本地数据走 perm("sync")，跨节点推送要求完整管理员（adminOnly）；
//   - 证书私钥只存在于 bundle 内存/响应中，不落临时文件；
//   - 导入的站点默认停用（enabled=false），避免同名域名立即抢流量；
//   - 冲突时生成新 ID 并在名称后加「(同步)」。

type syncBundle struct {
	Version    int              `json:"version"`
	ExportedAt string           `json:"exported_at"`
	SourceNode string           `json:"source_node"`
	Sites      []map[string]any `json:"sites"`
	Certs      []map[string]any `json:"certs"`
	Templates  []map[string]any `json:"templates"`
}

func exportSyncBundle(resources []string, siteIDs, certIDs []string) (syncBundle, error) {
	bundle := syncBundle{
		Version: 1, ExportedAt: time.Now().Format("2006-01-02T15:04:05"),
		SourceNode: CurrentNodeID(), Sites: []map[string]any{}, Certs: []map[string]any{}, Templates: []map[string]any{},
	}
	want := map[string]bool{}
	for _, r := range resources {
		want[r] = true
	}
	if len(want) == 0 {
		return bundle, NewHTTPError(400, "请至少选择一项同步内容（站点/证书/模板）")
	}
	siteFilter := map[string]bool{}
	for _, id := range siteIDs {
		siteFilter[id] = true
	}
	certFilter := map[string]bool{}
	for _, id := range certIDs {
		certFilter[id] = true
	}
	if want["sites"] {
		for _, site := range loadSites() {
			if len(siteFilter) > 0 && !siteFilter[str(site["id"])] {
				continue
			}
			copy := map[string]any{}
			for k, v := range site {
				copy[k] = v
			}
			bundle.Sites = append(bundle.Sites, copy)
		}
	}
	if want["certs"] {
		for _, cert := range sslCertsList(loadSSLCerts()) {
			if len(certFilter) > 0 && !certFilter[str(cert["id"])] {
				continue
			}
			certPath := str(cert["cert_path"])
			keyPath := str(cert["key_path"])
			if certPath == "" || keyPath == "" {
				continue
			}
			certPEM, cerr := os.ReadFile(certPath)
			keyPEM, kerr := os.ReadFile(keyPath)
			if cerr != nil || kerr != nil || len(certPEM) > 1<<20 || len(keyPEM) > 1<<20 {
				continue
			}
			item := map[string]any{}
			for k, v := range cert {
				item[k] = v
			}
			item["cert_pem"] = string(certPEM)
			item["key_pem"] = string(keyPEM)
			delete(item, "cert_path")
			delete(item, "key_path")
			bundle.Certs = append(bundle.Certs, item)
		}
	}
	if want["templates"] {
		bundle.Templates = loadComposeTemplates()
	}
	return bundle, nil
}

func importSyncBundle(bundle syncBundle, enableSites bool) (map[string]any, error) {
	if bundle.Version != 1 {
		return nil, NewHTTPError(400, "不支持的同步包版本")
	}
	result := map[string]any{"sites": 0, "certs": 0, "templates": 0, "warnings": []string{}}
	warnings := []string{}
	// 站点：冲突时换新 ID，默认停用。
	if len(bundle.Sites) > 0 {
		sites := loadSites()
		ids := map[string]bool{}
		names := map[string]bool{}
		for _, site := range sites {
			ids[str(site["id"])] = true
			names[str(site["name"])] = true
		}
		imported := 0
		importedSites := []map[string]any{}
		for _, site := range bundle.Sites {
			copy := map[string]any{}
			for k, v := range site {
				copy[k] = v
			}
			id := str(copy["id"])
			name := str(copy["name"])
			if id == "" || !siteIDRe.MatchString(id) || ids[id] {
				id = "site_" + randomHex(8)
				copy["id"] = id
			}
			if name == "" {
				name = id
			}
			if names[name] {
				name = name + " (同步)"
			}
			copy["name"] = name
			copy["enabled"] = enableSites
			copy["synced_at"] = time.Now().Format("2006-01-02 15:04:05")
			sites = append(sites, copy)
			importedSites = append(importedSites, copy)
			ids[id] = true
			names[name] = true
			imported++
		}
		if err := saveSites(sites); err != nil {
			return nil, err
		}
		// 启用导入站点时必须同时生成 Web 服务器配置，否则会出现
		//「列表显示已启用但 nginx 没有 server 块」的假状态；失败则回退为停用。
		if enableSites {
			needSave := false
			for _, site := range importedSites {
				if err := applySiteConfig(site, true); err != nil {
					site["enabled"] = false
					needSave = true
					warnings = append(warnings, "站点 "+str(site["name"])+" 启用失败，已保持停用: "+err.Error())
				}
			}
			if needSave {
				if err := saveSites(sites); err != nil {
					return nil, err
				}
			}
			reloadIfNginx()
		}
		result["sites"] = imported
	}
	// 证书：写入 data/ssl/，保留原名称/域名。
	if len(bundle.Certs) > 0 {
		data := loadSSLCerts()
		certs := sslCertsList(data)
		dir := DataPath("ssl")
		_ = EnsureDir(dir)
		imported := 0
		for _, cert := range bundle.Certs {
			certPEM := str(cert["cert_pem"])
			keyPEM := str(cert["key_pem"])
			if certPEM == "" || keyPEM == "" {
				warnings = append(warnings, "证书 "+str(cert["name"])+" 缺少 PEM，已跳过")
				continue
			}
			if _, terr := tls.X509KeyPair([]byte(certPEM), []byte(keyPEM)); terr != nil {
				warnings = append(warnings, "证书 "+str(cert["name"])+" PEM 校验失败，已跳过: "+terr.Error())
				continue
			}
			id := "sync_" + randomHex(8)
			certPath := filepath.Join(dir, id+".crt")
			keyPath := filepath.Join(dir, id+".key")
			if err := os.WriteFile(certPath, []byte(certPEM), 0o600); err != nil {
				warnings = append(warnings, "写入证书失败: "+err.Error())
				continue
			}
			if err := os.WriteFile(keyPath, []byte(keyPEM), 0o600); err != nil {
				warnings = append(warnings, "写入私钥失败: "+err.Error())
				continue
			}
			certs = append(certs, map[string]any{
				"id": id, "name": defaultString(str(cert["name"]), id),
				"domains": cert["domains"], "type": "synced",
				"cert_path": certPath, "key_path": keyPath,
				"created_at":  time.Now().Format("2006-01-02T15:04:05"),
				"expires_at":  readCertExpiry(certPath),
				"synced_from": bundle.SourceNode,
			})
			imported++
		}
		data["certs"] = certs
		if err := WriteJSONFile(DataPath("ssl.json"), data); err != nil {
			return nil, err
		}
		result["certs"] = imported
	}
	// 编排模板：按名称 upsert。
	if len(bundle.Templates) > 0 {
		templates := loadComposeTemplates()
		byName := map[string]map[string]any{}
		for _, item := range templates {
			byName[str(item["name"])] = item
		}
		imported := 0
		for _, item := range bundle.Templates {
			name := strings.TrimSpace(str(item["name"]))
			if name == "" {
				continue
			}
			if existing := byName[name]; existing != nil {
				existing["compose"] = item["compose"]
				existing["updated_at"] = time.Now().Format("2006-01-02 15:04:05")
			} else {
				copy := map[string]any{}
				for k, v := range item {
					copy[k] = v
				}
				copy["created_at"] = time.Now().Format("2006-01-02 15:04:05")
				templates = append(templates, copy)
				byName[name] = copy
			}
			imported++
		}
		if err := saveComposeTemplates(templates); err != nil {
			return nil, err
		}
		result["templates"] = imported
	}
	result["warnings"] = warnings
	return result, nil
}

// agentJSONPost 通过 Agent 隧道向子节点发送 JSON（用于跨节点同步）。
func agentJSONPost(node map[string]any, path string, payload any) (map[string]any, error) {
	raw, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	client, err := pooledSSH(node, 10*time.Second)
	if err != nil {
		return nil, err
	}
	token, err := agentToken(node, client)
	if err != nil {
		return nil, err
	}
	port := int(int64Of(node["agent_port"]))
	if port == 0 {
		port = defaultAgentPort
	}
	conn, reader, err := openAgentConn(client, port, "POST", path,
		map[string]string{
			"Authorization": "Bearer " + token,
			"Content-Type":  "application/json",
			"Accept":        "application/json",
		}, bytes.NewReader(raw), int64(len(raw)))
	if err != nil {
		return nil, err
	}
	defer conn.Close()
	resp, err := http.ReadResponse(reader, &http.Request{Method: "POST"})
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	var data map[string]any
	if err := json.Unmarshal(body, &data); err != nil {
		return nil, err
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, NewHTTPError(502, defaultString(str(data["detail"]), "子节点同步失败"))
	}
	return data, nil
}

func registerSyncRoutes(a *App) {
	a.Post("/api/sync/export", perm("sync"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Resources []string `json:"resources"`
			SiteIDs   []string `json:"site_ids"`
			CertIDs   []string `json:"cert_ids"`
		}
		_ = DecodeJSON(r, &req)
		bundle, err := exportSyncBundle(req.Resources, req.SiteIDs, req.CertIDs)
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, bundle)
	})
	a.Post("/api/sync/import", perm("sync"), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Bundle      syncBundle `json:"bundle"`
			EnableSites bool       `json:"enable_sites"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		res, err := importSyncBundle(req.Bundle, req.EnableSites)
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, res)
	})
	a.Post("/api/nodes/sync", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			NodeIDs     []string `json:"node_ids"`
			Resources   []string `json:"resources"`
			SiteIDs     []string `json:"site_ids"`
			CertIDs     []string `json:"cert_ids"`
			EnableSites bool     `json:"enable_sites"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		bundle, err := exportSyncBundle(req.Resources, req.SiteIDs, req.CertIDs)
		if err != nil {
			return err
		}
		store := loadNodeStore()
		results := []map[string]any{}
		for _, id := range req.NodeIDs {
			node := store.Nodes[id]
			if node == nil {
				results = append(results, map[string]any{"node_id": id, "ok": false, "error": "节点不存在"})
				continue
			}
			if str(node["type"]) != "ssh" || !agentReady(node) {
				results = append(results, map[string]any{"node_id": id, "ok": false, "error": "仅支持已启用 Agent 的 SSH 子节点"})
				continue
			}
			res, serr := agentJSONPost(node, "/api/sync/import", map[string]any{
				"bundle": bundle, "enable_sites": req.EnableSites,
			})
			if serr != nil {
				results = append(results, map[string]any{"node_id": id, "ok": false, "error": serr.Error()})
				continue
			}
			results = append(results, map[string]any{"node_id": id, "ok": true, "result": res})
		}
		return WriteJSON(w, 200, map[string]any{"ok": true, "results": results})
	})
}
