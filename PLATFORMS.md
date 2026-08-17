# 平台接入指南（PLATFORMS）

## 通用铁律（所有平台一致，务必遵守）

1. **绝不逐房间调 API 获取信息/播放地址**（批量会触发风控）。列表与信息只用
   批量接口/SSR 页面内嵌数据；播放地址优先写
   `https://astar.cc.cd/<平台>/<房间号>`（虎牙写
   `https://astar.cc.cd/<平台>/<房间号>`），由 Worker/代理点播时
   实时解析（看哪个解析哪个）。
2. **不逐房间取播放地址**（bilibili `Room/playUrl`、斗鱼 `getH5PlayV1`、
   虎牙逐房间页均已废弃不用），一律写 Worker/代理解析地址。
3. 平台下线用 `multilive.py` 的 `DISABLED` 集合，不删模块。
4. 播放地址拿不到的房间直接跳过；单来源失败打印日志后继续，不整体崩溃。

## 调研新平台的方法论（接口情报怎么来）

1. 浏览器抓包（DevTools / patchright）看列表与取流请求，确认是否带签名/cookie。
2. 优先找**批量列表**接口（SSR 页面内嵌 / 分页接口），播放地址优先交给
   astar.cc.cd 的 Worker 点播解析。
3. 纯 HTTP 复现并用 curl/node 实测：状态码、响应字段、FLV/HLS 头（注意
   直播流是无限流，验证时读几个字节就断，别用 Range 拉大包）。
4. 未开播/不存在的返回结构也要摸清，用于区分「未开播(404)」与「风控(重试)」。
5. 单平台 `--dry-run` 验证通过后再放回 `sources.txt` 全量跑。

## 一个平台 = 一个文件夹（统一契约）

在 `multilive/platforms/` 下新建 `<平台名>/` 文件夹，在 `__init__.py` 里
继承 `Platform` 基类并导出 `platform` 实例，注册表（`multilive/registry.py`）
就会自动发现：

| 类属性 / 方法 | 说明 |
|---|---|
| `name` | 平台名（用于 sources.txt 前缀、tvg-id、日志） |
| `parse(line) -> [Source]` | 认领一行来源配置；不认领返回 `[]` |
| `fetch(sources, ctx) -> [Room]` | 抓取并返回统一模型 `Room` |
| `keep_stale`（可选） | `True` 保留下播历史（有兜底地址时），`False` 只留此刻在播 |
| `fallback_url(room)`（可选） | 历史条目兜底播放地址（douyin 用 pages.dev） |
| `max_workers`（可选） | `self.parallel_map` 默认并发数，默认 3 |

模板见 `multilive/platforms/_template.py`。要点：

- 平台文件变大后可继续在文件夹内拆分 `api.py` / `stream.py` 等子模块，
  `__init__.py` 只保留「类 + platform 实例」作为稳定契约出口。
- `Room(platform, rid, title, nickname, url, group, avatar)`，无播放地址的房间直接跳过。
- 纯 HTTP 用 `from multilive.core import Session`（自带 CookieJar/UA/超时），
  数据模型 `Room`/`Source` 也在 `multilive.core`。
  所有平台零第三方依赖（标准库）。
- `ctx.project_root`（读 tools/ 里的辅助脚本）、`ctx.pages_cap`（`--pages` 上限）。
- 单来源失败不要整体崩溃：日志提示后继续其他来源；并发优先用
  `self.parallel_map(fn, items)`（内部 ThreadPoolExecutor，每个任务独立 `Session`）。
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
- 免登录免签名，每页 50 房间
- 房间号取 `author.id`（`live.kuaishou.com/u/<id>` 短 ID，跨场次有效）；
  播放地址统一写 `https://astar.cc.cd/kuaishou/<房间号>`，不逐房间取 CDN 直链
- `keep_stale=False`：只保留此刻在播

### bilibili（纯 HTTP，整站列表 + 单房间）
- 整站列表：`api.live.bilibili.com/room/v1/room/get_user_recommend?page=N&page_size=100`
  （`live.bilibili.com/all` 同款；旧接口 `second/getList` 匿名被风控 -352）。
  该接口不带分区字段，列表房间用批量接口
  `xlive/web-room/v1/index/getRoomBaseInfo?req_biz=web_room_componet&uids=…`
  一次性补一级分区名（`area_name`），不逐房间取信息。
- 单房间信息：`api.live.bilibili.com/room/v1/room/get_info?room_id=`
- 播放：不逐房间取流（避免风控），m3u 条目直接写
  `https://astar.cc.cd/bilibili/<房间号>`，Worker 点播时实时解析。
  仅单个直播源仍走一次 `room/get_info` 取元数据（判断在播/标题/昵称），
  不再调 `Room/playUrl`。
- `keep_stale=False`

### huya（列表纯 HTTP；播放地址走自建代理动态解析，2026-08 恢复）
- 列表：`www.huya.com/cache.php?m=LiveList&do=getLiveListByPage&tagAll=0&page=N`
  （120 房间/页；条目里 `profileRoom` 才是房间号，`uid` 不是）
- 播放：不逐房间取流；m3u 条目统一写
  `https://astar.cc.cd/<平台>/<房间号>`，由代理点播时实时解析，
  看哪个解析哪个。
- 背景（2026-08-17 js-reverse 逆向实测，为什么不能直接写直链）：
  - 官方网页播放器全程走 **P2P slice 私有协议**（`p2p.huya.com/huyalive/{SN}_505_2_66.slice`
    或 `/websocket/` 长连接形态），浏览器不发任何 FLV/HLS 直播请求；token 由 ws RPC
    （`getCdnTokenInfoEx`/`getP2PStreamTokenInfoEx`）每 4 分钟续一次。
  - 页面内嵌 `sFlvUrl/.flv`：302 到阿里 TBCache 边缘，只吐 ~1MB 窗口就
    `Connection: close` 且一会 200 一会 403；`sHlsUrl/.m3u8` 403 已死。
  - P2P slice（页面内嵌 `sP2pAntiCode` 可用）能连续拉流（wsTime 过期 30+ 分钟
    仍 200），但容器是私有分片格式，PotPlayer/VLC/mpv 无法直接解码。
- `keep_stale=False`（只留此刻在播）

### douyu（目录纯 HTTP；播放地址走 Worker 动态解析，2026-08 实测）
- 目录：`www.douyu.com/gapi/rkc/directory/0_0/<page>`（120 房间/页，
  含 `nn` 昵称 / `rn` 房间名 / `c2name` 分类 / `rs16` 封面）
- 播放：不逐房间调 `getH5PlayV1` 取流（2026-08 实测：直链带 wsAuth/token
  有签名时效，签发几分钟后 CDN 只吐 1-2s 缓冲窗口就断开，无法支撑 m3u
  长期播放）。m3u 条目直接写 `https://astar.cc.cd/douyu/<房间号>`，
  Worker 点播时实时解析新签名直链，看哪个解析哪个。
- `keep_stale=False`


### yy（列表纯 HTTP；播放地址走 Worker 动态解析，2026-08 实测可播）
- 频道页 `https://www.yy.com/{dancing|pretty|music}` 为 SSR，页面只内嵌
  第一页在播房间（`data-url="/{sid}/{ssid}"` + `data-title` 房间标题）；
  页面 `pageInfo` 给出 totalCount/moduleId/biz，再用
  `www.yy.com/more/page.action?biz=…&moduleId=…&pageSize=200` 分页补齐
  全部房间；实测 dancing≈160 / music≈390 / pretty≈140（随直播浮动）。
- 播放地址不逐个取流（避免风控），m3u 条目直接写
  `https://astar.cc.cd/yy/<房间号>`：Worker 在点播时通过
  `stream-manager.yy.com/v3/channel/streams`（纯 POST 固定 JSON，
  无 cookie/签名，gear=4 蓝光最高画质）实时解析 FLV 直链并 302，
  看哪个解析哪个。列表抓取全程零风险纯 HTTP。
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
2. `--verbose` 看 DEBUG 日志；`output/run.log` 保留近 3 轮日志（不入库）。
3. `output/status.json` 对比每轮房间数变化，判断是否被风控/接口变动。
4. 单平台单独验证 OK 后再放回 `sources.txt` 全量跑。
5. 并发规则：平台之间并行；平台内部并发数在各自文件顶部常量（多为 3~5）。
