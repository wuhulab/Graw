package main

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

func main() {
	log.SetFlags(log.LstdFlags)
	cfg := graw.GetConfig()
	log.Printf("graw-go %s 启动，data=%s", graw.AppVersion, cfg.DataDir)

	if err := graw.SeedDefaultUsers(); err != nil {
		log.Fatalf("初始化账号失败: %v", err)
	}
	graw.SecureDataDir()
	graw.StartMetricsBackground()
	graw.StartTrashAutoPurge()
	graw.StartDailyReport()
	graw.StartMonitors()
	graw.StartTamperMonitor()
	graw.RestorePortForwards()
	graw.RestoreVPN()

	addr := strings.TrimSpace(os.Getenv("GRAW_ADDR"))
	if addr == "" {
		addr = ":8000"
	}
	srv := &http.Server{
		Addr:              addr,
		Handler:           graw.NewApp(),
		ReadHeaderTimeout: 15 * time.Second,
		IdleTimeout:       120 * time.Second,
	}

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
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = srv.Shutdown(ctx)
	graw.FlushMetrics()
}
