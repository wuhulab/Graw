/*
  这个文件保管「当前是谁在用这个面板」——整个 Graw 前端的登录身份中心。

  为什么它这么关键：除了登录接口和健康检查，后端 /api/* 的每一个接口都要求
  请求头带 Authorization: Bearer <token>。api.js 的请求拦截器就是从这里取
  token 往请求上贴；WebSocket（实时监控、Web 终端、防篡改告警）没法带请求头，
  也是从这里取 token 拼成 ?token=xxx 查询参数。所以这里一旦为空，
  整个面板就等于「未登录」，App.vue 会把桌面收起来、只显示登录页。

  两个核心状态字段：
    token —— 后端登录成功后签发的 JWT，是访问所有接口的通行证
    user  —— 当前账号信息（用户名、role 等）。role === 'admin' 决定能不能做
             写操作：Docker、网站、防火墙、数据库这些管理类接口后端都要求管理员，
             前端也据此隐藏/禁用按钮，省得让用户点了才吃 403

  为什么要落 localStorage：面板是单页应用，用户按 F5 刷新页面时内存里的
  JS 变量全没了。把身份存进浏览器本地，刷新后能直接续上登录态，
  不用每次刷新都重新输密码。

  用法：登录成功调 setAuth() 存身份；退出登录或 token 过期被后端拒绝时调
  clearAuth() 清干净；组件里用 isLoggedIn()/isAdmin() 做界面权限判断。
*/
import { reactive } from 'vue'                                // 让身份变化能自动驱动界面（登录后桌面出现、退出后回登录页）

// 浏览器本地存放身份的键名。加 graw_ 前缀是为了跟同域下其它应用的存储互不干扰
const STORAGE_KEY = 'graw_auth'

// --- 启动时读回上次的登录身份，实现「刷新页面不掉线」 ---
function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { token: null, user: null }              // 从没登录过 / 已退出登录：给一副空身份，界面进登录页
    return JSON.parse(raw)
  } catch {
    // 存储内容被手工改坏、或浏览器处于禁用存储的隐私模式：宁可当作未登录，
    // 也不能让解析异常把整个应用启动流程炸掉
    return { token: null, user: null }
  }
}

// --- 对外暴露：全站唯一的当前身份对象（api.js / App.vue / 各窗口都读它） ---
export const auth = reactive(load())

// --- 登录成功：把身份写进内存 + 落地浏览器 ---
export function setAuth(token, user) {
  auth.token = token                                          // 写这一行的瞬间，全站请求就开始带上新通行证
  auth.user = user                                            // 角色随之生效，管理员专属入口在界面上放出来
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ token, user }))   // 同步落地，下次刷新还能续上
}

// --- 退出登录 / token 失效：内存与本地一起清空 ---
export function clearAuth() {
  auth.token = null                                           // 通行证一撤，后续请求会被后端 401，界面自动退回登录页
  auth.user = null
  localStorage.removeItem(STORAGE_KEY)                         // 必须一起删，否则下次刷新会把已失效的旧 token 又读回来
}

// --- 判断是否已登录：决定展示桌面还是登录页 ---
export function isLoggedIn() {
  return !!auth.token                                         // 只看有没有通行证；是否过期由后端说了算，前端不自行校验
}

// --- 判断是否管理员：决定管理类功能入口是否可见可用 ---
export function isAdmin() {
  return auth.user?.role === 'admin'                          // 用可选链兜住「未登录时 user 为 null」的情况，避免取属性报错
}
