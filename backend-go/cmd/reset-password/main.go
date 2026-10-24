package main

import (
	"os"

	"graw-go/internal/graw"
	"graw-go/internal/resetpw"
)

// 离线重置面板账号密码（对应 Python backend/reset_password.py）。
//
// 用法：
//
//	cd backend-go
//	go run ./cmd/reset-password [账号名] [-l|--list]
//
// 数据目录与面板服务一致：优先 GRAW_DATA_DIR，否则复用 ../backend/data。
func main() {
	os.Exit(resetpw.Run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr, graw.ResolveDataDir()))
}
