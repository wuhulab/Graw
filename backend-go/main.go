package main

// 本文件是 graw-go 进程入口。
//
// 启动顺序（每一步都依赖前一步，勿随意调整）：
//  1. 读取配置（graw.GetConfig）并打印版本与 data 目录；
//  2. SeedDefaultUsers 播种/修复 admin 账号——必须在对外监听前完成，
//     否则首个请求可能读到空用户表而被判未授权；
//  3. SecureDataDir 收紧 data 目录权限；
//  4. 启动各后台协程（指标采集、回收站清理、每日巡检、可用性/证书监控、
//     防篡改、SSH 端口转发恢复、VPN 恢复）——这些协程内部自带 recover，
//     随进程退出统一回收，不单独做 Stop；
//  5. 启动 HTTP 服务（默认 :8000，GRAW_ADDR 覆盖；默认无 TLS，生产应置于反代之后）；
//  6. 收到 SIGINT/SIGTERM 后 10 秒优雅关闭，最后 FlushMetrics 落盘剩余采样。

import (
	"context"
	"errors"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"graw-go/internal/graw"
)

// main 完成配置初始化、后台协程启动与 HTTP 服务托管，并阻塞等待退出信号。
func main() {
	log.SetFlags(log.LstdFlags) // 日志带时间戳，便于容器/系统日志排查
	cfg := graw.GetConfig()
	log.Printf("graw-go %s 启动，data=%s", graw.AppVersion, cfg.DataDir)

	// 账号初始化失败属于致命错误：直接退出，避免无账号的进程对外提供服务。
	if err := graw.SeedDefaultUsers(); err != nil {
		log.Fatalf("初始化账号失败: %v", err)
	}
	// 收紧 data 目录（0700/0600）；Windows 上为空操作。必须在后台协程写文件前执行。
	graw.SecureDataDir()
	// 后台协程按职责分组拉起：采集/清理/巡检监控为常驻循环，恢复类只执行一次；
	// 它们之间无启动依赖，均随进程退出回收。
	graw.StartMetricsBackground()
	graw.StartTrashAutoPurge()
	graw.StartDailyReport()
	graw.StartMonitors()
	graw.StartTamperMonitor()
	graw.RestorePortForwards()
	graw.RestoreVPN()

	// 默认监听所有网卡的 8000；容器/反代部署时用 GRAW_ADDR 改写。
	addr := strings.TrimSpace(os.Getenv("GRAW_ADDR"))
	if addr == "" {
		addr = ":8000"
	}
	srv := &http.Server{
		Addr:              addr,
		Handler:           graw.NewApp(),
		ReadHeaderTimeout: 15 * time.Second, // 防慢速 header 攻击
		// 不设全局 ReadTimeout/WriteTimeout：文件上传下载与 WebSocket 都是长连接，
		// 设了会误杀正常请求。请求体读取由 httpx.bodyReadTimeout 按大小单独限定，
		// IdleTimeout 负责回收空闲 keep-alive。
		IdleTimeout: 120 * time.Second,
	}

	// 单独协程监听，主协程腾出来等退出信号。
	go func() {
		log.Printf("监听 %s", addr)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatalf("HTTP 服务启动失败: %v", err)
		}
	}()

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit
	log.Printf("正在关闭…")
	// 10 秒内等待在途请求结束；WebSocket 长连接会被直接断开。
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = srv.Shutdown(ctx)
	// Shutdown 之后不会再产生新采样，此时把缓冲区里的指标落盘。
	graw.FlushMetrics()
}
