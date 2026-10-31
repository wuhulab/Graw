package relnote

// 本文件把提交列表整理成 Release 摘要：按提交前缀归类、逐条列出每次提交、统计贡献者。
// 只做纯文本拼装、不访问网络，渲染结果可通过单测固定，不需要真实仓库。
//
// 输出结构：
//
//	# <tag> 变更摘要
//	> 变更范围与提交/贡献者数量
//	## 变更概览     各类别数量
//	### 新功能 / 修复 / ...   每条提交一行
//	## 贡献者       按提交数排序
//	**完整对比**    GitHub compare 链接

import (
	"fmt"
	"net/url"
	"regexp"
	"sort"
	"strings"
)

// category 描述摘要里的一个分节；Keys 是提交前缀，匹配时大小写不敏感。
type category struct {
	Title string
	Keys  []string
}

// categories 的顺序就是分节在摘要里的顺序；新增类别只改这里，渲染无需跟着改。
var categories = []category{
	{Title: "新功能", Keys: []string{"feat", "feature", "add", "新增"}},
	{Title: "修复", Keys: []string{"fix", "bugfix", "hotfix", "修复"}},
	{Title: "安全", Keys: []string{"security", "sec", "安全"}},
	{Title: "优化", Keys: []string{"perf", "opt", "optimize", "优化"}},
	{Title: "重构", Keys: []string{"refactor", "重构"}},
	{Title: "文档", Keys: []string{"docs", "doc", "文档"}},
	{Title: "测试", Keys: []string{"test", "测试"}},
	{Title: "构建与杂项", Keys: []string{"build", "ci", "chore", "style"}},
	{Title: "回滚", Keys: []string{"revert", "rollback", "回滚"}},
}

// otherCategory 收留没有约定前缀的提交，保证每条提交都出现在摘要里。
const otherCategory = "其他变更"

// section 是渲染时的一段提交。
type section struct {
	Title   string
	Commits []Commit
}

// notesInput 是渲染所需的全部数据；链接在 Host / Slug 为空时自动省略。
type notesInput struct {
	Tag     string
	From    string
	Host    string
	Slug    string
	Commits []Commit
}

// subjectPrefix 解析 Conventional Commits 风格的前缀：类型名 + 可选 (scope) + 可选 ! + 冒号。
// 类型名同时接受英文与中文（如「修复」），冒号兼容半角与全角。
var subjectPrefix = regexp.MustCompile(`^\s*([A-Za-z]+|[\p{Han}]+)(?:\([^)]*\))?!?\s*[:：]\s*(.*)$`)

// classifySubject 返回提交所属分节与去掉类型前缀后的说明。
// 未识别的前缀（例如 `v1.9.0:`）保留完整标题，避免把有效信息从摘要里删掉。
func classifySubject(subject string) (title, message string) {
	subject = strings.TrimSpace(subject)
	match := subjectPrefix.FindStringSubmatch(subject)
	if match == nil {
		return otherCategory, subject
	}
	kind := strings.ToLower(strings.TrimSpace(match[1]))
	for _, cat := range categories {
		for _, key := range cat.Keys {
			if kind == key {
				message = strings.TrimSpace(match[2])
				if message == "" {
					message = subject
				}
				return cat.Title, message
			}
		}
	}
	return otherCategory, subject
}

// splitSections 把提交按类别分组，空类别不输出。
func splitSections(commits []Commit) []section {
	grouped := map[string][]Commit{}
	for _, commit := range commits {
		title, message := classifySubject(commit.Subject)
		commit.Subject = message
		grouped[title] = append(grouped[title], commit)
	}
	sections := make([]section, 0, len(categories)+1)
	for _, cat := range categories {
		if list := grouped[cat.Title]; len(list) > 0 {
			sections = append(sections, section{Title: cat.Title, Commits: list})
		}
	}
	if list := grouped[otherCategory]; len(list) > 0 {
		sections = append(sections, section{Title: otherCategory, Commits: list})
	}
	return sections
}

// Contributor 是一位贡献者及其提交次数。
type Contributor struct {
	Name    string
	Login   string
	Email   string
	Commits int
}

// collectContributors 按邮箱归并作者与 Co-authored-by，提交数降序、同名次按名字排序。
func collectContributors(commits []Commit) []Contributor {
	byEmail := map[string]*Contributor{}
	for _, commit := range commits {
		addContributor(byEmail, commit.Author, commit.Email)
		for _, coAuthor := range parseCoAuthors(commit.Body) {
			addContributor(byEmail, coAuthor.Name, coAuthor.Email)
		}
	}
	list := make([]Contributor, 0, len(byEmail))
	for _, contributor := range byEmail {
		list = append(list, *contributor)
	}
	sort.Slice(list, func(i, j int) bool {
		if list[i].Commits != list[j].Commits {
			return list[i].Commits > list[j].Commits
		}
		return list[i].Name < list[j].Name
	})
	return list
}

// addContributor 累加一个人的提交数；邮箱为空时用名字归并，避免同一人被拆成多个条目。
func addContributor(byEmail map[string]*Contributor, name, email string) {
	email = strings.ToLower(strings.TrimSpace(email))
	name = strings.TrimSpace(name)
	key := email
	if key == "" {
		key = strings.ToLower(name)
	}
	if key == "" {
		return // 姓名和邮箱都为空的异常提交不计入贡献者
	}
	if contributor, ok := byEmail[key]; ok {
		contributor.Commits++
		return
	}
	if name == "" {
		name = email
	}
	byEmail[key] = &Contributor{
		Name:    name,
		Email:   email,
		Login:   loginFromEmail(email),
		Commits: 1,
	}
}

// noreplyEmail 从 GitHub 的隐私邮箱里提取用户名：
//
//	12345+octocat@users.noreply.github.com -> octocat
//	octocat@users.noreply.github.com        -> octocat
var noreplyEmail = regexp.MustCompile(`^(?:\d+\+)?([A-Za-z0-9-]+)@users\.noreply\.github\.com$`)

// loginFromEmail 推导 GitHub 用户名；普通邮箱返回空串。
func loginFromEmail(email string) string {
	match := noreplyEmail.FindStringSubmatch(strings.ToLower(strings.TrimSpace(email)))
	if match == nil {
		return ""
	}
	return match[1]
}

// renderMarkdown 生成最终摘要文本。
func renderMarkdown(in notesInput) string {
	sections := splitSections(in.Commits)
	contributors := collectContributors(in.Commits)

	var builder strings.Builder
	fmt.Fprintf(&builder, "# %s 变更摘要\n\n", in.Tag)
	if in.From != "" {
		fmt.Fprintf(&builder, "> 变更范围：`%s` → `%s`，共 %d 次提交，%d 位贡献者。\n\n",
			in.From, in.Tag, len(in.Commits), len(contributors))
	} else {
		fmt.Fprintf(&builder, "> 首个版本，共 %d 次提交，%d 位贡献者。\n\n", len(in.Commits), len(contributors))
	}

	builder.WriteString("## 变更概览\n\n")
	fmt.Fprintf(&builder, "- 提交总数：%d\n", len(in.Commits))
	fmt.Fprintf(&builder, "- 参与贡献者：%d\n", len(contributors))
	for _, sec := range sections {
		fmt.Fprintf(&builder, "- %s：%d 项\n", sec.Title, len(sec.Commits))
	}
	builder.WriteString("\n")

	for _, sec := range sections {
		fmt.Fprintf(&builder, "### %s\n\n", sec.Title)
		for _, commit := range sec.Commits {
			builder.WriteString(commitLine(commit, in))
			builder.WriteByte('\n')
		}
		builder.WriteString("\n")
	}

	if len(contributors) > 0 {
		builder.WriteString("## 贡献者\n\n")
		for _, contributor := range contributors {
			builder.WriteString(contributorLine(contributor))
			builder.WriteByte('\n')
		}
		builder.WriteString("\n")
	}

	if in.From != "" && in.Host != "" && in.Slug != "" {
		fmt.Fprintf(&builder, "**完整对比**：https://%s/%s/compare/%s...%s\n",
			in.Host, in.Slug, url.PathEscape(in.From), url.PathEscape(in.Tag))
	}
	return strings.TrimRight(builder.String(), "\n") + "\n"
}

// commitLine 渲染一条提交；能识别仓库时把短哈希链到对应提交页。
func commitLine(commit Commit, in notesInput) string {
	subject := strings.ReplaceAll(strings.TrimSpace(commit.Subject), "\n", " ")
	author := displayPerson(commit.Author, commit.Email)
	if in.Host != "" && in.Slug != "" {
		return fmt.Sprintf("- [`%s`](https://%s/%s/commit/%s) %s%s",
			shortHash(commit.Hash), in.Host, in.Slug, commit.Hash, subject, author)
	}
	return fmt.Sprintf("- `%s` %s%s", shortHash(commit.Hash), subject, author)
}

// displayPerson 渲染提交行尾的署名，优先使用能从邮箱推导出的 GitHub 用户名。
func displayPerson(name, email string) string {
	if login := loginFromEmail(email); login != "" {
		return fmt.Sprintf("（@%s）", login)
	}
	if name != "" {
		return fmt.Sprintf("（%s）", name)
	}
	return ""
}

// contributorLine 渲染贡献者一行；有 GitHub 用户名时同时展示姓名与 @。
func contributorLine(contributor Contributor) string {
	if contributor.Login != "" {
		return fmt.Sprintf("- %s（@%s）：%d 次提交", contributor.Name, contributor.Login, contributor.Commits)
	}
	return fmt.Sprintf("- %s：%d 次提交", contributor.Name, contributor.Commits)
}

// shortHash 截取 7 位短哈希，与 git 的默认简写一致。
func shortHash(hash string) string {
	if len(hash) <= 7 {
		return hash
	}
	return hash[:7]
}
