//go:build windows

package graw

import (
	"fmt"
	"os"
	"os/exec"
)

// startTermProcess Windows 下无标准库 ConPTY 封装，返回错误并由调用方回退到管道模式。
func startTermProcess(cmd *exec.Cmd) (*os.File, error) {
	return nil, fmt.Errorf("conpty unavailable")
}

func termResize(f *os.File, rows, cols int) {}
