package relnote

// 本文件是 graw-relnote 命令的编排层：解析参数 → 读取 git 提交 → 渲染 Markdown →（可选）发布 GitHub Release。
//
// 用法（在仓库任意位置执行，要求本机可调用 git）：
//
//	cd backend-go
//	go run ./cmd/relnote --tag v1.9.2 --from v1.9.0        # 只预览，Markdown 打印到标准输出
//	go run ./cmd/relnote --tag v1.9.2 --output notes.md    # 写入文件
//	GITHUB_TOKEN=xxx go run ./cmd/relnote --tag v1.9.2 --create   # 创建 GitHub Release
//
// 打 tag 后由 .github/workflows/release.yml 自动以 --create 方式执行；
// 同一 tag 重复执行会更新已有 Release（见 github.go），可以安全重跑。
//
// 退出码约定：0 成功；1 业务失败（git 读取 / 发布失败）；2 参数错误。

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
)

// Options 是一次摘要生成的全部输入；零值可用，缺省项会在 Build 里自动推导。
type Options struct {
	Tag           string // 目标 tag；留空时取 HEAD 精确指向的 tag
	From          string // 起始 ref（不含）；留空时自动取目标 tag 的上一个 tag
	RepoSlug      string // owner/repo；留空时依次读 GITHUB_REPOSITORY、git remote
	Token         string // GitHub Token；留空时读环境变量或本机 gh 登录态
	APIBase       string // GitHub API 根地址；留空时读 GITHUB_API_URL，再退回官方地址
	Name          string // Release 标题；留空时与 tag 相同
	Output        string // 输出路径；"-" 表示标准输出
	Create        bool   // 是否真的创建 GitHub Release
	Draft         bool   // 创建为草稿
	Prerelease    bool   // 标记为预发布
	IncludeMerges bool   // 是否把合并提交也列进摘要
}

// Result 是生成结果，Run 用它写文件 / 打印发布地址。
type Result struct {
	Tag              string // 实际使用的 tag
	From             string // 实际使用的起始 ref（可能为空：首个版本）
	Host             string // 代码托管主机名，用于拼提交/对比链接
	Slug             string // owner/repo，用于拼链接与调用 API
	Markdown         string // 渲染好的变更摘要
	CommitCount      int    // 纳入摘要的提交数
	ContributorCount int    // 贡献者人数（含 Co-authored-by）
	ReleaseURL       string // 创建成功后的 Release 页面地址
}

// Run 执行命令并返回进程退出码，stdout/stderr 显式传入便于单测。
func Run(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("graw-relnote", flag.ContinueOnError)
	fs.SetOutput(stderr)
	opts := Options{Output: "-"}
	fs.StringVar(&opts.Tag, "tag", "", "目标 tag（默认取 HEAD 指向的 tag）")
	fs.StringVar(&opts.From, "from", "", "起始 ref（不含），默认自动取目标 tag 的上一个 tag")
	fs.StringVar(&opts.RepoSlug, "repo", "", "GitHub 仓库 owner/repo（默认读 GITHUB_REPOSITORY 或 git remote）")
	fs.StringVar(&opts.Token, "token", "", "GitHub Token（默认读 GITHUB_TOKEN / GH_TOKEN / gh auth token）")
	fs.StringVar(&opts.Name, "name", "", "Release 标题（默认与 tag 相同）")
	fs.StringVar(&opts.Output, "output", "-", "Markdown 输出路径，- 表示标准输出")
	fs.BoolVar(&opts.Create, "create", false, "创建 GitHub Release（不加时只输出摘要）")
	fs.BoolVar(&opts.Draft, "draft", false, "创建为草稿 Release")
	fs.BoolVar(&opts.Prerelease, "prerelease", false, "标记为预发布")
	fs.BoolVar(&opts.IncludeMerges, "include-merges", false, "把合并提交也列进摘要")
	fs.Usage = func() {
		fmt.Fprintln(stderr, "用法: graw-relnote [--tag v1.9.2] [--from v1.9.0] [--output notes.md] [--create]")
		fmt.Fprintln(stderr, "默认只输出摘要；加 --create 才会调用 GitHub API 创建 Release。")
		fs.PrintDefaults()
	}
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if fs.NArg() > 0 {
		fmt.Fprintf(stderr, "错误：无法识别的参数 %q\n", fs.Args())
		fs.Usage()
		return 2
	}

	result, err := Build(context.Background(), opts)
	if err != nil {
		fmt.Fprintf(stderr, "错误：%v\n", err)
		return 1
	}
	if err := writeOutput(opts.Output, result.Markdown, stdout, stderr); err != nil {
		fmt.Fprintf(stderr, "错误：%v\n", err)
		return 1
	}
	if opts.Create {
		fmt.Fprintf(stderr, "已发布 GitHub Release：%s\n", result.ReleaseURL)
	}
	return 0
}

// writeOutput 把摘要写到标准输出或文件；文件模式下不重复打印，避免和 CI 摘要重复。
func writeOutput(path, markdown string, stdout, stderr io.Writer) error {
	if path == "" || path == "-" {
		_, err := fmt.Fprint(stdout, markdown)
		return err
	}
	if err := os.WriteFile(path, []byte(markdown), 0o644); err != nil {
		return fmt.Errorf("写入 %s 失败: %w", path, err)
	}
	fmt.Fprintf(stderr, "已写入变更摘要：%s\n", path)
	return nil
}

// Build 完成「读 git → 渲染 → 发布」全流程，不直接读命令行参数，方便测试与复用。
func Build(ctx context.Context, opts Options) (Result, error) {
	repo, err := openGitRepo()
	if err != nil {
		return Result{}, err
	}

	tag := opts.Tag
	if tag == "" {
		tag, err = repo.currentTag()
		if err != nil {
			return Result{}, err
		}
	}
	from := opts.From
	if from == "" {
		from, err = repo.previousTag(tag)
		if err != nil {
			return Result{}, err
		}
	}

	commits, err := repo.commits(from, tag, opts.IncludeMerges) // 读取 tag 区间内的提交
	if err != nil {
		return Result{}, err
	}
	host, slug := resolveRepo(opts.RepoSlug, repo) // 链接与 API 都需要 owner/repo
	markdown := renderMarkdown(notesInput{Tag: tag, From: from, Host: host, Slug: slug, Commits: commits})
	result := Result{
		Tag:              tag,
		From:             from,
		Host:             host,
		Slug:             slug,
		Markdown:         markdown,
		CommitCount:      len(commits),
		ContributorCount: len(collectContributors(commits)),
	}
	if !opts.Create {
		return result, nil
	}

	// 发布前再解析 Token，避免「只预览」的日常使用无谓地调用 gh。
	// 优先级：命令行 --token > 环境变量 > 本机 gh 登录态。
	token := opts.Token
	if token == "" {
		token = envToken()
	}
	if token == "" {
		token = ghAuthToken()
	}
	if token == "" {
		return Result{}, errors.New("创建 Release 需要 GitHub Token：设置 GITHUB_TOKEN / GH_TOKEN，或先执行 gh auth login")
	}
	if slug == "" {
		return Result{}, errors.New("无法从 git remote 识别仓库，请用 --repo owner/repo 指定，例如 wuhulab/Graw")
	}
	name := opts.Name
	if name == "" {
		name = tag
	}
	release, err := publishRelease(ctx, opts.APIBase, token, slug, releaseRequest{
		TagName:    tag,
		Name:       name,
		Body:       markdown,
		Draft:      opts.Draft,
		Prerelease: opts.Prerelease,
	})
	if err != nil {
		return Result{}, err
	}
	result.ReleaseURL = release.HTMLURL
	return result, nil
}

// envToken 读取 CI / 本地环境里最常见的两种 Token 变量名。
func envToken() string {
	for _, key := range []string{"GITHUB_TOKEN", "GH_TOKEN"} {
		if value := strings.TrimSpace(os.Getenv(key)); value != "" {
			return value
		}
	}
	return ""
}

// ghAuthToken 兜底复用本机 gh 的登录令牌，省去用户每次导出 Token。
// gh 未安装或未登录时静默返回空串，是否报错交给调用方统一提示。
func ghAuthToken() string {
	out, err := exec.Command("gh", "auth", "token").Output()
	if err != nil {
		return ""
	}
	token := strings.TrimSpace(string(out))
	// gh 可能在输出里混入提示文字；只接受单行、不含空白的值，避免把整段文本当 Token 发出去。
	if token == "" || strings.ContainsAny(token, " \t\r\n") {
		return ""
	}
	return token
}

// resolveRepo 决定摘要里使用哪个仓库地址，优先级：命令行 → CI 环境 → git remote。
func resolveRepo(explicit string, repo *gitRepo) (host, slug string) {
	if explicit != "" {
		return serverHost(), cleanSlug(explicit)
	}
	if value := strings.TrimSpace(os.Getenv("GITHUB_REPOSITORY")); value != "" {
		return serverHost(), cleanSlug(value)
	}
	raw, err := repo.remoteURL()
	if err != nil {
		return "", ""
	}
	remoteHost, remoteSlug, ok := parseRemoteURL(raw)
	if !ok {
		return "", ""
	}
	return remoteHost, remoteSlug
}

// serverHost 返回代码托管主机名：CI 里跟随 GITHUB_SERVER_URL（兼容 GitHub Enterprise），本地默认 github.com。
func serverHost() string {
	raw := strings.TrimSpace(os.Getenv("GITHUB_SERVER_URL"))
	if raw != "" {
		if host, ok := parseHost(raw); ok {
			return host
		}
	}
	return "github.com"
}

// cleanSlug 把 owner/repo 形式整理干净，容忍前后斜杠和 .git 后缀。
func cleanSlug(value string) string {
	value = strings.TrimSpace(value)
	value = strings.TrimPrefix(value, "/")
	value = strings.TrimSuffix(value, "/")
	value = strings.TrimSuffix(value, ".git")
	return value
}
