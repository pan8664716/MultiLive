# 平台接入指南（PLATFORMS）

## 一个平台 = 一个模块

在 `multilive/platforms/` 下新建 `douyu.py`，实现 3 个约定就会被自动注册：

| 约定 | 说明 |
|---|---|
| `NAME` | 平台名（用于 sources.txt 前缀、tvg-id、日志） |
| `parse(line) -> [Source]` | 认领一行来源配置；不认领返回 `[]` |
| `fetch(sources, ctx) -> [Room]` | 抓取并返回统一模型 `Room` |
| `keep_stale`（可选） | `True` 保留下播历史（有兜底地址时），`False` 只留此刻在播 |
| `fallback_url(room)`（可选） | 历史条目兜底播放地址（douyin 用 pages.dev） |

模板见 `multilive/platforms/_template.py`。要点：

- `Room(platform, rid, title, nickname, url, group, avatar)`，无播放地址的房间直接跳过。
- 纯 HTTP 用 `from multilive.core import Session`（自带 CookieJar/UA/超时）；
  一次性 GET 用 `core.http_json`。所有平台零第三方依赖（标准库）。
- `ctx.project_root`（读 tools/ 里的辅助脚本）、`ctx.pages_cap`（`--pages` 上限）。
- 单来源失败不要整体崩溃：日志提示后继续其他来源；并发用
  `concurrent.futures.ThreadPoolExecutor`，每个线程独立 `Session`。
- 平台缩略名要能被用户一眼看懂：`douyin/kuaishou/bilibili/douyu/huya/twitch`。

`Source(platform, kind, target, meta=0)` 的 `kind/target` 语义平台自定，
`meta` 可存翻页数等整数配置（参考 kuaishou 的 `HOT:50`）。

## 已内置平台

### douyin（接口/浏览器/页面 三级降级）
- 分类接口：`live.douyin.com/webcast/web/partition/detail/room/v2/`
  （带固定 `a_bogus` 参数即可放行，实测 188 位固定值通用）
- 房间接口：`live.douyin.com/webcast/room/web/enter/`（需 ttwid Cookie）
- 页面兜底：解析 HTML 内嵌 RSC 数据（`self.__pace_f.push`）
- 浏览器兜底：`tools/browser_fetch_douyin.mjs`（Patchright，可选）
- `keep_stale=True`：历史房间回退 `https://douyin-m3u8.pages.dev/room/<rid>`

### kuaishou（纯 HTTP）
- `live.kuaishou.com/live_api/hot/list?type=HOT&filterType=0&page=N&pageSize=24`
- 免登录免签名，每页 50 房间 + 4 档 CDN 直链（取最高 `level`）
- `keep_stale=False`：只保留此刻在播

### bilibili（纯 HTTP，单房间）
### bilibili（纯 HTTP，整站列表 + 单房间）
- 整站列表：`api.live.bilibili.com/room/v1/room/get_user_recommend?page=N&page_size=100`
  （`live.bilibili.com/all` 同款；旧接口 `second/getList` 匿名被风控 -352）
- 单房间信息：`api.live.bilibili.com/room/v1/room/get_info?room_id=`
- 播放：`api.live.bilibili.com/room/v1/Room/playUrl?cid=<room_id>&quality=0&platform=web`
- `keep_stale=False`

### huya（纯 HTTP，2026-08 实测可播）
- 列表：`www.huya.com/cache.php?m=LiveList&do=getLiveListByPage&tagAll=0&page=N`
  （120 房间/页；条目里 `profileRoom` 才是房间号，`uid` 不是）
- 播放：逐房间页 `www.huya.com/<房间号>` 解析内嵌 `stream: {...}` JSON：
  `gameLiveInfo`（昵称/房间名/游戏）+ `gameStreamInfoList[0]`；
  地址 = `sFlvUrl + '/' + sStreamName + '.' + sFlvUrlSuffix + '?' + sFlvAntiCode`，
  实测直接 200 + FLV 头，无需再算签名。
- `keep_stale=False`（只留此刻在播）

### douyu（目录纯 HTTP；播放地址当前被站点锁死）
- 目录：`www.douyu.com/gapi/rkc/directory/0_0/<page>`（120 房间/页，
  含 `nn` 昵称 / `rn` 房间名 / `c2name` 分类 / `rs16` 封面）
- 播放（2026-08 实测）：`betard` 不再下发 `rtmp_url`；`getH5Play` 需要
  `homeH5Enc` 动态下发的 42KB 混淆 JS 签名——Node 可执行签名 JS 并产出 sign，
  但接回 `getH5Play` 仍 403「鉴权失败」，说明还差播放器请求中的会话标识。
  当前实现：目录纯 HTTP 拉取元数据 + `tools/douyu_play.mjs` 尝试解析，
  解析不到的房间直接跳过（不会整体失败）。站点策略松动后在
  `tools/douyu_play.mjs` / `douyu.py` 内跟进即可。
- `keep_stale=False`

## 调研结论（后续平台的现成接口情报）

以下结论来自 2026-08 实机抓包验证，新平台实现时可直接采用。

### 待接入：Twitch
- 官方 API（Helix）需要 `Client-ID` + OAuth token，播放地址走
  `usher.ttvnw.net/api/channel/hls/<channel>.m3u8` 需要签名 sig/token，
  **匿名纯 HTTP 拿不到**。`keep_stale=True`（保留历史 + 播放器二次解析）。
- 若匿名需求明确，可考虑第三方 m3u8 聚合站点做兜底地址（类似 douyin 的
  pages.dev 方案），稳定性和合规需自己评估。

## 调试技巧

1. 先 `python3 multilive.py --platform <新平台> --dry-run`。
2. `--verbose` 看 DEBUG 日志；`out/run.log` 保留近 3 轮日志。
3. `out/status.json` 对比每轮房间数变化，判断是否被风控/接口变动。
4. 单平台单独验证 OK 后再放回 `sources.txt` 全量跑。
5. 并发规则：平台之间并行，平台内部固定 5 并发（各平台文件顶部常量）。
