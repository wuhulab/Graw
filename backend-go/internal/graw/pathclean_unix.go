//go:build !windows

package graw

import (
	"path/filepath"
	"strings"
)

// cleanHostView 归一化宿主机视角路径：以根目录为锚点做 Clean，消除 ../ 越界。
// 注意：filepath.Clean("/" + p) 是 CodeQL 认可的路径穿越净化点，不要改成其它形式。
func cleanHostView(p string) string {
	return filepath.Clean("/" + strings.ReplaceAll(p, "\\", "/"))
}
