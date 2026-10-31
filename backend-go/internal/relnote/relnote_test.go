package relnote

// 本文件用固定输入覆盖摘要生成的关键解析与渲染逻辑，不依赖真实 git 仓库和网络。

import (
	"strings"
	"testing"
)

// TestParseRemoteURL 覆盖本地 / CI 里常见的 remote 写法。
func TestParseRemoteURL(t *testing.T) {
	cases := []struct {
		raw    string
		host   string
		slug   string
		wanted bool
	}{
		{raw: "https://github.com/wuhulab/Graw.git", host: "github.com", slug: "wuhulab/Graw", wanted: true},
		{raw: "git@github.com:wuhulab/Graw.git", host: "github.com", slug: "wuhulab/Graw", wanted: true},
		{raw: "ssh://git@github.com/wuhulab/Graw.git", host: "github.com", slug: "wuhulab/Graw", wanted: true},
		{raw: "https://github.com/wuhulab/Graw/", host: "github.com", slug: "wuhulab/Graw", wanted: true},
		{raw: "https://gitlab.com/group/sub/repo.git", host: "gitlab.com", slug: "group/sub/repo", wanted: true},
		{raw: "https://github.com/wuhulab", wanted: false},
		{raw: "not-a-remote", wanted: false},
	}
	for _, item := range cases {
		host, slug, ok := parseRemoteURL(item.raw)
		if ok != item.wanted {
			t.Fatalf("parseRemoteURL(%q) ok = %v，期望 %v", item.raw, ok, item.wanted)
		}
		if !item.wanted {
			continue
		}
		if host != item.host || slug != item.slug {
			t.Fatalf("parseRemoteURL(%q) = %s/%s，期望 %s/%s", item.raw, host, slug, item.host, item.slug)
		}
	}
}

// TestPreviousInTagList 验证按版本降序的 tag 列表里能取到上一个版本。
func TestPreviousInTagList(t *testing.T) {
	tags := []string{"v1.9.2", "v1.9.0", "v1.8.2"}
	cases := map[string]string{
		"v1.9.2": "v1.9.0",
		"v1.9.0": "v1.8.2",
		"v1.8.2": "", // 最后一个 tag 说明是首个版本
		"v2.0.0": "", // 目标不在列表里，按首个版本处理
	}
	for tag, wanted := range cases {
		if got := previousInTagList(tags, tag); got != wanted {
			t.Fatalf("previousInTagList(%q) = %q，期望 %q", tag, got, wanted)
		}
	}
}

// TestParseCommits 验证控制字符切分能保住带换行的提交正文。
func TestParseCommits(t *testing.T) {
	raw := "abcdef1\x1f张三\x1fzhang@example.com\x1fFix: 修复登录\x1fCo-authored-by: 李四 <li@users.noreply.github.com>\n\x1e\n" +
		"1234567\x1f李四\x1fli@users.noreply.github.com\x1fADD: 添加节点总览\x1f\n\x1e\n"
	commits := parseCommits(raw)
	if len(commits) != 2 {
		t.Fatalf("提交数 = %d，期望 2", len(commits))
	}
	if commits[0].Hash != "abcdef1" || commits[0].Author != "张三" {
		t.Fatalf("第一条提交解析错误: %+v", commits[0])
	}
	if commits[0].Body != "Co-authored-by: 李四 <li@users.noreply.github.com>" {
		t.Fatalf("提交正文解析错误: %q", commits[0].Body)
	}
	if commits[1].Subject != "ADD: 添加节点总览" || commits[1].Body != "" {
		t.Fatalf("第二条提交解析错误: %+v", commits[1])
	}
}

// TestClassifySubject 覆盖英文 / 中文前缀、损坏前缀与无前缀提交。
func TestClassifySubject(t *testing.T) {
	cases := []struct {
		subject string
		title   string
		message string
	}{
		{subject: "Fix: 修复一些安全问题", title: "修复", message: "修复一些安全问题"},
		{subject: "ADD:添加vpn网络代理内网支持", title: "新功能", message: "添加vpn网络代理内网支持"},
		{subject: "feat(ui)!: 新增深色模式", title: "新功能", message: "新增深色模式"},
		{subject: "修复：站点配置写回", title: "修复", message: "站点配置写回"},
		{subject: "chore(deps): 升级依赖", title: "构建与杂项", message: "升级依赖"},
		{subject: "v1.9.0:Go语言后端测试部署版本", title: otherCategory, message: "v1.9.0:Go语言后端测试部署版本"},
		{subject: "随便改改", title: otherCategory, message: "随便改改"},
	}
	for _, item := range cases {
		title, message := classifySubject(item.subject)
		if title != item.title || message != item.message {
			t.Fatalf("classifySubject(%q) = %q / %q，期望 %q / %q", item.subject, title, message, item.title, item.message)
		}
	}
}

// TestCollectContributors 验证作者、共同作者都计入，并按邮箱归并、按提交数排序。
func TestCollectContributors(t *testing.T) {
	commits := []Commit{
		{Hash: "a", Author: "张三", Email: "zhang@example.com", Subject: "Fix: 修复登录"},
		{Hash: "b", Author: "李四", Email: "12345+li@users.noreply.github.com", Subject: "ADD: 添加总览",
			Body: "Co-authored-by: 王五 <wang@example.com>"},
		{Hash: "c", Author: "张三", Email: "zhang@example.com", Subject: "Fix: 修复退出"},
	}
	list := collectContributors(commits)
	if len(list) != 3 {
		t.Fatalf("贡献者数 = %d，期望 3", len(list))
	}
	if list[0].Name != "张三" || list[0].Commits != 2 {
		t.Fatalf("第一位贡献者应为提交数最多的张三: %+v", list[0])
	}
	if list[0].Login != "" {
		t.Fatalf("普通邮箱不应推导出 GitHub 用户名: %+v", list[0])
	}
	for _, contributor := range list[1:] {
		if contributor.Commits != 1 {
			t.Fatalf("并列贡献者提交数应为 1: %+v", contributor)
		}
		if strings.HasPrefix(contributor.Name, "李四") && contributor.Login != "li" {
			t.Fatalf("李四应推导出 GitHub 用户名 li: %+v", contributor)
		}
	}
}

// TestLoginFromEmail 覆盖 GitHub 隐私邮箱的两种格式。
func TestLoginFromEmail(t *testing.T) {
	cases := map[string]string{
		"12345+octocat@users.noreply.github.com": "octocat",
		"octocat@users.noreply.github.com":       "octocat",
		"zhang@example.com":                      "",
	}
	for email, wanted := range cases {
		if got := loginFromEmail(email); got != wanted {
			t.Fatalf("loginFromEmail(%q) = %q，期望 %q", email, got, wanted)
		}
	}
}

// TestRenderMarkdown 验证分节、逐条提交、贡献者与 compare 链接都能正确出现。
func TestRenderMarkdown(t *testing.T) {
	commits := []Commit{
		{Hash: "abcdef1234567", Author: "张三", Email: "zhang@example.com", Subject: "Fix: 修复登录"},
		{Hash: "1234567890123", Author: "李四", Email: "12345+li@users.noreply.github.com", Subject: "ADD: 添加节点总览"},
		{Hash: "9999999999999", Author: "张三", Email: "zhang@example.com", Subject: "v1.9.1: 小修文档",
			Body: "Co-authored-by: 王五 <wang@example.com>"},
	}
	markdown := renderMarkdown(notesInput{
		Tag:     "v1.9.2",
		From:    "v1.9.0",
		Host:    "github.com",
		Slug:    "wuhulab/Graw",
		Commits: commits,
	})

	wanted := []string{
		"# v1.9.2 变更摘要",
		"共 3 次提交，3 位贡献者",
		"### 新功能",
		"### 修复",
		"### 其他变更",
		"## 贡献者",
		"李四（@li）：1 次提交",
		"https://github.com/wuhulab/Graw/compare/v1.9.0...v1.9.2",
		"https://github.com/wuhulab/Graw/commit/abcdef1234567",
	}
	for _, text := range wanted {
		if !strings.Contains(markdown, text) {
			t.Fatalf("摘要缺少 %q:\n%s", text, markdown)
		}
	}
	if strings.Contains(markdown, "### 安全\n\n") {
		t.Fatalf("没有安全类提交时不应输出空分节:\n%s", markdown)
	}
	if strings.Contains(markdown, "### 回滚\n\n") {
		t.Fatalf("没有回滚提交时不应输出空分节:\n%s", markdown)
	}
}
