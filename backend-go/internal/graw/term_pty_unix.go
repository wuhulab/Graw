//go:build !windows

package graw

import (
	"os"
	"os/exec"

	"github.com/creack/pty"
)

// startTermProcess 在 Unix 上用伪终端启动 shell（真正的交互式 TTY）。
func startTermProcess(cmd *exec.Cmd) (*os.File, error) {
	return pty.Start(cmd)
}

func termResize(f *os.File, rows, cols int) {
	_ = pty.Setsize(f, &pty.Winsize{Rows: uint16(rows), Cols: uint16(cols)})
}
