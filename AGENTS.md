# AGENTS.md — MultiLive 交接说明（给后续 AI / 开发者）

## 这是什么

多平台直播 m3u 聚合器：每小时把各平台「在播直播间」抓成 m3u 并推回 GitHub，供 PotPlayer/VLC/mpv 等直接订阅播放。

- 用户侧产物：`output/multilive.m3u`（全聚合）+ `output/<平台>_live.m3u`（单平台），订阅地址见 README（走 `gh-proxy.org` 代理前缀）
- 远端：`git@github.com:pan8664716/MultiLive.git`，GitHub Action 每小时自动跑 `python3 multilive.py` 并提交
- 播放地址形态：**抖音/快手写列表接口自带的 CDN 直链**（抖音 HLS、快手 FLV，均批量返回，不逐房间取流）；yy/bilibili/douyu/huya 不写直链，m3u 统一写 `https://astar.cc.cd/<平台>/<房间号>`，由 Worker 点播时实时解析（看哪个解析哪个）

## 快速开始

```bash
python3 multilive.py --dry-run              # 只打印统计，不写文件
python3 multilive.py --platform yy          # 只刷某个平台（排查用）
python3 multilive.py --verbose              # 全量 + DEBUG 日志
python3 multilive.py --pages 2              # 限制翻页数
```

## 架构与数据流

```
sources.txt → config.load_sources()（经 registry 自动发现平台）
  → 平台级并行（每个平台一个线程）
  → 各平台 fetch(sources, ctx) 返回 [Room]
  → m3u.merge() 增量合并（去重/置顶/保留策略）
  → output/multilive.m3u + output/<平台>_live.m3u + output/status.json
```

核心模块：

| 文件 | 职责 |
|---|---|
| `multilive.py` | 薄入口（转调 `multilive/cli.py`，保持 `python3 multilive.py` 用法不变） |
| `multilive/cli.py` | CLI 参数、平台并行调度、`DISABLED`（平台下线集合）、日志 |
| `multilive/config.py` | sources.txt 解析 |
| `multilive/registry.py` | 平台注册表：自动发现 `platforms/` 下的 Platform 实例 |
| `multilive/core.py` | 公共库：`Room`/`Source` 模型、`Session`（纯标准库 HTTP）、`log`/`fmt_exc` |
| `multilive/m3u.py` | m3u 读写、增量合并、status 输出 |
| `multilive/platforms/base.py` | `Platform` 基类（统一契约）+ 平台公共小工具 |
| `multilive/platforms/<平台>/` | 各平台实现（每个平台一个文件夹，契约见 PLATFORMS.md / `_template.py`） |
| `tools/*.mjs` | 浏览器兜底脚本（Patchright，仅 douyin 取参兜底用） |

平台契约（统一方式）：每个平台一个文件夹 `multilive/platforms/<平台>/`，在
`__init__.py` 里继承 `Platform` 基类、实现 `parse(line)->[Source]` 与
`fetch(sources,ctx)->[Room]`，并导出 `platform` 实例，注册表即自动发现；
`keep_stale`/`fallback_url` 为可选钩子。新增平台照抄
`multilive/platforms/_template.py`。

## 铁律（改代码前必读）

1. **绝不逐房间调 API 获取信息或播放地址**（批量请求会触发风控）。只能：
   - 列表/信息：批量接口、SSR 页面内嵌数据（如 B站 `getRoomBaseInfo?uids=…` 50个/请求）；
   - 播放地址：抖音/快手直接用列表接口自带的 CDN 直链（批量返回，不逐房间取流）；
     其余平台统一写 `https://astar.cc.cd/<平台>/<房间号>`，由 Worker 点播时解析；
   - **不逐房间取播放地址**（bilibili `Room/playUrl`、斗鱼 `getH5PlayV1`、虎牙逐房间页均已废弃不用）。
2. 平台暂时下线：改 `multilive/cli.py` 顶部 `DISABLED = set()`（当前无平台下线），把平台名加进集合（不抓取 + 每轮清空该平台输出），**不要删平台文件夹**；恢复 = 移出集合 + `sources.txt` 取消注释。
3. 增量合并规则（`m3u.merge`）：先按「平台:房间号」删重复；本轮条目全部置顶；未运行平台的历史原样保留；`keep_stale=False` 的平台本轮运行后丢弃下播房间。
4. `output/*.m3u` 与 `output/status.json` **入库**（订阅靠它们）。改了抓取逻辑必须本地跑通再提交，别只改代码不产出。
5. HTTP 只用标准库 `Session`（`get/get_text/get_json/post_json`，自带 CookieJar/UA）；文档里旧写的 `core.http_json` 不存在，别用。
6. 单来源失败不整体崩溃：打印日志继续；并发用 `ThreadPoolExecutor`，每个线程独立 `Session`。

## 排障速查

- 先 `python3 multilive.py --platform <x> --dry-run --verbose`；`output/run.log` 滚动保留 3 份，`output/status.json` 每轮房间数可对比是否被风控/接口变动。
- 常见风控码：B站 `-412`（数据中心 IP）/ `-352`；douyin ttwid/滑块；YY SDK `result:2`；斗鱼 websec 加密。
- B站播放地址同样走 Worker（`astar.cc.cd/bilibili/<房间号>`）点播解析，MultiLive 内不再逐房间调 `Room/playUrl`。
- 平台数量异常少：多半是分页没抓全（YY 教训：SSR 只有第一页，需页面 `pageInfo` 的 `totalCount/moduleId/biz` 走 `/more/page.action` 补齐）。
- 接口失效：先用浏览器抓包找新接口，再转纯 HTTP 复现（方法论见 PLATFORMS.md）。

## GitHub Action 注意

- `.github/workflows/update-m3u.yml`：每小时 cron（UTC 整点）+ 手动触发；安装 Node 24 + `npm ci`（patchright 依赖），**不要删 `package-lock.json`**。
- Action 只提交 `output/` 下文件；本地 push 前若远端领先（Action 刚提交过），先 `git fetch` + rebase；输出文件冲突时以「最新数据优先」解决（取远端做基底，重跑目标平台合并，参考 git 历史里的同类提交）。
- 订阅 URL 用 `https://gh-proxy.org/https://raw.githubusercontent.com/pan8664716/MultiLive/main/output/<file>`（国内可直连）。

## 关联项目

- **Cloudflare Pages Worker**（YY/B站/抖音/斗鱼等点播解析，域名 `astar.cc.cd`）：`/Users/star/Downloads/douyin/douyin-m3u8-cf`，单文件 `_worker.js`。注意：该仓库本地**无 git remote** 且有未提交改动，涉及它时不要直接 push，先与用户确认。
- **统一动态解析**：`https://astar.cc.cd/<平台>/<房间号>`，点播时实时解析各平台完整签名直链。
- douyin-actions / kuaishou：本项目前身，合并后已废弃，仅作接口情报参考。
