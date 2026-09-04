// Package huya 虎牙直播：cache.php 列表纯 HTTP。
package huya

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"

	"multilive/internal/core"
	"multilive/internal/platform"
)

const (
	listAPI    = "https://www.huya.com/cache.php?m=LiveList&do=getLiveListByPage&tagAll=0&page=%d"
	playerBase = "https://astar.cc.cd/huya/%s"
)

const (
	defaultPages = 10
	maxPages     = 79
	listWorkers  = 5
)

var lRe = regexp.MustCompile(`^https?://www\.huya\.com/l(:\d+)?`)

type Platform struct{ platform.Base }

func (Platform) Name() string    { return "huya" }
func (Platform) KeepStale() bool { return false }

func (Platform) Parse(line string) ([]core.Source, error) {
	m := lRe.FindStringSubmatch(strings.TrimSpace(line))
	if m == nil {
		return nil, nil
	}
	pages := defaultPages
	if m[1] != "" {
		if n, err := strconv.Atoi(m[1][1:]); err == nil {
			pages = n
		}
	}
	if pages < 1 {
		pages = 1
	}
	if pages > maxPages {
		pages = maxPages
	}
	return []core.Source{{Platform: "huya", Kind: "list", Target: "ALL", Meta: pages}}, nil
}

type listMeta struct {
	nickname, title, group, avatar string
}

func fetchListPage(page int) map[string]listMeta {
	out := map[string]listMeta{}
	sess := core.NewClient(nil)
	var j map[string]any
	_, _, err := sess.GetJSON(fmt.Sprintf(listAPI, page), "https://www.huya.com/l", &j, 0)
	if err != nil {
		core.Infof("  [列表] 第%d页失败: %s", page, core.FmtErr(err, 120))
		return out
	}
	for _, it := range core.JList(core.JMap(j, "data"), "datas") {
		m, _ := it.(map[string]any)
		if m == nil {
			continue
		}
		rid := strings.TrimSpace(core.JStr(m, "profileRoom"))
		if rid == "" {
			continue
		}
		g := strings.TrimSpace(core.JStr(m, "gameFullName"))
		if g == "" {
			g = "huya"
		}
		out[rid] = listMeta{
			nickname: strings.TrimSpace(core.JStr(m, "nick")),
			title:    strings.TrimSpace(core.JStr(m, "roomName")),
			group:    g, avatar: strings.TrimSpace(core.JStr(m, "screenshot")),
		}
	}
	return out
}

func (Platform) Fetch(sources []core.Source, ctx *core.Ctx) ([]core.Room, error) {
	core.Infof("[huya] %d 个来源", len(sources))
	pages := 0
	for _, s := range sources {
		if s.Meta > pages {
			pages = s.Meta
		}
	}
	if pages <= 0 {
		pages = defaultPages
	}
	if ctx.PagesCap > 0 && pages > ctx.PagesCap {
		pages = ctx.PagesCap
	}
	nums := make([]int, pages)
	for i := range nums {
		nums[i] = i + 1
	}
	results := core.Parallel(listWorkers, nums, fetchListPage)
	rooms := map[string]listMeta{}
	for _, m := range results {
		for rid, v := range m {
			if _, ok := rooms[rid]; !ok {
				rooms[rid] = v
			}
		}
	}
	core.Infof("  [列表] %d页共 %d 个不重复房间", pages, len(rooms))
	out := make([]core.Room, 0, len(rooms))
	for rid, m := range rooms {
		out = append(out, core.Room{
			Platform: "huya", RID: rid, Title: m.title, Nickname: m.nickname,
			URL: fmt.Sprintf(playerBase, rid), Group: m.group, Avatar: m.avatar,
		})
	}
	core.Infof("[huya] 完成: %d 在播房间（地址走代理动态解析，未逐个取流）", len(out))
	return out, nil
}

var _ platform.Platform = Platform{}
