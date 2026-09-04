// Package douyu 斗鱼直播：目录接口纯 HTTP 列表。
package douyu

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"

	"multilive/internal/core"
	"multilive/internal/platform"
)

const (
	dirAPI     = "https://www.douyu.com/gapi/rkc/directory/0_0/%d"
	playerBase = "https://astar.cc.cd/douyu/%s"
)

const (
	defaultPages = 10
	maxPages     = 65
	listWorkers  = 5
)

var dirRe = regexp.MustCompile(`^https?://www\.douyu\.com/directory/all(:\d+)?`)

type Platform struct{ platform.Base }

func (Platform) Name() string    { return "douyu" }
func (Platform) KeepStale() bool { return false }

func (Platform) Parse(line string) ([]core.Source, error) {
	m := dirRe.FindStringSubmatch(strings.TrimSpace(line))
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
	return []core.Source{{Platform: "douyu", Kind: "all", Target: "ALL", Meta: pages}}, nil
}

type dirMeta struct {
	rid, nickname, title, group, avatar string
}

func fetchDirPage(page int) map[string]dirMeta {
	out := map[string]dirMeta{}
	sess := core.NewClient(nil)
	var j map[string]any
	_, _, err := sess.GetJSON(fmt.Sprintf(dirAPI, page), "https://www.douyu.com/directory/all", &j, 0)
	if err != nil {
		core.Infof("  [目录] 第%d页失败: %s", page, core.FmtErr(err, 120))
		return out
	}
	for _, it := range core.JList(core.JMap(j, "data"), "rl") {
		m, _ := it.(map[string]any)
		if m == nil {
			continue
		}
		rid := strings.TrimSpace(core.JStr(m, "rid"))
		if rid == "" {
			continue
		}
		g := strings.TrimSpace(core.JStr(m, "c2name"))
		if g == "" {
			g = "douyu"
		}
		out[rid] = dirMeta{
			rid: rid, nickname: strings.TrimSpace(core.JStr(m, "nn")),
			title: strings.TrimSpace(core.JStr(m, "rn")), group: g,
			avatar: core.FirstStr(core.JStr(m, "rs16"), core.JStr(m, "rs1")),
		}
	}
	return out
}

func (Platform) Fetch(sources []core.Source, ctx *core.Ctx) ([]core.Room, error) {
	core.Infof("[douyu] %d 个来源", len(sources))
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
	results := core.Parallel(listWorkers, nums, fetchDirPage)
	rooms := map[string]dirMeta{}
	for _, m := range results {
		for rid, v := range m {
			if _, ok := rooms[rid]; !ok {
				rooms[rid] = v
			}
		}
	}
	core.Infof("  [目录] %d页共 %d 个不重复房间", pages, len(rooms))
	out := make([]core.Room, 0, len(rooms))
	for rid, m := range rooms {
		out = append(out, core.Room{
			Platform: "douyu", RID: rid, Title: m.title, Nickname: m.nickname,
			URL: fmt.Sprintf(playerBase, rid), Group: m.group, Avatar: m.avatar,
		})
	}
	core.Infof("[douyu] 完成: %d 在播房间（地址走 Worker 动态解析，未逐个取流）", len(out))
	return out, nil
}

var _ platform.Platform = Platform{}
