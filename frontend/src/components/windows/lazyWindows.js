/**
 * lazyWindows.js — 功能窗口组件的「按需加载」注册表
 * ==========================================================
 * 业务背景（本轮加载速度优化）：
 *   此前 App.vue 顶部把 70+ 个窗口组件全部静态 import，Vite 只能把它们连同
 *   ECharts、xterm、markdown-it 等重量级依赖打成一个巨大的首屏 chunk——用户
 *   一进面板就要下载/解析全部功能代码，首屏明显变慢（构建产物单文件曾达 5MB+）。
 *   而实际上：用户一次只会打开少数几个窗口，「面板模式」更是只挂载当前激活窗口。
 *
 * 方案：
 *   这里统一用 defineAsyncComponent + 动态 import() 注册全部窗口组件，
 *   Vite 会为每个 .vue 自动切分出独立 chunk（首次打开该窗口时才下载）。
 *   App.vue 只做一次命名导入，使用方式（component: markRaw(XxxWindow)）完全不变，
 *   因此窗口系统、面板模式、KeepAlive 缓存、CommandPalette 入口都无需改动。
 *
 * 约定：
 *   - 所有「功能窗口」都在此注册；直接写在 App.vue 模板里的基础组件
 *     （Login / PanelLayout / PanelHome / WindowContent / 各类全局告警层）仍保持静态导入，
 *     它们属于首屏必需，不能延迟。
 *   - 新增窗口组件时，在下方对应分组追加一行导出即可。
 *   - 约定导出名 = 组件名 = 文件名（去掉 .vue），便于检索与静态校验。
 */

import { defineAsyncComponent } from 'vue'   // Vue 异步组件工厂：把动态 import 包成组件

/**
 * 包装动态 import：懒加载组件对象，可安全传给 markRaw 存入响应式窗口描述。
 *
 * @param {Function} loader 返回 Promise 的加载函数（`() => import('./Xxx.vue')`）
 * @returns {Object} 可渲染的异步组件定义
 */
const lazy = (loader) => defineAsyncComponent({
  loader,
  delay: 0,             // 不设延迟：chunk 命中缓存时立刻渲染，避免出现无谓的空白帧
  suspensible: false,   // 显式关闭 Suspense 接管：本应用无 Suspense，走内部加载态更可预期
})

// ---------------- 容器 / Docker 相关 ----------------
export const DockerWindow = lazy(() => import('./DockerWindow.vue'))
export const ContainerLogsWindow = lazy(() => import('./ContainerLogsWindow.vue'))
export const ContainerDetailWindow = lazy(() => import('./ContainerDetailWindow.vue'))
export const ContainerStatsWindow = lazy(() => import('./ContainerStatsWindow.vue'))
export const ContainerEditWindow = lazy(() => import('./ContainerEditWindow.vue'))
export const DockerConfigEditorWindow = lazy(() => import('./DockerConfigEditorWindow.vue'))

// ---------------- 系统 / 主机管理 ----------------
export const ProcessWindow = lazy(() => import('./ProcessWindow.vue'))
export const DisksWindow = lazy(() => import('./DisksWindow.vue'))
export const FilesWindow = lazy(() => import('./FilesWindow.vue'))
export const RecycleBinWindow = lazy(() => import('./RecycleBinWindow.vue'))
export const TerminalWindow = lazy(() => import('./TerminalWindow.vue'))
export const EditorWindow = lazy(() => import('./EditorWindow.vue'))
export const MediaWindow = lazy(() => import('./MediaWindow.vue'))
export const RuntimeWindow = lazy(() => import('./RuntimeWindow.vue'))
export const RuntimeCreateWindow = lazy(() => import('./RuntimeCreateWindow.vue'))
export const MonitoringWindow = lazy(() => import('./MonitoringWindow.vue'))
export const MetricsHistoryWindow = lazy(() => import('./MetricsHistoryWindow.vue'))
export const RollbackWindow = lazy(() => import('./RollbackWindow.vue'))
export const BatchWindow = lazy(() => import('./BatchWindow.vue'))
export const ReportWindow = lazy(() => import('./ReportWindow.vue'))

// ---------------- 网站 / 数据库 / SSL ----------------
export const SitesWindow = lazy(() => import('./SitesWindow.vue'))
export const SiteEditWindow = lazy(() => import('./SiteEditWindow.vue'))
export const SiteMaintenanceWindow = lazy(() => import('./SiteMaintenanceWindow.vue'))
export const SiteOptsWindow = lazy(() => import('./SiteOptsWindow.vue'))
export const RewriteWindow = lazy(() => import('./RewriteWindow.vue'))
export const WebStatsWindow = lazy(() => import('./WebStatsWindow.vue'))
export const CertWindow = lazy(() => import('./CertWindow.vue'))
export const SslUploadWindow = lazy(() => import('./SslUploadWindow.vue'))
export const SslLeFormWindow = lazy(() => import('./SslLeFormWindow.vue'))
export const GitDeployWindow = lazy(() => import('./GitDeployWindow.vue'))
export const GitDeployFormWindow = lazy(() => import('./GitDeployFormWindow.vue'))
export const DatabaseWindow = lazy(() => import('./DatabaseWindow.vue'))
export const DatabaseManageWindow = lazy(() => import('./DatabaseManageWindow.vue'))
export const DatabaseCreateWindow = lazy(() => import('./DatabaseCreateWindow.vue'))
export const SlowQueryWindow = lazy(() => import('./SlowQueryWindow.vue'))

// ---------------- 安全 / ShunX 保护机制 ----------------
export const ShunxSecurityWindow = lazy(() => import('./ShunxSecurityWindow.vue'))
export const FirewallRuleFormWindow = lazy(() => import('./FirewallRuleFormWindow.vue'))
export const WafAclFormWindow = lazy(() => import('./WafAclFormWindow.vue'))
export const TamperFormWindow = lazy(() => import('./TamperFormWindow.vue'))
export const ImageScanWindow = lazy(() => import('./ImageScanWindow.vue'))
export const SshKeyGenWindow = lazy(() => import('./SshKeyGenWindow.vue'))
export const SshKeyImportWindow = lazy(() => import('./SshKeyImportWindow.vue'))
export const SshKeyDeployWindow = lazy(() => import('./SshKeyDeployWindow.vue'))
export const SessionsWindow = lazy(() => import('./SessionsWindow.vue'))

// ---------------- 任务 / 备份 / 通知 ----------------
export const TasksWindow = lazy(() => import('./TasksWindow.vue'))
export const CronTaskFormWindow = lazy(() => import('./CronTaskFormWindow.vue'))
export const BackupTaskFormWindow = lazy(() => import('./BackupTaskFormWindow.vue'))
export const BackupRemoteFormWindow = lazy(() => import('./BackupRemoteFormWindow.vue'))
export const NotifyChannelFormWindow = lazy(() => import('./NotifyChannelFormWindow.vue'))
export const NotifyRuleFormWindow = lazy(() => import('./NotifyRuleFormWindow.vue'))
export const LogCollectFormWindow = lazy(() => import('./LogCollectFormWindow.vue'))
export const ServiceMonitorFormWindow = lazy(() => import('./ServiceMonitorFormWindow.vue'))
export const UptimeFormWindow = lazy(() => import('./UptimeFormWindow.vue'))

// ---------------- 网络 / 存储 / 端口转发 ----------------
export const FrpWindow = lazy(() => import('./FrpWindow.vue'))
export const FrpProxyFormWindow = lazy(() => import('./FrpProxyFormWindow.vue'))
export const PortForwardWindow = lazy(() => import('./PortForwardWindow.vue'))
export const PortForwardFormWindow = lazy(() => import('./PortForwardFormWindow.vue'))
export const NetStorageWindow = lazy(() => import('./NetStorageWindow.vue'))
export const NetStorageBrowseWindow = lazy(() => import('./NetStorageBrowseWindow.vue'))
export const NetStorageFormWindow = lazy(() => import('./NetStorageFormWindow.vue'))
export const ConnectionFormWindow = lazy(() => import('./ConnectionFormWindow.vue'))
export const FtpUsersWindow = lazy(() => import('./FtpUsersWindow.vue'))
export const FtpUserFormWindow = lazy(() => import('./FtpUserFormWindow.vue'))

// ---------------- 应用商店 ----------------
export const AppStoreWindow = lazy(() => import('./AppStoreWindow.vue'))
export const AppStoreInstallWindow = lazy(() => import('./AppStoreInstallWindow.vue'))
export const AppStoreInstallLogWindow = lazy(() => import('./AppStoreInstallLogWindow.vue'))
export const AppStoreComposeEditorWindow = lazy(() => import('./AppStoreComposeEditorWindow.vue'))
export const AppStoreReadmeWindow = lazy(() => import('./AppStoreReadmeWindow.vue'))
export const AppStoreConfigWindow = lazy(() => import('./AppStoreConfigWindow.vue'))

// ---------------- 账号 / 界面设置 / 日志 / 版本 ----------------
export const UserWindow = lazy(() => import('./UserWindow.vue'))
export const ChangePasswordWindow = lazy(() => import('./ChangePasswordWindow.vue'))
export const UISettingsWindow = lazy(() => import('./UISettingsWindow.vue'))
export const SettingsWindow = lazy(() => import('./SettingsWindow.vue'))
export const LogsWindow = lazy(() => import('./LogsWindow.vue'))
export const PhpVersionsWindow = lazy(() => import('./PhpVersionsWindow.vue'))
