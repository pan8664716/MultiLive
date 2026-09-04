# 平台接入指南（PLATFORMS）
## 通用铁律（所有平台一致，务必遵守）

1. **绝不逐房间调 API 获取信息/播放地址**（批量会触发风控）。列表与信息只用
   批量接口/SSR 页面内嵌数据；播放地址优先写
   `https://astar.cc.cd/<平台>/<房间号>`（虎牙写
   `https://astar.cc.cd/<平台>/<房间号>`），由 Worker/代理点播时
   实时解析（看哪个解析哪个）。
2. **不逐房间取播放地址**（bilibili `Room/playUrl`、斗鱼 `getH5PlayV1`、
   虎牙逐房间页均已废弃不用），一律写 Worker/代理解析地址。
3. 平台下线用 `cmd/multilive/main.go` 的 `Disabled` 集合，不删平台包。
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

在 `internal/platforms/` 下新建 `<平台名>/` 包，实现 `platform.Platform` 接口，
再到 `internal/registry/registry.go` 的 `All()` 里加一行注册：

| 类属性 / 方法 | 说明 |
|---|---|
| `Name()` | 平台名（用于 sources.txt 前缀、tvg-id、日志） |
| `Parse(line) -> [Source]` | 认领一行来源配置；不认领返回空 |
| `Fetch(sources, ctx) -> [Room]` | 抓取并返回统一模型 `Room` |
| `KeepStale()`（可选） | `true` 保留下播历史（有兜底地址时），`false` 只留此刻在播 |
| `FallbackURL(room)`（可选） | 历史条目兜底播放地址（douyin/kuaishou 用 astar.cc.cd） |
| 平台内并发（可选） | 用 `core.Parallel(workers, items, fn)`，每个任务独立 `Client` |

要点（旧 Python 版 `multilive/platforms/_template.py` 仅作语义参考）：

- 平台包变大后可在包内继续拆分 `api.go` / `stream.go` 等子文件，对外只保留 `Platform` 结构体作为稳定契约出口。
- `core.Room{Platform, RID, Title, Nickname, URL, Group, Avatar}`，无播放地址的房间直接跳过。
- 纯 HTTP 用 `core.NewClient`（标准库 `net/http` + CookieJar/UA/超时），
  数据模型 `Room`/`Source`/`Ctx` 也在 `internal/core`。
  纯标准库，零第三方依赖。
- `ctx.ProjectRoot`（读 tools/ 里的辅助脚本）、`ctx.PagesCap`（`--pages` 上限）。
- 单来源失败不要整体崩溃：日志提示后继续其他来源；并发优先用
  `core.Parallel(workers, items, fn)`（每个 goroutine 独立 `Client`）。
- 平台缩略名要能被用户一眼看懂：`douyin/kuaishou/bilibili/douyu/huya/twitch`。

`core.Source{Platform, Kind, Target, Meta}` 的 `Kind/Target` 语义平台自定，
`meta` 可存翻页数等整数配置（参考 kuaishou 的 `HOT:50`）。

## 已内置平台

### douyin（接口/浏览器/页面 三级降级）
- 分类接口：`live.douyin.com/webcast/web/partition/detail/room/v2/`
  （带固定 `a_bogus` 参数即可放行，实测 188 位固定值通用）
- 房间接口：`live.douyin.com/webcast/room/web/enter/`（需 ttwid Cookie）
- 页面兜底：解析 HTML 内嵌 RSC 数据（`self.__pace_f.push`）
- 浏览器兜底：`tools/browser_fetch_douyin.mjs`（Patchright，可选）
- 本轮在播房间优先写 `stream_url` 里的 CDN 直链（hls 优先，其次 flv，转 https）；
  `keep_stale=True`：历史房间原样保留，本轮无直链时回退 `https://astar.cc.cd/douyin/<rid>`

### kuaishou（纯 HTTP）
- `live.kuaishou.com/live_api/hot/list?type=HOT&filterType=0&page=N&pageSize=24`
- 免登录免签名，每页 50 房间
- 房间号取 `author.id`（`live.kuaishou.com/u/<id>` 短 ID，跨场次有效）
- 播放地址用列表自带 `playUrls` 里最高档 FLV 直链（批量返回，不逐房间取流）
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

### tiktok（浏览器签名 + 纯 HTTP 批量获取；不逐房间请求）
- TikTok `/live/category/*` feed 接口需 webmssdk SDK 签名（X-Gnarly/X-Dynosaur），
  纯 HTTP 无法自行生成签名。
- 混合方案：浏览器只负责「签名」，数据获取走纯 HTTP：
  1. `tools/browser_fetch_tiktok.mjs` 打开一个 Patchright 会话，依次导航
     所有分类页让 SDK 自然发出签名请求；
  2. 在 route 层拦截含 X-Gnarly 的 feed URL 并 abort（不让浏览器真正发请求）；
  3. 同时导出 ttwid/msToken 等 cookies；
  4. Go 用 `core.NewClient` 携带 cookies 重放每个签名 URL 获取数据（`Client.GetJSON`）。
- 首次直接打开某分类可能返回空列表；在同一会话内先跳到 /live 再回来可触发。
- 房间号取 `owner.display_id`（即用户 uniqueId）；播放地址统一写
  `https://astar.cc.cd/tiktok/<uniqueId>`。
- 注意：依赖浏览器（Action 已装 patchright + Chrome）+ Node；签名有时效性
  （每次运行重新获取）。TikTok 按地区运营，部分地区返回停服页。
- `keep_stale=False`


## 调研结论（后续平台的现成接口情报）

以下结论来自 2026-08 实机抓包验证，新平台实现时可直接采用。

### twitch（匿名 GQL 批量列表，纯 HTTP）
- 列表：`gql.twitch.tv/gql` 的 `streams` 查询（匿名可用，需网页版
  `Client-Id`，2026-08 实测），`first=30/页` + `after` 游标翻页；
  条目带 `broadcaster.login` / `displayName` / `game.name` /
  `previewImageURL`（`{width}x{height}` 需替换为实际尺寸）。
- 播放：列表接口不带流地址（`usher` 需要签名 sig/token），m3u 统一写
  `https://astar.cc.cd/twitch/<login>`，Worker 点播时实时解析。
- `keep_stale=False`（只留此刻在播）


### migu（咪咕视频 IPTV，官方频道列表接口，纯 HTTP）
- 列表：`program-sc.miguvideo.com/live/v2/tv-data/1ff892f2b5ab4a79be6e25b69d2f5d05`
  返回分类（`liveList`：央视/卫视/地方/体育/影视/新闻/教育/熊猫/综艺…，含「热门」
  重复分类需过滤）+ 央视频道（`dataList`）；其余分类按各自 `vomsID` 再拉
  `tv-data/<vomsID>` 拿 `dataList`，频道带 `pID` / `name` / `pics`（台标）。
- 播放：`play.miguvideo.com/playurl/v1/play/playurl` 需要逐频道 MD5 盐值签名
  （`md5(timestamp+pID+appVersion)` + 固定 salt`1230024`/后缀），**不逐频道取流**，
  m3u 统一写 `https://astar.cc.cd/migu/<pID>`，Worker 点播时实时解析。
- `keep_stale=True`（IPTV 频道稳定，接口波动时保留历史条目）

### 4gtv（台湾直播；内置静态频道清单，播放走 Worker 动态解析）
- 4gtv 官方接口 `api2.4gtv.tv/App/GetChannelUrl2` 需要**台湾 IP** 且逐频道
  请求：`4gtv_auth`（Base64→XOR(20241010-20241012)→二次Base64→AES-256-CBC→
  拼 today(YYYYMMDD)→SHA-512→hex→base64，日级有效）+ 随机 `fsenc_key` +
  `okhttp/3.12.11` UA，取流后还要 1080p 级联探测回退；CI/数据中心无法直连、
  也不符合「不逐房间取流」铁律，因此播放地址统一写
  `https://astar.cc.cd/4gtv/<频道ID>`，Worker 点播时按上述逻辑实时解析
  （需台湾出口）。
- 列表：官方列表接口同样需台湾 IP，采用**内置静态清单**
  `internal/platforms/fourgtv/channels.tsv`（内嵌 `go:embed`，56 个频道，id/名称/分组；
  来自公开 4gtv 频道表整理，新增频道直接往该文件加行即可）。
- `keep_stale=True`（IPTV 频道稳定，保留历史条目）

## 调试技巧

1. 先 `go run ./cmd/multilive --platform <新平台> --dry-run`（或先 `go build -o multilive ./cmd/multilive`）。
2. `--verbose` 看 DEBUG 日志；`output/run.log` 保留近 3 轮日志（不入库）。
3. `output/status.json` 对比每轮房间数变化，判断是否被风控/接口变动。
4. 单平台单独验证 OK 后再放回 `sources.txt` 全量跑。
5. 并发规则：平台之间 goroutine 并行；平台内部分页并发数在各自包顶部常量（多为 3~5）。
