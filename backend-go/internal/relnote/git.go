package relnote

// 本文件只负责和 git 对话：定位仓库根目录、解析 tag、读取区间提交、提取共同作者、解析 remote 地址。
// 全部走 git 命令行，不引入 git 库——CI 精简环境里少一个依赖，排查时也只需看这几条命令。
//
// 关键选择：
//   - git log 输出用控制字符切分（0x1f 字段分隔、0x1e 记录分隔），而不是按换行切。
//     提交正文本身带换行，按行解析会把一条提交拆成多条。
//   - Co-authored-by 只认标准 trailer，大小写不敏感。

import (
	"bytes"
	"errors"
	"fmt"
	"net/url"
	"os/exec"
	"regexp"
	"strings"
)

// Commit 是一条提交里摘要会用到的字段。
type Commit struct {
	Hash    string // 完整提交哈希，渲染时截成 7 位显示
	Author  string // 提交作者署名
	Email   string // 作者邮箱，同时作为贡献者归并键
	Subject string // 提交标题
	Body    string // 提交正文，用来找 Co-authored-by
}

// gitRepo 绑定一个仓库根目录，所有命令都在该目录执行，避免受调用方 cwd 影响。
type gitRepo struct {
	dir string
}

// openGitRepo 定位当前目录所在的仓库根目录。
func openGitRepo() (*gitRepo, error) {
	out, err := runGit("", "rev-parse", "--show-toplevel")
	if err != nil {
		return nil, errors.New("当前目录不在 git 仓库内，请在仓库里执行（例如 cd backend-go）")
	}
	dir := strings.TrimSpace(out)
	if dir == "" {
		return nil, errors.New("git 没有返回仓库根目录")
	}
	return &gitRepo{dir: dir}, nil
}

// runGit 执行一条 git 命令；dir 为空表示沿用当前目录。
// 失败时把 git 自己的 stderr 带回来，报错才能直接看出是「tag 不存在」还是「没有提交」。
func runGit(dir string, args ...string) (string, error) {
	cmd := exec.Command("git", args...)
	if dir != "" {
		cmd.Dir = dir
	}
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		message := strings.TrimSpace(stderr.String())
		if message != "" {
			return "", fmt.Errorf("git %s: %s", strings.Join(args, " "), message)
		}
		return "", fmt.Errorf("git %s: %w", strings.Join(args, " "), err)
	}
	return string(out), nil
}

func (r *gitRepo) run(args ...string) (string, error) {
	return runGit(r.dir, args...)
}

// currentTag 返回 HEAD 精确指向的 tag；HEAD 不在 tag 上时提示用户显式指定。
func (r *gitRepo) currentTag() (string, error) {
	out, err := r.run("describe", "--tags", "--exact-match", "HEAD")
	if err != nil {
		return "", errors.New("当前 HEAD 没有对应的 tag，请用 --tag 指定要生成摘要的版本")
	}
	tag := strings.TrimSpace(out)
	if tag == "" {
		return "", errors.New("当前 HEAD 没有对应的 tag，请用 --tag 指定要生成摘要的版本")
	}
	return tag, nil
}

// previousTag 找目标 tag 之前的上一个 tag（返回空串表示这是首个版本）。
func (r *gitRepo) previousTag(tag string) (string, error) {
	// 首选 describe：从 tag 的父提交沿历史回溯，拿到最近的可达 tag，能正确处理非线性的合并历史。
	if out, err := r.run("describe", "--tags", "--abbrev=0", tag+"^"); err == nil {
		if prev := strings.TrimSpace(out); prev != "" {
			return prev, nil
		}
	}
	// 兜底：tag 在父提交上不可达时（例如 tag 直接打在首个提交），按版本排序取前一个。
	out, err := r.run("tag", "--list", "--sort=-v:refname")
	if err != nil {
		return "", fmt.Errorf("读取 tag 列表失败: %w", err)
	}
	return previousInTagList(strings.Fields(out), tag), nil
}

// previousInTagList 在按版本降序排列的 tag 列表里取 tag 的下一个（即旧版本）。
// 目标 tag 不在列表里时返回空串，调用方按「首个版本」处理。
func previousInTagList(tags []string, tag string) string {
	for i, current := range tags {
		if current == tag && i+1 < len(tags) {
			return tags[i+1]
		}
	}
	return ""
}

// commits 读取 from..to 区间的提交；from 为空时读取 to 的全部历史。
func (r *gitRepo) commits(from, to string, includeMerges bool) ([]Commit, error) {
	rangeSpec := to
	if from != "" {
		rangeSpec = from + ".." + to
	}
	args := []string{"log"}
	if !includeMerges {
		args = append(args, "--no-merges") // 合并提交没有业务内容，默认剔除
	}
	args = append(args, "--format=%H%x1f%an%x1f%ae%x1f%s%x1f%b%x1e", rangeSpec)
	out, err := r.run(args...)
	if err != nil {
		return nil, fmt.Errorf("读取提交失败（范围 %s）: %w", rangeSpec, err)
	}
	return parseCommits(out), nil
}

// parseCommits 把 git log 的原始输出切成提交列表；独立成函数便于单测。
func parseCommits(raw string) []Commit {
	records := strings.Split(raw, "\x1e")
	commits := make([]Commit, 0, len(records))
	for _, record := range records {
		record = strings.Trim(record, "\r\n")
		if record == "" {
			continue
		}
		fields := strings.SplitN(record, "\x1f", 5)
		if len(fields) < 4 {
			continue // 结构不完整，跳过而不是猜
		}
		commit := Commit{
			Hash:    strings.TrimSpace(fields[0]),
			Author:  strings.TrimSpace(fields[1]),
			Email:   strings.TrimSpace(fields[2]),
			Subject: strings.TrimSpace(fields[3]),
		}
		if len(fields) == 5 {
			commit.Body = strings.TrimSpace(fields[4])
		}
		if commit.Hash == "" {
			continue
		}
		commits = append(commits, commit)
	}
	return commits
}

// remoteURL 读取 remote 地址；没有 origin 时退而取第一个 remote，兼容 fork 后改了远端名的情况。
func (r *gitRepo) remoteURL() (string, error) {
	if out, err := r.run("remote", "get-url", "origin"); err == nil {
		if value := strings.TrimSpace(out); value != "" {
			return value, nil
		}
	}
	out, err := r.run("remote")
	if err != nil {
		return "", err
	}
	names := strings.Fields(out)
	if len(names) == 0 {
		return "", errors.New("仓库没有配置 remote")
	}
	value, err := r.run("remote", "get-url", names[0])
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(value), nil
}

// person 是提交里出现的一位署名者（作者或共同作者）。
type person struct {
	Name  string
	Email string
}

// coAuthorPattern 匹配 Git 标准的共同作者 trailer，例如：
//
//	Co-authored-by: Alice <alice@example.com>
var coAuthorPattern = regexp.MustCompile(`(?mi)^co-authored-by:\s*(.+?)\s*<([^<>\s]+)>`)

// parseCoAuthors 从提交正文里提取共同作者。
func parseCoAuthors(body string) []person {
	matches := coAuthorPattern.FindAllStringSubmatch(body, -1)
	people := make([]person, 0, len(matches))
	for _, match := range matches {
		people = append(people, person{
			Name:  strings.TrimSpace(match[1]),
			Email: strings.TrimSpace(match[2]),
		})
	}
	return people
}

// parseRemoteURL 把常见 remote 写法解析成「主机名 + 仓库路径」。
//
// 支持的写法：
//
//	https://github.com/wuhulab/Graw.git
//	ssh://git@github.com/wuhulab/Graw.git
//	git@github.com:wuhulab/Graw.git
func parseRemoteURL(raw string) (host, slug string, ok bool) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return "", "", false
	}
	// scp 风格没有 scheme，url.Parse 会把它当路径，需要单独拆。
	if !strings.Contains(raw, "://") {
		at := strings.Index(raw, "@")
		colon := strings.Index(raw, ":")
		if at < 0 || colon <= at {
			return "", "", false
		}
		return cleanRepoParts(raw[at+1:colon], raw[colon+1:])
	}
	parsed, err := url.Parse(raw)
	if err != nil {
		return "", "", false
	}
	return cleanRepoParts(parsed.Hostname(), parsed.Path)
}

// cleanRepoParts 清掉路径两侧的斜杠与 .git 后缀，并确认至少有 owner/repo 两段。
func cleanRepoParts(host, path string) (string, string, bool) {
	host = strings.TrimSpace(host)
	path = cleanSlug(path)
	if host == "" || path == "" {
		return "", "", false
	}
	if !strings.Contains(path, "/") {
		return "", "", false
	}
	return host, path, true
}

// parseHost 从 https://github.com 这类地址里取出主机名。
func parseHost(raw string) (string, bool) {
	parsed, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || parsed.Hostname() == "" {
		return "", false
	}
	return parsed.Hostname(), true
}
