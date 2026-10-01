# Graw 应用接口开放协议（Graw Plugin Open Protocol, GPOP）

> 面向第三方开发者的插件化应用接入标准。本文档定义如何为 Graw 开发「以插件形式
> 运行的应用」：插件自身是一个可独立部署的 Docker Compose 应用，同时通过一套
> 标准化的清单（`plugin.yml`）与开放接口（`/api/op/*`）与 Graw 面板深度集成。

- 协议版本：**v1**
- 后端模块：`backend/app/plugin_protocol.py`、`backend/app/routers/plugins.py`
- 示例插件：[`plugin-examples/hello-graw/`](../plugin-examples/hello-graw/)
- 系列索引：
  - [社区应用商店（纯 Docker 分发，无协议）](../app-store/README.md)

---

## 1. 核心概念

| 概念 | 说明 |
| --- | --- |
| 插件（Plugin） | 一个包含 `plugin.yml` 清单 + `docker-compose.yml` 的应用包 |
| 清单（Manifest） | `plugin.yml`，声明插件元数据、能力与入口 |
| 开放接口（Open API） | `GET/POST /api/op/*`，插件凭令牌调用的面板能力代理 |
| 令牌（Token） | 安装插件时面板生成的访问凭证，注入插件容器环境变量 `GRAW_PLUGIN_TOKEN` |
| 能力（Capability） | 插件在清单中声明、允许使用的开放接口集合，声明的才放行 |

### 1.1 运行形态

插件以 **Docker Compose 应用**形式运行（底层复用应用商店的 `docker compose` 执行链路，
兼容 Docker / Podman）。面板负责完整生命周期：

```
安装 → 注入令牌与面板地址 → docker compose up -d → 注册
       └─ 启停 / 重启 / 卸载（compose down）/ 轮换令牌
```

### 1.2 协议注入的环境变量

安装成功后，面板自动向入口服务（清单 `entry.service`）注入四个环境变量，
插件进程从环境读取即可与面板通信：

| 环境变量 | 说明 |
| --- | --- |
| `GRAW_PLUGIN_ID` | 插件唯一 ID |
| `GRAW_PLUGIN_TOKEN` | 访问令牌（调用 `/api/op/*` 的凭证，请勿打印/入库） |
| `GRAW_PANEL_URL` | 面板对外地址（插件据此拼出完整的开放接口 URL） |
| `GRAW_PLUGIN_API_VERSION` | 面板侧协议的兼容版本号 |

> 面板注入优先级高于 docker-compose 模板内的同名变量，模板无需预填。

---

## 2. 插件包结构与清单协议

### 2.1 目录结构

```
<plugin-id>/
├── plugin.yml             # 必填：清单
├── docker-compose.yml     # 必填：编排（或 compose.yaml / docker-compose.yaml）
├── server.py / ...        # 插件自身代码（随镜像或挂载）
└── icon.png|svg           # 可选：图标
```

### 2.2 plugin.yml 字段定义（v1）

```yaml
api_version: 1            # 必填，协议版本。> 面板支持的版本会被拒绝安装
id: hello-graw            # 必填，唯一 ID（仅字母/数字/_-/.，字母数字开头，≤64）
name: "Hello Graw"        # 必填，显示名称
version: "1.0.0"          # 必填，插件版本（字母/数字/. _ + -）
description: "..."        # 必填，简介（多行用 > 或 |）
author: "Graw Team"       # 可选，作者
category: "面板/工具"      # 可选，分类（自由文本）
homepage: "https://..."   # 可选，项目主页
icon: icon.png            # 可选，图标文件名（面板预留）

capabilities:             # 可选，能力白名单（未声明则对应开放接口被拒）
  - panel_info            # 允许 GET  /api/op/me
  - notify                # 允许 POST /api/op/notify
  - audit                 # 允许 POST /api/op/audit
  - config                # 允许 读写 /api/op/config

entry:                    # 可选，入口声明（决定端口映射与前端入口展示）
  service: hello          # 主服务名（必须存在于 docker-compose services）
  port: 8080              # 对外暴露端口（1-65535，安装时可被面板覆盖）
  path: /                 # 以 / 开头的相对路径（Web 入口，可选）

env:                      # 可选，安装时的环境变量说明（预填友好项）
  - { name: "HELLO_WORLD_COUNT", default: "1", desc: "问候语重复次数" }

compose_url: "https://..."  # 可选，远程安装时显式指定 docker-compose.yml 的 URL
                            #（缺省则取 plugin.yml 同目录下的 docker-compose.yml）

tags:                     # 可选，标签
  - "示例"
```

> 未列出的顶层字段会被忽略（不透传未知字段）；字符串均有长度上限，
> `capabilities` 只保留白名单内的能力。校验失败安装中止。

---

## 3. 面板管理接口（管理员）

`/api/plugins/*`（需管理员 Bearer 令牌，SSH 远端节点下整体禁用）：

| 方法与路径 | 说明 |
| --- | --- |
| `GET  /api/plugins/settings` | 读取插件功能总开关（始终可用） |
| `PUT  /api/plugins/settings` | 写入插件功能总开关（`{enabled}`，需重启面板完全生效） |
| `GET  /api/plugins/protocol` | 协议信息（版本/能力清单/本地示例插件） |
| `GET  /api/plugins` | 已安装插件列表（脱敏，不含令牌） |
| `GET  /api/plugins/{id}` | 插件详情 |
| `POST /api/plugins/install` | 安装插件（`{id, source?, panel_url?, port?}`） |
| `POST /api/plugins/{id}/start` | 启动（compose start） |
| `POST /api/plugins/{id}/stop` | 停止（compose stop） |
| `POST /api/plugins/{id}/restart` | 重启（compose restart） |
| `POST /api/plugins/{id}/uninstall` | 卸载（compose down + 移除注册） |
| `POST /api/plugins/{id}/rotate-token` | 轮换令牌（旧令牌立即失效，返回新令牌） |
| `GET  /api/plugins/{id}/config` | 读取插件持久化配置（管理视角） |

> **总开关说明**：设置界面「插件」区块可关闭插件功能。关闭后面板重启时
> **不再注册插件相关路由**（`/api/plugins` 业务接口与 `/api/op` 全部 404，
> 即「不加载插件相关代码」）；仅 `settings` 开关路由保留，便于管理员重新打开。

### 3.1 安装请求体

```json
{
  "id": "hello-graw",
  "source": "local",
  "panel_url": "http://127.0.0.1:8000",
  "port": 8080,
  "pull": true,
  "restart": "always"
}
```

| 字段 | 说明 |
| --- | --- |
| `id` | 必填，插件 ID（同时用于本地示例目录定位） |
| `source` | 清单来源：`local`（`app-store/plugins/<id>/`）、`dir:<路径>`（调试）、或 `http(s)://...`（远程 `plugin.yml` URL，带 SSRF 防护） |
| `panel_url` | 面板对外地址，注入 `GRAW_PANEL_URL`。插件容器需据此访问面板 |
| `port` | 外部映射端口；缺省取清单 `entry.port` |
| `pull` | 安装前是否 `docker compose pull` |
| `restart` | 重启策略（`always`/`unless-stopped` 等） |

安装成功响应包含 **`token`（明文，仅返回本次）**，请提示管理员妥善保存；
遗失可调用「轮换令牌」重新获取。

---

## 4. 插件开放接口（供插件调用）

所有端点前缀 `/api/op`，**必须携带两个请求头**：

```
X-Graw-Plugin-Id: <plugin_id>
Authorization: Bearer <token>
```

`token` 取自注入的环境变量 `GRAW_PLUGIN_TOKEN`。面板只存令牌哈希，比对用常量时间；
未启用的插件一律 403。清单未声明对应能力时，该能力端点返回 403。

### 4.1 `GET /api/op/protocol`（无需鉴权）

协议握手信息，供插件端确认兼容性：

```json
{ "api_version": 1, "capabilities": ["panel_info","notify","audit","config"] }
```

### 4.2 `GET /api/op/me`（需能力 `panel_info`）

查询插件自身信息 + 面板基本信息（含面板时间/时区偏移）。

### 4.3 `POST /api/op/notify`（需能力 `notify`）

向面板通知中心推送一条消息（走面板已配置的通知渠道）。

```json
{ "title": "标题（必填）", "message": "详情", "level": "info" }
```

`level` 取值 `info|warn|error`。响应含 `channels_sent`（成功推送的渠道数）。

### 4.4 `POST /api/op/audit`（需能力 `audit`）

写入面板操作审计日志（`action` 必填，`detail` 可空）。

### 4.5 `GET/PUT /api/op/config`（需能力 `config`）

读写插件自有持久化配置（JSON 对象，上限 64KB），落盘位置
`backend/data/plugins/<id>/config.json`。适合存插件自己的运行参数，
把配置与容器解耦（容器可随时重建而不丢配置）。

---

## 5. 令牌与安全模型

1. **随机生成**：令牌 = 32 字节 `secrets.token_urlsafe`，每次安装/轮换均不同。
2. **只存哈希**：面板注册表只保存 SHA-256 哈希；`data/plugins.json` 泄露也不能回放令牌。
3. **能力最小化**：插件只能在 `capabilities` 范围内调用开放接口，越权即 403。
4. **输入收口**：清单未知字段丢弃、字符串限长、ID 白名单正则、URL 走 SSRF 防护。
5. **本地性**：`/api/plugins` 与 `/api/op` 属面板本地管理项，SSH 远端节点下被门控。
6. **审计可追溯**：安装/卸载/通知等操作均写入面板审计日志（用户记作 `plugin:<id>`）。

---

## 6. 插件开发快速开始

### 6.1 从示例起步

直接参考仓库内置示例（`plugin-examples/hello-graw/`），它演示了：
- 标准清单写法与能力声明；
- 读取注入的环境变量完成握手（`/api/op/me`）；
- 业务端点 + 触发面板通知（`/api/op/notify`）。

### 6.2 最小实现清单

1. 编写 `plugin.yml`（必填字段齐全，声明需要的 `capabilities`）；
2. 编写 `docker-compose.yml`（至少一个服务；入口服务名与 `entry.service` 一致）；
3. 应用代码读取 `GRAW_PLUGIN_TOKEN` / `GRAW_PANEL_URL` / `GRAW_PLUGIN_ID`
   调用所需开放接口；
4. 请求体统一 `Content-Type: application/json`。

### 6.3 在面板中测试

```bash
# 开发模式：插件位于仓库 app-store/plugins/<id>/
curl -X POST http://<panel>/api/plugins/install \
  -H "Authorization: Bearer <admin token>" \
  -H "Content-Type: application/json" \
  -d '{"id":"hello-graw","source":"local","panel_url":"http://127.0.0.1:8000"}'
```

安装成功后插件容器内即可调用：

```bash
curl -H "X-Graw-Plugin-Id: hello-graw" \
     -H "Authorization: Bearer $GRAW_PLUGIN_TOKEN" \
     http://<panel>/api/op/me
```

---

## 7. 兼容性

- `api_version` 为兼容性主版本；面板只接受 `<= 面板支持版本` 的清单，
  未来升级破坏性变更会提升 `api_version`，低版本插件可继续运行。
- 开放接口响应始终为 JSON；失败返回非 2xx 状态码 + `detail` 字段说明原因。