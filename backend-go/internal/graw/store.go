package graw

import (
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
)

// ErrNotExist 表示配置文件不存在（与 os.ErrNotExist 等价，便于上层判定）。
var ErrNotExist = os.ErrNotExist

// ReadJSON 读取 JSON 文件；文件不存在返回 ErrNotExist。
func ReadJSON(path string, v any) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	dec := json.NewDecoder(f)
	dec.UseNumber()
	return dec.Decode(v)
}

// ReadJSONOr 读取 JSON 文件；不存在或损坏时返回 fallback 指向的值。
func ReadJSONOr(path string, v any, fallback any) any {
	if err := ReadJSON(path, v); err != nil {
		return fallback
	}
	return v
}

// WriteJSONFile 原子写入 JSON 文件（同目录临时文件 + rename，权限 0600）。
func WriteJSONFile(path string, v any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	data, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	return atomicWrite(path, data, 0o600)
}

// WriteJSONCompactFile 原子写入紧凑 JSON（用于体积敏感的缓存/日志类文件）。
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

func atomicWrite(path string, data []byte, perm os.FileMode) error {
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, "."+filepath.Base(path)+".tmp-*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer func() {
		_ = os.Remove(tmpName)
	}()
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	_ = os.Chmod(tmpName, perm)
	return os.Rename(tmpName, path)
}

// SecureDataDir 收紧 data 目录权限（Linux 生效，Windows 忽略）。
func SecureDataDir() {
	root := GetConfig().DataDir
	_ = os.Chmod(root, 0o700)
	_ = filepath.Walk(root, func(p string, info os.FileInfo, err error) error {
		if err != nil {
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
		return cleanHostView(p)
	}
	return p
}

// CopyFile 复制文件（自动创建目标目录，权限 0600）。
func CopyFile(src, dst string) error {
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
	_ = os.Chmod(dst, st.Mode().Perm())
	return nil
}

// Exists 判断路径是否存在。
func Exists(path string) bool {
	path = sanitizeFsPath(path)
	_, err := os.Stat(path)
	return err == nil
}

// IsDir 判断路径是否为目录。
func IsDir(path string) bool {
	path = sanitizeFsPath(path)
	st, err := os.Stat(path)
	return err == nil && st.IsDir()
}

// EnsureDir 创建目录（权限 0700）并返回错误。
func EnsureDir(path string) error {
	path = sanitizeFsPath(path)
	return os.MkdirAll(path, 0o700)
}

// IsNotExist 判断错误是否为「文件不存在」。
func IsNotExist(err error) bool {
	return errors.Is(err, os.ErrNotExist)
}
