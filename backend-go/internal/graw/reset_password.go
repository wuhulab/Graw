package graw

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"unicode/utf8"
)

// 离线密码重置（对应 Python backend/reset_password.py）。
// 不依赖正在运行的面板进程，直接读写 data/users.json；成功后可立即用新密码登录。

var (
	// ErrUsersFileMissing 数据目录中没有 users.json（面板从未启动过）。
	ErrUsersFileMissing = errors.New("未找到用户数据文件")
	// ErrUserNotFound 账号不存在。
	ErrUserNotFound = errors.New("账号不存在")
	// ErrPasswordTooShort 新密码少于 6 个字符。
	ErrPasswordTooShort = errors.New("密码长度不足 6 位")
	// ErrUsernameEmpty 未提供账号名。
	ErrUsernameEmpty = errors.New("账号不能为空")
)

// ResolveDataDir 返回与面板服务相同规则解析出的数据目录，供离线工具复用。
func ResolveDataDir() string {
	return GetConfig().DataDir
}

// UsersFilePath 返回指定数据目录下的 users.json 路径。
func UsersFilePath(dataDir string) string {
	return filepath.Join(dataDir, "users.json")
}

// LoadUsersFromDir 从指定数据目录读取用户表（离线工具用，不影响运行中的服务）。
func LoadUsersFromDir(dataDir string) (Users, error) {
	var users Users
	if err := ReadJSON(UsersFilePath(dataDir), &users); err != nil {
		return nil, err
	}
	return users, nil
}

// ResetUserPassword 使用面板默认数据目录重置指定账号密码。
func ResetUserPassword(username, password string) error {
	return ResetUserPasswordInDir(ResolveDataDir(), username, password)
}

// ResetUserPasswordInDir 离线重置指定数据目录中的账号密码。
//
// 与 Python reset_password.py 保持一致的语义：
//   - 账号必须已存在；
//   - 新密码至少 6 个字符；
//   - 重置后清除 must_change_password（登录后不再强制改密）；
//   - 不修改 token_version（与 Python 版一致，已签发令牌不会被强制失效）。
func ResetUserPasswordInDir(dataDir, username, password string) error {
	username = strings.TrimSpace(username)
	if username == "" {
		return ErrUsernameEmpty
	}
	users, err := LoadUsersFromDir(dataDir)
	if err != nil {
		if os.IsNotExist(err) {
			return fmt.Errorf("%w: %s", ErrUsersFileMissing, UsersFilePath(dataDir))
		}
		return fmt.Errorf("读取用户数据失败: %w", err)
	}
	if len(users) == 0 {
		return fmt.Errorf("%w: %s", ErrUsersFileMissing, UsersFilePath(dataDir))
	}
	user, ok := users[username]
	if !ok || user == nil {
		return fmt.Errorf("%w: '%s'", ErrUserNotFound, username)
	}
	if utf8.RuneCountInString(password) < 6 {
		return ErrPasswordTooShort
	}
	hash, err := HashPassword(password)
	if err != nil {
		return fmt.Errorf("生成密码哈希失败: %w", err)
	}
	user["password"] = hash
	user["must_change_password"] = false
	return WriteJSONFile(UsersFilePath(dataDir), users)
}
