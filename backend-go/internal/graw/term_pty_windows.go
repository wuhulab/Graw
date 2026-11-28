//go:build windows

package graw

import (
	"context"
	"fmt"
	"os/exec"

	"github.com/UserExistsError/conpty"
	"golang.org/x/sys/windows"
)

// conptyTTY 把 Windows ConPTY 包装成 termTTY。
// 与 Unix 不同，ConPTY 的读写是两根独立管道，由 conpty 库内部管理。
type conptyTTY struct {
	c *conpty.ConPty
}

func (t *conptyTTY) Read(p []byte) (int, error)  { return t.c.Read(p) }
func (t *conptyTTY) Write(p []byte) (int, error) { return t.c.Write(p) }
func (t *conptyTTY) Close() error                { return t.c.Close() }

// Resize 注意参数顺序：ConPTY 要的是「宽 x 高」，界面传进来的是「行 x 列」。
func (t *conptyTTY) Resize(rows, cols int) error { return t.c.Resize(cols, rows) }

// Wait 等待 ConPTY 里的进程退出；进程句柄由 Close 释放。
func (t *conptyTTY) Wait() { _, _ = t.c.Wait(context.Background()) }

// conptyAvailable 判断当前 Windows 是否支持 ConPTY（Win10 1809 及以上）。
func conptyAvailable() bool { return conpty.IsConPtyAvailable() }

// startTermProcess 用 ConPTY 启动本地 shell：真控制台才有提示符、逐键回显、
// 方向键 / Ctrl+C / TUI。ConPTY 不可用（过旧的 Windows）时返回错误，
// 由调用方回退到管道模式。
func startTermProcess(cmd *exec.Cmd) (termTTY, error) {
	if !conpty.IsConPtyAvailable() {
		return nil, fmt.Errorf("conpty unavailable")
	}
	// 用 LookPath 解析后的 cmd.Path 作为可执行文件（cmd.Args[0] 可能只是命令名）；
	// ComposeCommandLine 负责给带空格的路径与参数加引号。
	args := append([]string{cmd.Path}, cmd.Args[1:]...)
	opts := []conpty.ConPtyOption{conpty.ConPtyDimensions(80, 24)}
	if cmd.Dir != "" {
		opts = append(opts, conpty.ConPtyWorkDir(cmd.Dir))
	}
	c, err := conpty.Start(windows.ComposeCommandLine(args), opts...)
	if err != nil {
		return nil, err
	}
	return &conptyTTY{c: c}, nil
}
