package graw

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// AI 模块（P2）：保持「轻内核」定位，只提供三件事：
//   - 模型账号：OpenAI 兼容接口的 base_url / api_key / model 管理；
//   - 对话测试：直连 /chat/completions，验证账号是否可用；
//   - GPU 监控：读取 nvidia-smi（只读）；
//   - MCP 清单：登记 MCP HTTP 端点并做连通性测试。
//
// 不内置智能体编排、向量库、模型下载或 AI 网关路由；这些更适合插件/应用商店配方。
// 凭据保存在 data/ai.json（权限 0600），列表接口只回 api_key_set。

type aiAccount struct {
	ID        string `json:"id"`
	Name      string `json:"name"`
	BaseURL   string `json:"base_url"`
	APIKey    string `json:"api_key"`
	Model     string `json:"model"`
	Enabled   bool   `json:"enabled"`
	CreatedAt string `json:"created_at"`
}

type aiMCP struct {
	ID        string            `json:"id"`
	Name      string            `json:"name"`
	URL       string            `json:"url"`
	Headers   map[string]string `json:"headers"`
	Enabled   bool              `json:"enabled"`
	CreatedAt string            `json:"created_at"`
}

type aiStore struct {
	Accounts []aiAccount `json:"accounts"`
	MCP      []aiMCP     `json:"mcp"`
}

func aiStorePath() string { return DataPath("ai.json") }

func loadAIStore() aiStore {
	data := aiStore{Accounts: []aiAccount{}, MCP: []aiMCP{}}
	_ = ReadJSON(aiStorePath(), &data)
	if data.Accounts == nil {
		data.Accounts = []aiAccount{}
	}
	if data.MCP == nil {
		data.MCP = []aiMCP{}
	}
	return data
}

func saveAIStore(data aiStore) error {
	CaptureSnapshot("ai-config", "ai", aiStorePath())
	return WriteJSONFile(aiStorePath(), data)
}

func aiAccountPublic(a aiAccount) map[string]any {
	return map[string]any{
		"id": a.ID, "name": a.Name, "base_url": a.BaseURL, "model": a.Model,
		"enabled": a.Enabled, "api_key_set": strings.TrimSpace(a.APIKey) != "",
		"created_at": a.CreatedAt,
	}
}

func aiMCPPublic(m aiMCP) map[string]any {
	keys := []string{}
	for k := range m.Headers {
		keys = append(keys, k)
	}
	sortStrings(keys)
	return map[string]any{
		"id": m.ID, "name": m.Name, "url": m.URL, "header_keys": keys,
		"enabled": m.Enabled, "created_at": m.CreatedAt,
	}
}

func validateAIURL(raw string) error {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || u.Host == "" {
		return NewHTTPError(400, "URL 格式非法")
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return NewHTTPError(400, "只支持 http/https")
	}
	if strings.Contains(u.Host, "169.254.169.254") {
		return NewHTTPError(400, "不允许访问云 metadata 地址")
	}
	return nil
}

// aiChatURL 把用户填写的 base_url 归一化到 /chat/completions。
func aiChatURL(base string) string {
	base = strings.TrimRight(strings.TrimSpace(base), "/")
	if strings.HasSuffix(base, "/chat/completions") {
		return base
	}
	return base + "/chat/completions"
}

func aiChat(account aiAccount, prompt string) (map[string]any, error) {
	if strings.TrimSpace(account.Model) == "" {
		return nil, NewHTTPError(400, "请先填写模型名称")
	}
	if strings.TrimSpace(prompt) == "" {
		prompt = "你好，请用一句话介绍你自己。"
	}
	body := map[string]any{
		"model": account.Model,
		"messages": []map[string]any{
			{"role": "user", "content": prompt},
		},
		"max_tokens": 512,
	}
	raw, _ := json.Marshal(body)
	req, err := http.NewRequest("POST", aiChatURL(account.BaseURL), bytes.NewReader(raw))
	if err != nil {
		return nil, NewHTTPError(400, "构造请求失败")
	}
	req.Header.Set("Content-Type", "application/json")
	if account.APIKey != "" {
		req.Header.Set("Authorization", "Bearer "+account.APIKey)
	}
	// 走统一的 SSRF 防护客户端：允许内网/本机模型（如 Ollama），
	// 但仍拒绝云 metadata、链路本地等受保护地址，并禁用自动跳转。
	client := NewSafeHTTPClient(60*time.Second, true, true, false)
	resp, err := client.Do(req)
	if err != nil {
		return nil, NewHTTPError(502, "请求模型失败: "+err.Error())
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, NewHTTPError(502, "模型返回 HTTP "+itoa(resp.StatusCode)+": "+tailText(string(data), 1000))
	}
	var parsed map[string]any
	if err := json.Unmarshal(data, &parsed); err != nil {
		return nil, NewHTTPError(502, "模型响应不是 JSON")
	}
	content := ""
	if choices, ok := parsed["choices"].([]any); ok && len(choices) > 0 {
		msg := mapOf(mapOf(choices[0])["message"])
		content = str(msg["content"])
		if content == "" {
			content = str(mapOf(choices[0])["text"])
		}
	}
	if content == "" {
		content = tailText(string(data), 2000)
	}
	return map[string]any{
		"ok": true, "content": content, "usage": parsed["usage"],
		"model": defaultString(str(parsed["model"]), account.Model),
	}, nil
}

func aiGPUStatus() map[string]any {
	res := HostCmd(15*time.Second, "nvidia-smi",
		"--query-gpu=index,name,memory.total,memory.used,utilization.gpu,temperature.gpu",
		"--format=csv,noheader,nounits")
	if res.Code != 0 {
		return map[string]any{
			"available": false, "reason": "未检测到 nvidia-smi（无 NVIDIA GPU 或驱动未安装）",
			"gpus": []any{},
		}
	}
	gpus := []map[string]any{}
	for _, line := range strings.Split(strings.TrimSpace(res.Stdout), "\n") {
		parts := strings.Split(line, ",")
		if len(parts) < 6 {
			continue
		}
		for i := range parts {
			parts[i] = strings.TrimSpace(parts[i])
		}
		gpus = append(gpus, map[string]any{
			"index": int64Of(parts[0]), "name": parts[1],
			"memory_total_mb": int64Of(parts[2]), "memory_used_mb": int64Of(parts[3]),
			"utilization": int64Of(parts[4]), "temperature": int64Of(parts[5]),
		})
	}
	return map[string]any{"available": true, "gpus": gpus}
}

func aiMCPTest(item aiMCP) (map[string]any, error) {
	if err := validateAIURL(item.URL); err != nil {
		return nil, err
	}
	// MCP Streamable HTTP 正常入口是 POST initialize；GET 只作为兼容回退。
	initBody := map[string]any{
		"jsonrpc": "2.0", "id": 1, "method": "initialize",
		"params": map[string]any{
			"protocolVersion": "2024-11-05", "capabilities": map[string]any{},
			"clientInfo": map[string]any{"name": "graw", "version": AppVersion},
		},
	}
	raw, _ := json.Marshal(initBody)
	req, err := http.NewRequest("POST", item.URL, bytes.NewReader(raw))
	if err != nil {
		return nil, NewHTTPError(400, "构造请求失败")
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	for k, v := range item.Headers {
		req.Header.Set(k, v)
	}
	// 与对话测试一致：允许本机/内网 MCP 端点，但拒绝受保护地址。
	client := NewSafeHTTPClient(10*time.Second, true, true, false)
	start := time.Now()
	resp, err := client.Do(req)
	if err == nil && (resp.StatusCode == 405 || resp.StatusCode == 406) {
		// 老式 SSE 端点不支持 POST，再试一次 GET。
		_ = resp.Body.Close()
		req, rerr := http.NewRequest("GET", item.URL, nil)
		if rerr != nil {
			return map[string]any{"ok": false, "error": rerr.Error()}, nil
		}
		for k, v := range item.Headers {
			req.Header.Set(k, v)
		}
		resp, err = client.Do(req)
	}
	if err != nil {
		return map[string]any{"ok": false, "error": err.Error()}, nil
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
	return map[string]any{
		"ok":     resp.StatusCode >= 200 && resp.StatusCode < 500,
		"status": resp.StatusCode, "latency_ms": time.Since(start).Milliseconds(),
	}, nil
}

func registerAIRoutes(a *App) {
	a.Get("/api/ai/overview", perm("ai"), func(w http.ResponseWriter, r *http.Request) error {
		store := loadAIStore()
		return WriteJSON(w, 200, map[string]any{
			"accounts": len(store.Accounts), "mcp": len(store.MCP), "gpu": aiGPUStatus(),
		})
	})
	a.Get("/api/ai/accounts", perm("ai"), func(w http.ResponseWriter, r *http.Request) error {
		store := loadAIStore()
		out := []map[string]any{}
		for _, item := range store.Accounts {
			out = append(out, aiAccountPublic(item))
		}
		return WriteJSON(w, 200, map[string]any{"accounts": out})
	})
	a.Post("/api/ai/accounts", perm("ai"), func(w http.ResponseWriter, r *http.Request) error {
		var req aiAccount
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		name := strings.TrimSpace(req.Name)
		if name == "" || len(name) > 64 {
			return NewHTTPError(400, "名称必填且不超过 64 字")
		}
		if err := validateAIURL(req.BaseURL); err != nil {
			return err
		}
		req.ID = "ai_" + randomHex(8)
		req.Name = name
		req.BaseURL = strings.TrimRight(strings.TrimSpace(req.BaseURL), "/")
		req.Model = strings.TrimSpace(req.Model)
		req.CreatedAt = time.Now().Format("2006-01-02 15:04:05")
		store := loadAIStore()
		store.Accounts = append(store.Accounts, req)
		if err := saveAIStore(store); err != nil {
			return err
		}
		return WriteJSON(w, 200, aiAccountPublic(req))
	})
	a.Put("/api/ai/accounts/{account_id}", perm("ai"), func(w http.ResponseWriter, r *http.Request) error {
		id := strings.TrimSpace(PathParam(r, "account_id"))
		// enabled 用指针区分「未提交」与「显式关闭」：只改名称/模型时
		// 不应把账号顺带禁用。
		var req struct {
			Name    string `json:"name"`
			BaseURL string `json:"base_url"`
			APIKey  string `json:"api_key"`
			Model   string `json:"model"`
			Enabled *bool  `json:"enabled"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if name := strings.TrimSpace(req.Name); len(name) > 64 {
			return NewHTTPError(400, "名称不超过 64 字")
		}
		store := loadAIStore()
		found := false
		for i := range store.Accounts {
			if store.Accounts[i].ID != id {
				continue
			}
			if strings.TrimSpace(req.Name) != "" {
				store.Accounts[i].Name = strings.TrimSpace(req.Name)
			}
			if strings.TrimSpace(req.BaseURL) != "" {
				if err := validateAIURL(req.BaseURL); err != nil {
					return err
				}
				store.Accounts[i].BaseURL = strings.TrimRight(strings.TrimSpace(req.BaseURL), "/")
			}
			if strings.TrimSpace(req.Model) != "" {
				store.Accounts[i].Model = strings.TrimSpace(req.Model)
			}
			// api_key 留空表示保持原值。
			if strings.TrimSpace(req.APIKey) != "" {
				store.Accounts[i].APIKey = strings.TrimSpace(req.APIKey)
			}
			if req.Enabled != nil {
				store.Accounts[i].Enabled = *req.Enabled
			}
			found = true
			break
		}
		if !found {
			return NewHTTPError(404, "模型账号不存在")
		}
		if err := saveAIStore(store); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/ai/accounts/{account_id}/delete", perm("ai"), func(w http.ResponseWriter, r *http.Request) error {
		id := strings.TrimSpace(PathParam(r, "account_id"))
		store := loadAIStore()
		out := store.Accounts[:0]
		for _, item := range store.Accounts {
			if item.ID != id {
				out = append(out, item)
			}
		}
		store.Accounts = out
		if err := saveAIStore(store); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/ai/accounts/{account_id}/test", perm("ai"), func(w http.ResponseWriter, r *http.Request) error {
		id := strings.TrimSpace(PathParam(r, "account_id"))
		var req struct {
			Prompt string `json:"prompt"`
		}
		_ = DecodeJSON(r, &req)
		var account *aiAccount
		store := loadAIStore()
		for i := range store.Accounts {
			if store.Accounts[i].ID == id {
				account = &store.Accounts[i]
				break
			}
		}
		if account == nil {
			return NewHTTPError(404, "模型账号不存在")
		}
		if !account.Enabled {
			return NewHTTPError(400, "该账号已禁用")
		}
		res, err := aiChat(*account, req.Prompt)
		if err != nil {
			return err
		}
		return WriteJSON(w, 200, res)
	})
	a.Get("/api/ai/gpu", perm("ai"), func(w http.ResponseWriter, r *http.Request) error {
		return WriteJSON(w, 200, aiGPUStatus())
	})
	a.Get("/api/ai/mcp", perm("ai"), func(w http.ResponseWriter, r *http.Request) error {
		store := loadAIStore()
		out := []map[string]any{}
		for _, item := range store.MCP {
			out = append(out, aiMCPPublic(item))
		}
		return WriteJSON(w, 200, map[string]any{"servers": out})
	})
	a.Post("/api/ai/mcp", perm("ai"), func(w http.ResponseWriter, r *http.Request) error {
		var req aiMCP
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		name := strings.TrimSpace(req.Name)
		if name == "" || len(name) > 64 {
			return NewHTTPError(400, "名称必填且不超过 64 字")
		}
		if err := validateAIURL(req.URL); err != nil {
			return err
		}
		if req.Headers == nil {
			req.Headers = map[string]string{}
		}
		req.ID = "mcp_" + randomHex(8)
		req.Name = name
		req.URL = strings.TrimSpace(req.URL)
		req.CreatedAt = time.Now().Format("2006-01-02 15:04:05")
		store := loadAIStore()
		store.MCP = append(store.MCP, req)
		if err := saveAIStore(store); err != nil {
			return err
		}
		return WriteJSON(w, 200, aiMCPPublic(req))
	})
	a.Post("/api/ai/mcp/{mcp_id}/delete", perm("ai"), func(w http.ResponseWriter, r *http.Request) error {
		id := strings.TrimSpace(PathParam(r, "mcp_id"))
		store := loadAIStore()
		out := store.MCP[:0]
		for _, item := range store.MCP {
			if item.ID != id {
				out = append(out, item)
			}
		}
		store.MCP = out
		if err := saveAIStore(store); err != nil {
			return err
		}
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
	a.Post("/api/ai/mcp/{mcp_id}/test", perm("ai"), func(w http.ResponseWriter, r *http.Request) error {
		id := strings.TrimSpace(PathParam(r, "mcp_id"))
		store := loadAIStore()
		for _, item := range store.MCP {
			if item.ID == id {
				res, err := aiMCPTest(item)
				if err != nil {
					return err
				}
				return WriteJSON(w, 200, res)
			}
		}
		return NewHTTPError(404, "MCP 端点不存在")
	})
}
