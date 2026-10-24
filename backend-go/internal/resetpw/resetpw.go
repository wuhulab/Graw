package resetpw

import (
	"bufio"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"
	"unicode/utf8"

	"golang.org/x/term"

	"graw-go/internal/graw"
)

// Run 执行命令行密码重置，返回进程退出码（0 成功，1 业务失败，2 参数错误）。
// stdin/stdout/stderr 显式传入，便于单元测试覆盖交互流程。
func Run(args []string, stdin io.Reader, stdout, stderr io.Writer, dataDir string) int {
	fs := flag.NewFlagSet("graw-reset-password", flag.ContinueOnError)
	fs.SetOutput(stderr)
	list := fs.Bool("list", false, "列出所有账号")
	fs.BoolVar(list, "l", false, "列出所有账号（简写）")
	fs.Usage = func() {
		fmt.Fprintln(stderr, "用法: graw-reset-password [账号名] [-l|--list]")
		fmt.Fprintln(stderr, "不带账号名时交互式选择账号；密码输入不回显。")
	}
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if fs.NArg() > 1 {
		fs.Usage()
		return 2
	}

	usersPath := graw.UsersFilePath(dataDir)
	users, err := graw.LoadUsersFromDir(dataDir)
	if err != nil || len(users) == 0 {
		fmt.Fprintf(stderr, "错误：未找到用户数据文件 %s\n", usersPath)
		return 1
	}

	names := sortedUsernames(users)
	if *list {
		printUsers(stdout, users, names)
		return 0
	}

	reader := bufio.NewReader(stdin)
	username := ""
	if fs.NArg() == 1 {
		username = fs.Arg(0)
	} else {
		fmt.Fprintf(stdout, "可用账号：%s\n", strings.Join(names, ", "))
		var rerr error
		username, rerr = readLine(reader, stdout, "请输入要重置密码的账号：")
		if rerr != nil {
			fmt.Fprintf(stderr, "错误：读取账号失败: %v\n", rerr)
			return 1
		}
	}
	username = strings.TrimSpace(username)
	if username == "" {
		fmt.Fprintln(stderr, "错误：账号不能为空")
		return 1
	}
	if _, ok := users[username]; !ok {
		fmt.Fprintf(stderr, "错误：账号 '%s' 不存在\n", username)
		fmt.Fprintf(stderr, "可用账号：%s\n", strings.Join(names, ", "))
		return 1
	}

	password, err := readPassword(reader, stdin, stdout, "请输入新密码（至少 6 位）：")
	if err != nil {
		fmt.Fprintf(stderr, "错误：读取密码失败: %v\n", err)
		return 1
	}
	if utf8.RuneCountInString(password) < 6 {
		fmt.Fprintln(stderr, "错误：密码长度不足 6 位")
		return 1
	}
	confirm, err := readPassword(reader, stdin, stdout, "请再次输入新密码：")
	if err != nil {
		fmt.Fprintf(stderr, "错误：读取密码失败: %v\n", err)
		return 1
	}
	if password != confirm {
		fmt.Fprintln(stderr, "错误：两次输入的密码不一致")
		return 1
	}
	if err := graw.ResetUserPasswordInDir(dataDir, username, password); err != nil {
		if errors.Is(err, graw.ErrUserNotFound) || errors.Is(err, graw.ErrPasswordTooShort) ||
			errors.Is(err, graw.ErrUsersFileMissing) {
			fmt.Fprintf(stderr, "错误：%v\n", err)
		} else {
			fmt.Fprintf(stderr, "错误：重置密码失败: %v\n", err)
		}
		return 1
	}
	fmt.Fprintf(stdout, "账号 '%s' 的密码已重置。\n", username)
	return 0
}

func sortedUsernames(users graw.Users) []string {
	names := make([]string, 0, len(users))
	for name := range users {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

func printUsers(out io.Writer, users graw.Users, names []string) {
	fmt.Fprintln(out, "已存在的账号：")
	for _, name := range names {
		u := users[name]
		role := u.Role()
		flagText := ""
		if u.Bool("must_change_password") {
			flagText = " [待改密]"
		}
		fmt.Fprintf(out, "  - %s (%s)%s\n", name, role, flagText)
	}
}

func readLine(reader *bufio.Reader, out io.Writer, prompt string) (string, error) {
	fmt.Fprint(out, prompt)
	line, err := reader.ReadString('\n')
	if err != nil && err != io.EOF {
		return "", err
	}
	return strings.TrimRight(line, "\r\n"), nil
}

// readPassword 终端下隐藏输入；管道/测试输入退化为普通读取。
func readPassword(reader *bufio.Reader, stdin io.Reader, out io.Writer, prompt string) (string, error) {
	if f, ok := stdin.(*os.File); ok && term.IsTerminal(int(f.Fd())) {
		fmt.Fprint(out, prompt)
		b, err := term.ReadPassword(int(f.Fd()))
		fmt.Fprintln(out)
		if err != nil {
			return "", err
		}
		return string(b), nil
	}
	return readLine(reader, out, prompt)
}
