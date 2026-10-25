package main

// 本文件是离线重置面板密码的独立命令入口（不启动面板服务）。
//
// 它只是薄壳：参数解析、交互提示、读盘写盘与退出码全部在 internal/resetpw.Run 中，
// 这里只负责把真实 stdin/stdout/stderr 和「与面板一致的数据目录」传进去。
// 退出码约定：0 成功，1 业务失败（账号不存在/密码不合规等），2 参数错误。

import (
	"os"

	"graw-go/internal/graw"
	"graw-go/internal/resetpw"
)

// main 执行离线重置面板账号密码（对应 Python backend/reset_password.py）。
//
// 用法：
//
//	cd backend-go
//	go run ./cmd/reset-password [账号名] [-l|--list]
//
// 数据目录与面板服务一致：优先 GRAW_DATA_DIR，否则复用 ../backend/data
// （由 graw.ResolveDataDir 与 GetConfig 共用同一套推导规则，避免改错文件）。
func main() {
	// 参数解析/交互/写盘都在 resetpw.Run 内，这里只把退出码交给进程。
	os.Exit(resetpw.Run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr, graw.ResolveDataDir()))
}
