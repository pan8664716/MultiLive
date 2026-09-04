// Package bilibili B站直播：整站列表 + 分区列表 + 单房间元数据。
package bilibili

import (
	"fmt"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"multilive/internal/core"
	"multilive/internal/platform"
)

const (
	playerBase = "https://astar.cc.cd/bilibili/%s"
	infoAPI    = "https://api.live.bilibili.com/room/v1/room/get_info"
	recAPI     = "https://api.live.bilibili.com/room/v1/room/get_user_recommend"
	areaAPI    = "https://api.live.bilibili.com/room/v1/area/getRoomList"
	areaMapAPI = "https://api.live.bilibili.com/xlive/web-room/v1/index/getRoomBaseInfo"
)

const (
	defaultListPages = 5
	maxListPages     = 20
	maxWorkers       = 3
)

var (
	areaTagsRe = regexp.MustCompile(`^https?://live\.bilibili\.com/p/eden/area-tags`)
	roomRe     = regexp.MustCompile(`^https?://live\.bilibili\.com/(\d+)`)
	allRe      = regexp.MustCompile(`^https?://live\.bilibili\.com/all(:\d+)?`)
	tailNumRe  = regexp.MustCompile(`:(\d+)$`)
)

type Platform struct{ platform.Base }

func (Platform) Name() string    { return "bilibili" }
func (Platform) KeepStale() bool { return false }

func clampPages(n int) int {
	if n < 1 {
		n = defaultListPages
	}
	if n > maxListPages {
		n = maxListPages
	}
	return n
}

func parsePagesSuffix(t string) (int, string) {
	pages := defaultListPages
	if m := tailNumRe.FindStringSubmatch(t); m != nil {
		if n, err := strconv.Atoi(m[1]); err == nil {
			pages = n
		}
		t = t[:len(t)-len(m[0])]
	}
	return clampPages(pages), t
}

func (Platform) Parse(line string) ([]core.Source, error) {
	t := strings.TrimSpace(line)
	if areaTagsRe.MatchString(t) {
		pages, tt := parsePagesSuffix(t)
		u, err := url.Parse(tt)
		if err != nil {
			return nil, nil
		}
		q := u.Query()
		pa, aa := q.Get("parentAreaId"), q.Get("areaId")
		if isDigits(pa) && isDigits(aa) && pa != "" {
			return []core.Source{{Platform: "bilibili", Kind: "area", Target: pa + ":" + aa, Meta: pages}}, nil
		}
		return nil, nil
	}
	if m := roomRe.FindStringSubmatch(t); m != nil {
		return []core.Source{{Platform: "bilibili", Kind: "room", Target: m[1]}}, nil
	}
	if m := allRe.FindStringSubmatch(t); m != nil {
		pages := defaultListPages
		if m[1] != "" {
			if n, err := strconv.Atoi(m[1][1:]); err == nil {
				pages = n
			}
		}
		return []core.Source{{Platform: "bilibili", Kind: "all", Target: "ALL", Meta: clampPages(pages)}}, nil
	}
	return nil, nil
}

func isDigits(s string) bool {
	if s == "" {
		return false
	}
	for _, c := range s {
		if c < '0' || c > '9' {
			return false
		}
	}
	return true
}

func getJSON(sess *core.Client, u, referer string) (map[string]any, error) {
	var j map[string]any
	var last error
	for attempt := 0; attempt < 3; attempt++ {
		j = nil
		st, _, err := sess.GetJSON(u, referer, &j, 0)
		if err == nil {
			return j, nil
		}
		last = err
		if (st == 403 || st == 412 || st == 429) && attempt < 2 {
			wait := []float64{2.0, 6.0}[attempt]
			core.Infof("  [retry] 被限速(%d)，%.1fs 后重试", st, wait)
			time.Sleep(time.Duration(wait * float64(time.Second)))
			continue
		}
		break
	}
	return nil, last
}

func fetchRoom(shortID string) *core.Room {
	sess := core.NewClient(nil)
	j, err := getJSON(sess, infoAPI+"?room_id="+shortID, "")
	if err != nil {
		core.Infof("  [房间] %s 解析失败: %s", shortID, core.FmtErr(err, 80))
		return nil
	}
	info := core.JMap(j, "data")
	if info == nil {
		return nil
	}
	var rid float64
	switch v := info["room_id"].(type) {
	case float64:
		rid = v
	case string:
		n, _ := strconv.Atoi(v)
		rid = float64(n)
	}
	var liveStatus float64
	if v, ok := info["live_status"].(float64); ok {
		liveStatus = v
	}
	if rid == 0 || liveStatus != 1 {
		return nil
	}
	ridStr := strconv.Itoa(int(rid))
	group := strings.TrimSpace(core.JStr(info, "area_name"))
	if group == "" {
		group = "bilibili"
	}
	return &core.Room{
		Platform: "bilibili", RID: ridStr,
		Title:    strings.TrimSpace(core.JStr(info, "title")),
		Nickname: strings.TrimSpace(core.JStr(info, "uname")),
		URL:      fmt.Sprintf(playerBase, ridStr),
		Group:    group,
		Avatar:   core.FirstStr(core.JStr(info, "user_cover"), core.JStr(info, "keyframe")),
	}
}

func fetchRecPage(page int) []map[string]any {
	sess := core.NewClient(nil)
	var j map[string]any
	_, _, err := sess.GetJSON(fmt.Sprintf("%s?page=%d&page_size=100", recAPI, page), "", &j, 0)
	if err != nil {
		core.Infof("  [列表] 第%d页失败: %s", page, core.FmtErr(err, 120))
		return nil
	}
	var out []map[string]any
	for _, it := range core.JList(j, "data") {
		if m, ok := it.(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out
}

func fetchAreaPage(pa, aa string, page int) []map[string]any {
	sess := core.NewClient(nil)
	u := fmt.Sprintf("%s?parent_area_id=%s&area_id=%s&page=%d&page_size=100", areaAPI, pa, aa, page)
	var j map[string]any
	_, _, err := sess.GetJSON(u, "https://live.bilibili.com/p/eden/area-tags", &j, 0)
	if err != nil {
		core.Infof("  [分区] parent=%s area=%s 第%d页失败: %s", pa, aa, page, core.FmtErr(err, 120))
		return nil
	}
	var out []map[string]any
	for _, it := range core.JList(j, "data") {
		if m, ok := it.(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out
}

func ridOf(item map[string]any) string {
	switch v := item["roomid"].(type) {
	case float64:
		return strconv.Itoa(int(v))
	case string:
		return v
	}
	return ""
}

func buildRoom(item map[string]any) *core.Room {
	rid := ridOf(item)
	if rid == "" {
		return nil
	}
	group := strings.TrimSpace(core.FirstStr(
		strOf(item["area_v2_name"]), strOf(item["area_name"])))
	if group == "" {
		group = "bilibili"
	}
	return &core.Room{
		Platform: "bilibili", RID: rid,
		Title:    strings.TrimSpace(strOf(item["title"])),
		Nickname: strings.TrimSpace(strOf(item["uname"])),
		URL:      fmt.Sprintf(playerBase, rid),
		Group:    group,
		Avatar:   core.FirstStr(strOf(item["user_cover"]), strOf(item["face"])),
	}
}

func strOf(v any) string {
	switch t := v.(type) {
	case string:
		return t
	case float64:
		return strconv.Itoa(int(t))
	}
	return ""
}

func fetchAreaMap(uids []string) map[string]string {
	area := map[string]string{}
	sess := core.NewClient(nil)
	for i := 0; i < len(uids); i += 50 {
		chunk := uids[i:min(i+50, len(uids))]
		qs := make([]string, 0, len(chunk)+1)
		qs = append(qs, "req_biz=web_room_componet")
		for _, u := range chunk {
			qs = append(qs, "uids="+u)
		}
		var j map[string]any
		_, _, err := sess.GetJSON(areaMapAPI+"?"+strings.Join(qs, "&"), "https://live.bilibili.com/all", &j, 0)
		if err != nil {
			core.Infof("  [分区] 批量%d个uid失败: %s", len(chunk), core.FmtErr(err, 120))
			continue
		}
		byUIDs, _ := core.JMap(j, "data")["by_uids"].(map[string]any)
		for uid, info := range byUIDs {
			if m, ok := info.(map[string]any); ok {
				if name := strings.TrimSpace(strOf(m["area_name"])); name != "" {
					area[uid] = name
				}
			}
		}
	}
	return area
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func fetchAll(sources []core.Source, ctx *core.Ctx) []core.Room {
	pages := 0
	for _, s := range sources {
		if s.Meta > pages {
			pages = s.Meta
		}
	}
	if pages <= 0 {
		pages = defaultListPages
	}
	if ctx.PagesCap > 0 && pages > ctx.PagesCap {
		pages = ctx.PagesCap
	}
	pageNums := make([]int, pages)
	for i := range pageNums {
		pageNums[i] = i + 1
	}
	results := core.Parallel(maxWorkers, pageNums, fetchRecPage)
	items := map[string]map[string]any{}
	for _, list := range results {
		for _, it := range list {
			if rid := ridOf(it); rid != "" {
				if _, ok := items[rid]; !ok {
					items[rid] = it
				}
			}
		}
	}
	core.Infof("  [列表] %d页共 %d 个不重复房间", pages, len(items))
	var out []core.Room
	for _, it := range items {
		if r := buildRoom(it); r != nil {
			out = append(out, *r)
		}
	}
	var uids []string
	for _, it := range items {
		if u := strOf(it["uid"]); u != "" {
			uids = append(uids, u)
		}
	}
	if len(uids) > 0 {
		area := fetchAreaMap(uids)
		if len(area) > 0 {
			core.Infof("  [分区] 批量获取 %d/%d 个房间分类", len(area), len(items))
		}
		for i := range out {
			if it, ok := items[out[i].RID]; ok {
				if name, ok := area[strOf(it["uid"])]; ok {
					out[i].Group = name
				}
			}
		}
	}
	core.Infof("  [解析] %d 个在播房间（播放地址走 Worker 动态解析，未逐个取流）", len(out))
	return out
}

func (Platform) Fetch(sources []core.Source, ctx *core.Ctx) ([]core.Room, error) {
	core.Infof("[bilibili] %d 个来源", len(sources))
	var out []core.Room
	var mu sync.Mutex
	var allSrcs, areaSrcs []core.Source
	var roomIDs []string
	for _, s := range sources {
		switch s.Kind {
		case "all":
			allSrcs = append(allSrcs, s)
		case "area":
			areaSrcs = append(areaSrcs, s)
		case "room":
			roomIDs = append(roomIDs, s.Target)
		}
	}
	if len(allSrcs) > 0 {
		out = append(out, fetchAll(allSrcs, ctx)...)
	}
	if len(areaSrcs) > 0 {
		type ar struct {
			pa, aa string
			pages  int
		}
		var reqs []ar
		for _, s := range areaSrcs {
			pa, aa, _ := strings.Cut(s.Target, ":")
			pages := s.Meta
			if pages <= 0 {
				pages = defaultListPages
			}
			if ctx.PagesCap > 0 && pages > ctx.PagesCap {
				pages = ctx.PagesCap
			}
			reqs = append(reqs, ar{pa, aa, pages})
		}
		lists := core.Parallel(maxWorkers, reqs, func(r ar) map[string]map[string]any {
			m := map[string]map[string]any{}
			for page := 1; page <= r.pages; page++ {
				for _, it := range fetchAreaPage(r.pa, r.aa, page) {
					if rid := ridOf(it); rid != "" {
						if _, ok := m[rid]; !ok {
							m[rid] = it
						}
					}
				}
			}
			core.Infof("  [分区] parent=%s area=%s %d页: %d 个房间", r.pa, r.aa, r.pages, len(m))
			return m
		})
		seen := map[string]bool{}
		for _, m := range lists {
			for rid, it := range m {
				if !seen[rid] {
					seen[rid] = true
					if r := buildRoom(it); r != nil {
						mu.Lock()
						out = append(out, *r)
						mu.Unlock()
					}
				}
			}
		}
		core.Infof("  [分区] 合计 %d 个不重复房间", len(seen))
	}
	rooms := core.Parallel(maxWorkers, roomIDs, fetchRoom)
	for _, r := range rooms {
		if r != nil {
			out = append(out, *r)
		}
	}
	core.Infof("[bilibili] 完成: %d 在播房间", len(out))
	return out, nil
}

var _ platform.Platform = Platform{}
