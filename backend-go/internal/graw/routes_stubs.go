package graw

// 本文件集中注册各业务模块路由（历史文件名保留，所有模块均已有具体实现）。

func registerSecurityRoutes(a *App) {
	registerLoginLogRoutes(a)
	registerFTPUserRoutes(a)
	registerToolboxRoutes(a)
	registerHealthcheckRoutes(a)
	registerImageSafetyRoutes(a)
	registerSlowQueryRoutes(a)
	registerProtectionRoutes(a)
	registerTamperRoutes(a)
	registerUpdateRoutes(a)
}
func registerNotifyRoutes(a *App) { registerNotifyModuleRoutes(a) }
func registerNodeRoutes(a *App) {
	registerAgentRoutes(a)
	registerNodesAndAgentRoutes(a)
	registerSSHKeyRoutes(a)
	registerPortForwardRoutes(a)
	registerBatchRoutes(a)
}
func registerNetworkRoutes(a *App) {
	registerNetmapRoutes(a)
	registerFRPRoutes(a)
	registerVPNRoutes(a)
	registerGitDeployRoutes(a)
	registerNetStorageRoutes(a)
}
func registerMiscRoutes(a *App) { registerPluginRoutes(a) }

// LocalOnlyRejectReason 返回 local 类接口在远端节点被拒时的提示文案。
func LocalOnlyRejectReason() string { return "该功能仅本机节点可用" }
