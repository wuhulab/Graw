//go:build !windows

package graw

import (
	"os"
	"os/exec"

	"github.com/creack/pty"
)

// unixTTY 把 Unix 伪终端包装成 termTTY：同一个 *os.File 既读又写。
type unixTTY struct {
	f   *os.File
	cmd *exec.Cmd
}

func (t *unixTTY) Read(p []byte) (int, error)  { return t.f.Read(p) }
func (t *unixTTY) Write(p []byte) (int, error) { return t.f.Write(p) }
func (t *unixTTY) Close() error                { return t.f.Close() }

func (t *unixTTY) Resize(rows, cols int) error {
	return pty.Setsize(t.f, &pty.Winsize{Rows: uint16(rows), Cols: uint16(cols)})
}

// Wait 回收 shell 子进程（Unix 必须 Wait，否则会留下僵尸进程）。
func (t *unixTTY) Wait() { _ = t.cmd.Wait() }

// startTermProcess 在 Unix 上用伪终端启动 shell（真正的交互式 TTY）。
func startTermProcess(cmd *exec.Cmd) (termTTY, error) {
	f, err := pty.Start(cmd)
	if err != nil {
		return nil, err
	}
	return &unixTTY{f: f, cmd: cmd}, nil
}

// conptyAvailable 非 Windows 平台没有 ConPTY，由 Unix PTY 承担同样的职责。
func conptyAvailable() bool { return false }
