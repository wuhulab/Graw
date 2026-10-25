package graw

// 本文件（store.go）是面板的 JSON 持久化与文件系统工具层。
//
// 面板所有配置/凭据都以 JSON 文件形式落在 data 目录，本文件统一提供：
//   - ReadJSON / ReadJSONOr：读取（数字解码为 json.Number，保留整数精度）；
//   - WriteJSONFile / WriteJSONCompactFile：原子写（同目录临时文件 + rename），
//     文件权限固定 0600、目录 0700；
//   - SecureDataDir：启动时递归收紧 data 目录权限；
//   - sanitizeFsPath 及 CopyFile / Exists / IsDir / EnsureDir 等文件辅助函数。
//
// 安全约束 / 易踩坑：
//   - data 目录含 SSH/数据库凭据与 JWT 密钥，新增写入必须走本文件的原子写，
//     不要用 os.WriteFile 直写（可能留下半截文件，且默认权限过宽）；
//   - 临时文件必须与目标同目录：跨文件系统的 rename 不是原子操作；
//   - ReadJSON 用 UseNumber，调用方拿到的是 json.Number 而非 float64，
//     需要与 Python 版数字行为对齐时要显式转换。

import (
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
)

// ErrNotExist 表示配置文件不存在（与 os.ErrNotExist 等价，便于上层判定）。
//
// 保留别名是为了让业务代码只依赖 graw 包，同时 errors.Is 仍能匹配 os.ErrNotExist。
var ErrNotExist = os.ErrNotExist

// ReadJSON 读取 JSON 文件；文件不存在返回 ErrNotExist。
//
// 数字统一解码为 json.Number，避免大整数（时间戳/ID）被转成 float64 丢精度；
// 解析失败原样返回 encoding/json 的错误，调用方可据此区分「文件缺失」与「内容损坏」。
func ReadJSON(path string, v any) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	dec := json.NewDecoder(f)
	dec.UseNumber() // 保留数字原始文本，交给调用方按需转换
	return dec.Decode(v)
}

// ReadJSONOr 读取 JSON 文件；不存在或损坏时返回 fallback 指向的值。
//
// 返回值类型为 any：成功时就是 v，失败时原样返回 fallback（调用方自行断言类型）。
// 该函数会吞掉错误，只适合「缺文件也能继续」的非关键配置。
func ReadJSONOr(path string, v any, fallback any) any {
	if err := ReadJSON(path, v); err != nil {
		return fallback
	}
	return v
}

// WriteJSONFile 原子写入 JSON 文件（同目录临时文件 + rename，权限 0600）。
//
// 输出为 2 空格缩进 + 末尾换行：便于人工 diff、运维直接查看，也与 Python 版一致。
func WriteJSONFile(path string, v any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	data, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n') // 补末尾换行，避免 diff 显示 "\ No newline at end of file"
	return atomicWrite(path, data, 0o600)
}

// WriteJSONCompactFile 原子写入紧凑 JSON（用于体积敏感的缓存/日志类文件）。
//
// 无缩进、无末尾换行；配置类文件请改用 WriteJSONFile，便于人工排查。
func WriteJSONCompactFile(path string, v any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	data, err := json.Marshal(v)
	if err != nil {
		return err
	}
	return atomicWrite(path, data, 0o600)
}

// atomicWrite 原子地把 data 写入 path：同目录建临时文件 → 写入 → fsync → chmod → rename。
//
// 为什么必须同目录：rename 只在同一文件系统内保证原子；跨设备会退化为复制甚至失败。
// 写入顺序也有讲究：先落盘再 rename，保证读到的永远是完整内容；先收紧权限再改名，
// 避免目标路径短暂出现宽权限文件。
// 注意：本函数不 fsync 父目录，极端掉电场景下仍可能丢失 rename 元数据
// （与 Python 版 os.replace 的行为等价）。
func atomicWrite(path string, data []byte, perm os.FileMode) error {
	dir := filepath.Dir(path)
	// 前缀带 "."、后缀随机：既不易被误认为正式配置文件，也便于识别残留临时文件。
	tmp, err := os.CreateTemp(dir, "."+filepath.Base(path)+".tmp-*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer func() {
		// 成功 rename 后临时路径已不存在，这里的清理是失败路径的兜底。
		_ = os.Remove(tmpName)
	}()
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		// 先落盘：否则 rename 后遇到掉电可能读到空文件/半截文件。
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	_ = os.Chmod(tmpName, perm) // chmod 失败被忽略：至少保留创建时的权限，不影响原子性
	return os.Rename(tmpName, path)
}

// SecureDataDir 收紧 data 目录权限（Linux 生效，Windows 忽略）。
//
// 启动时调用一次；单个文件 chmod 失败被忽略（例如 Windows 不支持 POSIX 权限），
// 不因个别文件失败而中断整个启动流程。
func SecureDataDir() {
	root := GetConfig().DataDir
	_ = os.Chmod(root, 0o700)
	_ = filepath.Walk(root, func(p string, info os.FileInfo, err error) error {
		if err != nil {
			return nil // 单个条目不可读时跳过，避免整个遍历失败
		}
		// Walk 用 Lstat 报告符号链接，但 os.Chmod 会跟随链接改到目标文件；
		// data 目录里不应有链接，直接跳过，避免误改目录之外的权限。
		if info.Mode()&os.ModeSymlink != 0 {
			return nil
		}
		if info.IsDir() {
			_ = os.Chmod(p, 0o700)
		} else {
			_ = os.Chmod(p, 0o600)
		}
		return nil
	})
}

// sanitizeFsPath 保持相对路径语义，同时把绝对路径交给 cleanHostView 归一化。
// filepath.IsLocal 守卫是 CodeQL 认可的路径净化点：相对且不越界的路径原样返回。
func sanitizeFsPath(p string) string {
	if !filepath.IsLocal(p) {
		// 绝对路径/含 .. 的路径统一以根为锚点 Clean，消除越界片段。
		return cleanHostView(p)
	}
	return p
}

// CopyFile 复制文件（自动创建目标目录，权限 0600）。
//
// 失败行为：源不可读、目标目录不可建、写入/关闭失败都会返回错误；
// 成功后把目标权限还原为源文件的普通权限位（不含 setuid 等特殊位）。
func CopyFile(src, dst string) error {
	// 两侧路径都先过净化点，防止 ../ 逃出受限目录。
	src, dst = sanitizeFsPath(src), sanitizeFsPath(dst)
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	st, err := in.Stat()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(dst), 0o700); err != nil {
		return err
	}
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		_ = out.Close()
		return err
	}
	if err := out.Close(); err != nil {
		return err
	}
	// 只用 Perm()（普通权限位），避免把源文件的 setuid/setgid 复制到新位置。
	_ = os.Chmod(dst, st.Mode().Perm())
	return nil
}

// Exists 判断路径是否存在（任意类型，包括目录与符号链接指向的目标）。
func Exists(path string) bool {
	path = sanitizeFsPath(path)
	_, err := os.Stat(path)
	return err == nil
}

// IsDir 判断路径是否为目录；不存在或无权限时返回 false。
func IsDir(path string) bool {
	path = sanitizeFsPath(path)
	st, err := os.Stat(path)
	return err == nil && st.IsDir()
}

// EnsureDir 创建目录（权限 0700，含多级父目录）并返回错误。
//
// 已存在且是目录时返回 nil；已存在但是文件时返回错误。
func EnsureDir(path string) error {
	path = sanitizeFsPath(path)
	return os.MkdirAll(path, 0o700)
}

// IsNotExist 判断错误是否为「文件不存在」。
//
// 用 errors.Is 而非 == 比较，兼容被 fmt.Errorf("...: %w") 包装过的错误。
func IsNotExist(err error) bool {
	return errors.Is(err, os.ErrNotExist)
}
