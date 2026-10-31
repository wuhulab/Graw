package main

// 本文件是 Release 变更摘要命令的独立入口（graw-relnote），不启动面板服务。
//
// 它只是薄壳：参数解析、git 读取、Markdown 渲染与 GitHub 发布都在 internal/relnote 里，
// 这里只负责把真实 stdout/stderr 和退出码交给进程。退出码：0 成功，1 业务失败，2 参数错误。
//
// 用法：
//
//	cd backend-go
//	go run ./cmd/relnote --tag v1.9.2              # 预览上一个 tag 到 v1.9.2 的变更摘要
//	go run ./cmd/relnote --tag v1.9.2 --create    # 创建 / 更新 GitHub Release
//
// 打 tag 推送后由仓库根目录的 .github/workflows/release.yml 自动执行 --create。

import (
	"os"

	"graw-go/internal/relnote"
)

// main 执行 Release 摘要生成，退出码原样返回给进程。
func main() {
	os.Exit(relnote.Run(os.Args[1:], os.Stdout, os.Stderr))
}
