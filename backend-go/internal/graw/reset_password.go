package graw

// 本文件（reset_password.go）提供离线重置账号密码的领域逻辑。
//
// 运行中的面板进程与 cmd/reset-password 共用这里的实现：直接读写 data/users.json，
// 不依赖服务是否在跑，也不需要 JWT 或数据库。核心流程：
//   ResolveDataDir/UsersFilePath 定位文件 → LoadUsersFromDir 读取 → 校验账号与密码长度
//   → HashPassword（bcrypt）→ 清除 must_change_password → WriteJSONFile 原子写回。
//
// 安全约束 / 兼容点：
//   - 密码长度按「字符」计（≥6），与 Python len() 语义对齐，中文密码按 1 字算；
//   - 不修改 token_version：与 Python 版一致，重置后已签发的 JWT 仍然有效，
//     若怀疑令牌泄露需要另行改密/使会话失效，这里不处理；
//   - 所有校验都在写盘前完成：失败时 users.json 保持原内容不变。

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
	// ErrUsersFileMissing 数据目录中没有 users.json，或用户表为空（面板从未启动过）。
	ErrUsersFileMissing = errors.New("未找到用户数据文件")
	// ErrUserNotFound 账号不存在。
	ErrUserNotFound = errors.New("账号不存在")
	// ErrPasswordTooShort 新密码少于 6 个字符。
	ErrPasswordTooShort = errors.New("密码长度不足 6 位")
	// ErrUsernameEmpty 未提供账号名（或只输入了空白）。
	ErrUsernameEmpty = errors.New("账号不能为空")
)

// ResolveDataDir 返回与面板服务相同规则解析出的数据目录，供离线工具复用。
//
// 推导规则见 GetConfig：GRAW_DATA_DIR > ../backend/data > ./data，
// 保证离线工具改的就是运行中服务读的那份 users.json。
func ResolveDataDir() string {
	return GetConfig().DataDir
}

// UsersFilePath 返回指定数据目录下的 users.json 路径。
func UsersFilePath(dataDir string) string {
	return filepath.Join(dataDir, "users.json")
}

// LoadUsersFromDir 从指定数据目录读取用户表（离线工具用，不影响运行中的服务）。
//
// 返回的 map 是纯内存副本，修改它不会自动写回磁盘；
// 文件不存在或 JSON 损坏时原样返回错误，调用方可用 IsNotExist/errors.Is 判定。
func LoadUsersFromDir(dataDir string) (Users, error) {
	var users Users
	if err := ReadJSON(UsersFilePath(dataDir), &users); err != nil {
		return nil, err
	}
	return users, nil
}

// ResetUserPassword 使用面板默认数据目录重置指定账号密码。
//
// 等价于 ResetUserPasswordInDir(ResolveDataDir(), username, password)。
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
//
// 失败行为：账号为空/不存在、密码过短、读写文件失败都会返回包装后的错误；
// 校验阶段失败不会改动 users.json。
func ResetUserPasswordInDir(dataDir, username, password string) error {
	// 去掉粘贴时常见的首尾空白，避免「admin 」这种肉眼不可见的账号名。
	username = strings.TrimSpace(username)
	if username == "" {
		return ErrUsernameEmpty
	}
	users, err := LoadUsersFromDir(dataDir)
	if err != nil {
		if os.IsNotExist(err) {
			// 换成带路径的统一错误，运维能直接看出该去哪建/找 users.json。
			return fmt.Errorf("%w: %s", ErrUsersFileMissing, UsersFilePath(dataDir))
		}
		return fmt.Errorf("读取用户数据失败: %w", err)
	}
	if len(users) == 0 {
		// 合法但为空的 {} 也视为未初始化：没有任何账号可重置。
		return fmt.Errorf("%w: %s", ErrUsersFileMissing, UsersFilePath(dataDir))
	}
	// users.json 以用户名为 map 键；值为 nil 时同样按「不存在」处理。
	user, ok := users[username]
	if !ok || user == nil {
		return fmt.Errorf("%w: '%s'", ErrUserNotFound, username)
	}
	// 按字符数而非字节数校验，中文密码与 Python 版 len() 口径一致。
	if utf8.RuneCountInString(password) < 6 {
		return ErrPasswordTooShort
	}
	hash, err := HashPassword(password)
	if err != nil {
		return fmt.Errorf("生成密码哈希失败: %w", err)
	}
	user["password"] = hash
	// 离线重置视为管理员已掌握新密码，登录后不再强制改密。
	user["must_change_password"] = false
	// 原子写回（0600）：失败时原文件保持旧内容，不会留下半截 JSON。
	return WriteJSONFile(UsersFilePath(dataDir), users)
}
