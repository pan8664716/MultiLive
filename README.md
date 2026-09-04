# MultiLive — 多平台直播 m3u 聚合器

把多个直播平台（抖音 / 快手 / B站 / 斗鱼 / YY…）的「在播直播间」
聚合到一份 `output/multilive.m3u`，并为**每个平台单独生成一份 m3u**，供 PotPlayer /
VLC / mpv / IINA 直接导入播放。

Go 实现（`go.mod`，纯标准库零第三方依赖）：平台级 goroutine 并行 +
平台内分页并发，编译成单二进制运行。旧 Python 版（`multilive/` 目录）已废弃保留，
仅作接口情报参考，不再参与构建与 CI。

## 订阅方式（直接导入播放器）

仓库每半小时自动刷新一次，m3u 文件直接入库。支持 m3u 订阅的播放器
（PotPlayer / VLC / mpv / IINA / 电视盒子等）填入下面任一地址即可自动更新。
以下为国内可直接访问的代理地址（`gh-proxy.org` + raw 地址），
也可去掉代理前缀使用 GitHub 原始地址。

**全聚合（所有平台一个列表）**

```
全平台: https://gh-proxy.org/https://raw.githubusercontent.com/pan8664716/MultiLive/main/output/multilive.m3u
```

**各平台单独列表**

```
抖音: https://gh-proxy.org/https://raw.githubusercontent.com/pan8664716/MultiLive/main/output/douyin_live.m3u
快手: https://gh-proxy.org/https://raw.githubusercontent.com/pan8664716/MultiLive/main/output/kuaishou_live.m3u
B站: https://gh-proxy.org/https://raw.githubusercontent.com/pan8664716/MultiLive/main/output/bilibili_live.m3u
斗鱼: https://gh-proxy.org/https://raw.githubusercontent.com/pan8664716/MultiLive/main/output/douyu_live.m3u
YY: https://gh-proxy.org/https://raw.githubusercontent.com/pan8664716/MultiLive/main/output/yy_live.m3u
虎牙: https://gh-proxy.org/https://raw.githubusercontent.com/pan8664716/MultiLive/main/output/huya_live.m3u
Twitch: https://gh-proxy.org/https://raw.githubusercontent.com/pan8664716/MultiLive/main/output/twitch_live.m3u
咪咕: https://gh-proxy.org/https://raw.githubusercontent.com/pan8664716/MultiLive/main/output/migu_live.m3u
```

> 提示：`multilive.m3u` 为全平台聚合；单独列表便于只想订阅某平台时使用。
> 若网络可直连 GitHub，把前缀 `https://gh-proxy.org/` 去掉即可用 raw 地址。

## 特性

- **纯 HTTP 优先**：快手 / B站 / 斗鱼 / YY / 虎牙 / Twitch 列表纯 HTTP（Go 标准库
  `net/http` + CookieJar）；douyin 保留「接口 → 浏览器(可选) → 页面」三级降级，
  抖音/快手直接写列表接口自带的
  CDN 直链（抖音 HLS / 快手 FLV），YY/B站/斗鱼/虎牙/Twitch
  播放地址**不逐个取流**，m3u 直接写 `https://astar.cc.cd/<平台>/<房间号>`，
  由 Worker/代理点播时实时解析最高画质流（看哪个解析哪个，零风控风险）。
- **咪咕 IPTV**：官方频道列表接口 `program-sc.miguvideo.com/live/v2/tv-data/`
  批量拉全量频道（央视/卫视/地方/体育等 11 类，约 125 个）；播放地址需逐频道
  签名（MD5 盐值），m3u 统一写 `https://astar.cc.cd/migu/<pID>` 由 Worker 解析。
- **平台插件化 + 并行**：`internal/platforms/<平台>/` 每个平台一个包、
  统一实现 `platform.Platform` 接口、在 `internal/registry` 集中注册；
  **不同平台 goroutine 并行拉取，平台内部分页并发**
  （B站 412/403 退避重试；信息类数据全部走
    批量接口/页面内嵌数据，绝不逐房间调 API 取信息）。
- **增量合并**：先按「平台:房间号」删除重复，再将本轮更新的条目全部新增到最前面
  （douyin 保留历史并用兜底解析地址，其余平台只留此刻在播）。
- **便于排查**：每次运行输出 `output/status.json`（房间数/耗时/合并统计，入库）
  与 `output/run.log`（滚动日志，超 1MB 轮转保留 3 份，不入库），并保留各平台独立 m3u。
- **定时更新**：内置 GitHub Actions，每半小时自动刷新并提交。

## 快速开始

```bash
# 构建一次（也可用 go run ./cmd/multilive 直接跑）
go build -o multilive ./cmd/multilive

# 试跑（只打印统计，不写文件）
./multilive --dry-run

# 只跑单个平台（调试用）
./multilive --platform douyin --pages 2 --dry-run

# 正式更新（写入 output/multilive.m3u 与各平台 m3u）
./multilive
```

把生成的 `output/multilive.m3u`（或多平台各自的文件）拖进 PotPlayer / VLC 即可。
需要 Go 1.23+；`tools/*.mjs` 浏览器兜底另需 Node 24 + `npm ci`（patchright）。

## 配置来源（sources.txt）

每行一个来源，推荐带平台前缀；`#` 开头为注释：

```
douyin:https://live.douyin.com/categorynew/4_105   # 抖音分类页（一整类在播房间）
douyin:https://live.douyin.com/745350622378        # 抖音直播间页
douyin:745350622378                                # 抖音纯房间号
kuaishou:HOT:50                                    # 快手热门页，抓 50 页（约 2000 房间）
bilibili:https://live.bilibili.com/all:10          # B站整站列表（页数可选，100 房间/页）
bilibili:https://live.bilibili.com/6               # B站直播间页
huya:https://www.huya.com/l:10                     # 虎牙全部直播（页数可选，120 房间/页）
douyu:https://www.douyu.com/directory/all:10       # 斗鱼全部频道（页数可选，120 房间/页）
yy:https://www.yy.com/music/                            # YY 频道页（SSR 第一页 + 分页接口补齐）
```

裸地址自动识别（按平台名排序逐个尝试）；有歧义时请用显式前缀。
`:N` 后缀表示抓取 N 页，`--pages` 命令行参数可统一限制上限。

## 命令行

| 参数 | 说明 |
|---|---|
| `--dry-run` | 只打印统计与样例，不写文件 |
| `--platform a,b` | 只跑指定平台（排查用） |
| `--pages N` | 限制单来源翻页数（各平台列表/分类通用） |
| `--sources PATH` | 指定来源配置文件 |
| `--verbose` | 控制台输出 DEBUG 日志 |

## 输出文件

- `output/multilive.m3u` — 聚合列表（本轮置顶 + 增量去重）
- `output/douyin_live.m3u` / `output/kuaishou_live.m3u` / `output/bilibili_live.m3u` / `output/douyu_live.m3u` / `output/yy_live.m3u` / `output/huya_live.m3u` / `output/twitch_live.m3u` / `output/migu_live.m3u` — 每个平台单独一份
- `output/status.json` — 机器可读运行摘要（入库）
- `output/run.log` — 滚动日志（保留 3 份，不入库）
- `output/douyu_warm.json` — 旧的斗鱼取参缓存（已不再生成，可忽略）

## 部署到 GitHub（可选）

1. 新建仓库并推送本目录（本仓库远端：`git@github.com:pan8664716/MultiLive.git`）。
2. 仓库页 → **Actions** → **多平台直播 m3u 更新** → **Run workflow** 手动触发一次。
3. 之后每半小时自动运行；更新后的 m3u 会直接提交回仓库。

> CI 安装 Go（构建）+ Node（抖音/TikTok 浏览器兜底脚本用）+ `npm ci`（patchright）；
> 主链路纯 HTTP 不需要浏览器，douyin 接口被风控时自动落到「页面解析」兜底。

## 架构总览

```
sources.txt ──► config.LoadSources() ──► registry（集中注册 Platform 实现）
                    │
                    ▼
           平台级并行（各平台一个 goroutine）
   ┌──────────┬───────────┬──────────┬──────────┐
   ▼          ▼           ▼          ▼          ▼
 douyin/   kuaishou/   bilibili/   douyu/     yy/   huya/   twitch/
   │          │           │          │          │        （平台包，见 PLATFORMS.md）
   └──────────┴───────────┴────┬─────┴──────────┘
                             ▼
                 Room / Source 统一模型（core）
                             ▼
                 m3u.Merge() 增量合并（置顶/去重/保留策略）
                             ▼
   output/multilive.m3u + 各平台 *_live.m3u + output/status.json + output/run.log
```

核心模块：

| 文件 | 职责 |
|---|---|
| `cmd/multilive/main.go` | 入口：CLI 参数、平台并发调度、`Disabled`（平台下线集合） |
| `internal/config/` | sources.txt 解析 |
| `internal/registry/` | 平台注册表（集中注册） |
| `internal/core/` | 公共库：`Room`/`Source`/`Ctx` 模型、`Client`（标准库 HTTP 会话）、日志 |
| `internal/m3u/` | m3u 读写、增量合并、status 输出 |
| `internal/platform/` | `Platform` 接口（统一契约） |
| `internal/platforms/<平台>/` | 各平台实现（见 PLATFORMS.md） |
| `tools/browser_fetch_douyin.mjs` | douyin 浏览器兜底（可选，Patchright） |
| `tools/browser_fetch_tiktok.mjs` | tiktok 浏览器签名（Patchright） |

## 维护者 / AI 接管指引

> 先读根目录 **AGENTS.md**（架构、铁律、排障、Action 注意事项），再读 **PLATFORMS.md**（平台接口情报）。

## 接入新平台（30 秒版）

在 `internal/platforms/` 新建 `example/` 包实现 `platform.Platform` 接口
（`Name`/`Parse`/`Fetch`，`KeepStale`/`FallbackURL` 可选），再到
`internal/registry/registry.go` 的 `All()` 里加一行注册即可，
框架会自动参与并行抓取、合并与定时任务。完整约定与各平台的
接口调研结论见 **PLATFORMS.md**。
