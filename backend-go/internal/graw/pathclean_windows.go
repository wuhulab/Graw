//go:build windows

package graw

import (
	"path/filepath"
	"strings"
)

// cleanHostView 归一化宿主机视角路径（Windows 保留盘符，避免 C:\ 被当成根相对路径）。
func cleanHostView(p string) string {
	p = strings.ReplaceAll(p, "\\", "/")
	vol := filepath.VolumeName(p)
	if vol == "" {
		return filepath.Clean("/" + p)
	}
	return vol + filepath.Clean("/"+strings.TrimPrefix(p, vol))
}
