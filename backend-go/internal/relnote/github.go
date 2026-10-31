package relnote

// 本文件负责把摘要发布到 GitHub Releases，只依赖标准库 net/http。
// 不引入官方 SDK：CI 里少一个模块，出问题时也只需看这一份请求代码。
//
// 幂等策略：同名 Release 已存在时 GitHub 返回 422，这里改为查询后 PATCH 更新，
// 因此重跑同一 tag 的工作流不会失败，也不会产生重复 Release。

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// releaseRequest 是创建 / 更新 Release 的请求体，只填摘要需要的字段。
type releaseRequest struct {
	TagName    string `json:"tag_name"`
	Name       string `json:"name"`
	Body       string `json:"body"`
	Draft      bool   `json:"draft"`
	Prerelease bool   `json:"prerelease"`
}

// releaseResponse 只取后续会用到的字段。
type releaseResponse struct {
	ID      int64  `json:"id"`
	HTMLURL string `json:"html_url"`
	TagName string `json:"tag_name"`
}

// defaultAPIBase 是本地运行的默认 GitHub API 地址；CI 里由 GITHUB_API_URL 覆盖。
const defaultAPIBase = "https://api.github.com"

// publishRelease 创建 Release；目标 tag 已有 Release 时改为更新其内容。
func publishRelease(ctx context.Context, apiBase, token, slug string, req releaseRequest) (releaseResponse, error) {
	apiBase = strings.TrimRight(strings.TrimSpace(apiBase), "/")
	if apiBase == "" {
		apiBase = defaultAPIBase
	}
	releasesURL := fmt.Sprintf("%s/repos/%s/releases", apiBase, slug)

	status, body, err := githubDo(ctx, http.MethodPost, releasesURL, token, req)
	if err != nil {
		return releaseResponse{}, err
	}
	if status == http.StatusUnprocessableEntity {
		// 422 最常见的原因是同名 Release 已存在：转成更新，保证重跑幂等。
		return updateExistingRelease(ctx, apiBase, token, slug, req)
	}
	if status != http.StatusCreated {
		return releaseResponse{}, errors.New(githubError(status, body))
	}
	var release releaseResponse
	if err := json.Unmarshal(body, &release); err != nil {
		return releaseResponse{}, fmt.Errorf("解析 GitHub 返回内容失败: %w", err)
	}
	return release, nil
}

// updateExistingRelease 按 tag 查到已有 Release，再 PATCH 覆盖标题与正文。
func updateExistingRelease(ctx context.Context, apiBase, token, slug string, req releaseRequest) (releaseResponse, error) {
	tagsURL := fmt.Sprintf("%s/repos/%s/releases/tags/%s", apiBase, slug, url.PathEscape(req.TagName))
	status, body, err := githubDo(ctx, http.MethodGet, tagsURL, token, nil)
	if err != nil {
		return releaseResponse{}, err
	}
	if status != http.StatusOK {
		return releaseResponse{}, fmt.Errorf("Release %s 已存在但无法读取（%s）", req.TagName, githubError(status, body))
	}
	var existing releaseResponse
	if err := json.Unmarshal(body, &existing); err != nil {
		return releaseResponse{}, fmt.Errorf("解析已有 Release 失败: %w", err)
	}

	status, body, err = githubDo(ctx, http.MethodPatch,
		fmt.Sprintf("%s/repos/%s/releases/%d", apiBase, slug, existing.ID), token, req)
	if err != nil {
		return releaseResponse{}, err
	}
	if status != http.StatusOK {
		return releaseResponse{}, errors.New(githubError(status, body))
	}
	var updated releaseResponse
	if err := json.Unmarshal(body, &updated); err != nil {
		return releaseResponse{}, fmt.Errorf("解析 GitHub 返回内容失败: %w", err)
	}
	return updated, nil
}

// githubDo 发一次 GitHub API 请求，返回状态码与响应体。
// 业务错误（4xx/5xx）不在这里吞掉，由调用方按状态码决定是重试更新还是直接报错。
func githubDo(ctx context.Context, method, endpoint, token string, payload any) (int, []byte, error) {
	var body io.Reader
	if payload != nil {
		data, err := json.Marshal(payload)
		if err != nil {
			return 0, nil, fmt.Errorf("编码请求体失败: %w", err)
		}
		body = bytes.NewReader(data)
	}
	request, err := http.NewRequestWithContext(ctx, method, endpoint, body)
	if err != nil {
		return 0, nil, fmt.Errorf("构造 GitHub 请求失败: %w", err)
	}
	request.Header.Set("Accept", "application/vnd.github+json")
	request.Header.Set("X-GitHub-Api-Version", "2022-11-28")
	request.Header.Set("User-Agent", "graw-relnote")
	if token != "" {
		request.Header.Set("Authorization", "Bearer "+token)
	}
	if payload != nil {
		request.Header.Set("Content-Type", "application/json")
	}

	client := &http.Client{Timeout: 30 * time.Second}
	response, err := client.Do(request)
	if err != nil {
		return 0, nil, fmt.Errorf("请求 GitHub 失败: %w", err)
	}
	defer response.Body.Close()
	// 限制读取上限，避免异常响应把内存撑爆；Release 正文远小于 1MB。
	data, err := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	if err != nil {
		return 0, nil, fmt.Errorf("读取 GitHub 响应失败: %w", err)
	}
	return response.StatusCode, data, nil
}

// githubError 把 GitHub 的错误响应压缩成一行，方便在 CI 日志里直接定位。
func githubError(status int, body []byte) string {
	message := strings.TrimSpace(string(body))
	runes := []rune(message)
	if len(runes) > 400 {
		message = string(runes[:400]) + "..."
	}
	return fmt.Sprintf("GitHub API 返回 %d：%s", status, message)
}
