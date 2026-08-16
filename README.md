# MultiLive — 多平台直播 m3u 聚合器

把多个直播平台（抖音 / 快手 / B站 / 虎牙 / 斗鱼…）的「在播直播间」
聚合到一份 `output/multilive.m3u`，并为**每个平台单独生成一份 m3u**，供 PotPlayer /
VLC / mpv / IINA 直接导入播放。

本仓库是 douyin-actions 与 kuaishou 两个方案的合并重构：统一了配置格式、
抓取框架、m3u 增量合并与定时更新，**新增平台只需写一个模块**，不碰公共代码。

## 订阅方式（直接导入播放器）

仓库每小时自动刷新一次，m3u 文件直接入库。支持 m3u 订阅的播放器
（PotPlayer / VLC / mpv / IINA / 电视盒子等）填入下面任一地址即可自动更新。
以下为国内可直接访问的代理地址（`gh-proxy.org` + raw 地址），
也可去掉代理前缀使用 GitHub 原始地址。

**全聚合（所有平台一个列表）**

```
https://gh-proxy.org/https://raw.githubusercontent.com/pan8664716/MultiLive/main/output/multilive.m3u
```

**各平台单独列表**

```
https://gh-proxy.org/https://raw.githubusercontent.com/pan8664716/MultiLive/main/output/douyin_live.m3u
https://gh-proxy.org/https://raw.githubusercontent.com/pan8664716/MultiLive/main/output/kuaishou_live.m3u
https://gh-proxy.org/https://raw.githubusercontent.com/pan8664716/MultiLive/main/output/bilibili_live.m3u
https://gh-proxy.org/https://raw.githubusercontent.com/pan8664716/MultiLive/main/output/huya_live.m3u
https://gh-proxy.org/https://raw.githubusercontent.com/pan8664716/MultiLive/main/output/douyu_live.m3u
```

> 提示：`multilive.m3u` 为全平台聚合；单独列表便于只想订阅某平台时使用。
> 若网络可直连 GitHub，把前缀 `https://gh-proxy.org/` 去掉即可用 raw 地址。

## 特性

- **纯 HTTP 优先**：快手 / B站 / 虎牙全部纯 HTTP（仅 Python 3.9+ 标准库）；
  douyin 保留「接口 → 浏览器(可选) → 页面」三级降级；斗鱼目录与播放地址
  主链路纯 HTTP（参考抖音思路：浏览器仅在取参被风控时兜底）。
- **平台插件化 + 并行**：`multilive/platforms/` 每个平台一个模块、自动注册；
  **不同平台并行拉取，每个平台内部固定 5 并发**。
- **增量合并**：本轮在播房间置顶 + 按「平台:房间号」全局去重 + 平台级保留策略
  （douyin 保留历史并用兜底解析地址，其余平台只留此刻在播）。
- **便于排查**：每次运行输出 `output/status.json`（房间数/耗时/合并统计，入库）
  与 `output/run.log`（滚动日志，不入库），并保留各平台独立 m3u。
- **定时更新**：内置 GitHub Actions，每小时（北京时间整点）自动刷新并提交。

## 快速开始

```bash
# 试跑（只打印统计，不写文件）
python3 multilive.py --dry-run

# 只跑单个平台（调试用）
python3 multilive.py --platform douyin --pages 2 --dry-run

# 正式更新（写入 output/multilive.m3u 与各平台 m3u）
python3 multilive.py
```

把生成的 `output/multilive.m3u`（或多平台各自的文件）拖进 PotPlayer / VLC 即可。

## 配置来源（sources.txt）

每行一个来源，推荐带平台前缀；`#` 开头为注释：

```
douyin:https://live.douyin.com/categorynew/4_105   # 抖音分类页（一整类在播房间）
douyin:https://live.douyin.com/745350622378        # 抖音直播间页
douyin:745350622378                                # 抖音纯房间号
kuaishou:HOT:50                                    # 快手热门页，抓 50 页（约 2000 房间）
bilibili:https://live.bilibili.com/all:5           # B站整站列表（页数可选，100 房间/页）
bilibili:https://live.bilibili.com/6               # B站直播间页
huya:https://www.huya.com/l:10                     # 虎牙全部直播（页数可选，120 房间/页）
douyu:https://www.douyu.com/directory/all:10       # 斗鱼全部频道（页数可选，120 房间/页）
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
- `output/douyin_live.m3u` / `output/kuaishou_live.m3u` / `output/bilibili_live.m3u` / `output/huya_live.m3u` / `output/douyu_live.m3u` — 每个平台单独一份
- `output/status.json` — 机器可读运行摘要（入库）
- `output/run.log` — 滚动日志（保留 3 份，不入库）
- `output/douyu_warm.json` — 斗鱼浏览器取参缓存（运行时生成，不入库）

## 部署到 GitHub（可选）

1. 新建仓库并推送本目录（本仓库远端：`git@github.com:pan8664716/MultiLive.git`）。
2. 仓库页 → **Actions** → **多平台直播 m3u 更新** → **Run workflow** 手动触发一次。
3. 之后每小时自动运行；更新后的 m3u 会直接提交回仓库。

> CI 内置 Node（仅用于斗鱼浏览器取参兜底，主链路纯 HTTP 不需要），无需
> 安装浏览器；douyin 浏览器兜底不启用，接口被风控时自动落到「页面解析」兜底。

## 架构总览

```
sources.txt ──► config.load_sources() ──► 平台注册表（自动发现）
                    │
                    ▼
           平台级并行（各平台一个线程）
   ┌──────────┬──────────┬──────────┬─────────┐
   ▼          ▼          ▼          ▼         ▼
douyin.py  kuaishou.py bilibili.py huya.py  douyu.py   ← 平台模块：parse() + fetch()
   │          │          │          │         │           （内部均 5 并发）
   └──────────┴──────────┴────┬─────┴─────────┘
                             ▼
                      Room 统一模型
                             ▼
                 m3u.merge() 增量合并（置顶/去重/保留策略）
                             ▼
   output/multilive.m3u + 各平台 *_live.m3u + output/status.json + output/run.log
```

核心模块：

| 文件 | 职责 |
|---|---|
| `multilive.py` | CLI 入口、平台并发调度、日志初始化 |
| `multilive/config.py` | sources.txt 解析、平台自动发现与注册 |
| `multilive/core.py` | `Room` 模型、`Session` HTTP 会话（CookieJar）、日志 |
| `multilive/m3u.py` | m3u 读写、增量合并、status 输出 |
| `multilive/platforms/*` | 平台实现（见 PLATFORMS.md） |
| `tools/browser_fetch_douyin.mjs` | douyin 浏览器兜底（可选，Patchright） |
| `tools/douyu_warm.mjs` | 斗鱼浏览器取参兜底（Patchright，可选） |

## 接入新平台（30 秒版）

在 `multilive/platforms/` 新建 `douyu.py`，实现 `NAME / parse / fetch` 即可，
框架会自动发现并参与并行抓取、合并与定时任务。完整约定与各平台的
接口调研结论见 **PLATFORMS.md**。
