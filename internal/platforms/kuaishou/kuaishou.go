// Package kuaishou 快手直播：live_api/hot/list 纯 HTTP 热门列表。
package kuaishou

import (
	"fmt"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"multilive/internal/core"
	"multilive/internal/platform"
)

const (
	hotAPI     = "https://live.kuaishou.com/live_api/hot/list"
	playerBase = "https://astar.cc.cd/kuaishou/%s"
)

const (
	defaultPages = 50
	maxPages     = 50
	pageWorkers  = 5
)

var nameRe = regexp.MustCompile(`^[A-Z0-9]+$`)

type Platform struct{ platform.Base }

func (Platform) Name() string    { return "kuaishou" }
func (Platform) KeepStale() bool { return true }

func (Platform) FallbackURL(r core.Room) string { return fmt.Sprintf(playerBase, r.RID) }

func (Platform) Parse(line string) ([]core.Source, error) {
	t := strings.TrimSpace(line)
	if t == "" {
		return nil, nil
	}
	name := t
	pages := defaultPages
	if idx := strings.LastIndex(strings.Split(t, "/")[len(strings.Split(t, "/"))-1], ":"); idx >= 0 {
		last := t[strings.LastIndex(t, ":")+1:]
		if n, err := strconv.Atoi(last); err == nil {
			pages = n
			name = t[:strings.LastIndex(t, ":")]
		}
	}
	if i := strings.LastIndex(name, "/live/"); i >= 0 {
		name = name[i+len("/live/"):]
	}
	name = strings.TrimSuffix(strings.ToUpper(name), "/")
	name = strings.TrimSpace(name)
	if !nameRe.MatchString(name) {
		return nil, nil
	}
	if pages < 1 {
		pages = 1
	}
	if pages > maxPages {
		pages = maxPages
	}
	return []core.Source{{Platform: "kuaishou", Kind: "list", Target: name, Meta: pages}}, nil
}

func fetchPage(source string, page int) []map[string]any {
	sess := core.NewClient(nil)
	qs := url.Values{}
	qs.Set("type", source)
	qs.Set("filterType", "0")
	qs.Set("page", strconv.Itoa(page))
	qs.Set("pageSize", "24")
	var j map[string]any
	_, _, err := sess.GetJSON(hotAPI+"?"+qs.Encode(), "https://live.kuaishou.com/live/"+source, &j, 20*time.Second)
	if err != nil {
		core.Infof("  [%s] 第%d页失败: %s", source, page, core.FmtErr(err, 120))
		return nil
	}
	var out []map[string]any
	for _, it := range core.JList(core.JMap(j, "data"), "list") {
		if m, ok := it.(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out
}

// hasPlayURL 列表自带 playUrls 是否有可用直链（仅作在播判断，不直接写直链）。
func hasPlayURL(room map[string]any) bool {
	type rep struct {
		level   float64
		bitrate float64
		url     string
	}
	var reps []rep
	if pus, ok := room["playUrls"].([]any); ok {
		for _, pu := range pus {
			pum, _ := pu.(map[string]any)
			if pum == nil {
				continue
			}
			as, _ := core.JMap(pum, "adaptationSet")["representation"].([]any)
			for _, r := range as {
				rm, _ := r.(map[string]any)
				if rm == nil {
					continue
				}
				u, _ := rm["url"].(string)
				if u == "" {
					continue
				}
				lv, _ := rm["level"].(float64)
				br, _ := rm["bitrate"].(float64)
				reps = append(reps, rep{lv, br, u})
			}
		}
	}
	if len(reps) == 0 {
		return false
	}
	sort.Slice(reps, func(i, j int) bool {
		if reps[i].level != reps[j].level {
			return reps[i].level < reps[j].level
		}
		return reps[i].bitrate < reps[j].bitrate
	})
	return reps[len(reps)-1].url != ""
}

func fetchSource(source string, pages int) map[string]map[string]any {
	rooms := map[string]map[string]any{}
	var mu sync.Mutex
	nums := make([]int, pages)
	for i := range nums {
		nums[i] = i + 1
	}
	results := core.Parallel(pageWorkers, nums, func(p int) []map[string]any {
		return fetchPage(source, p)
	})
	for _, list := range results {
		for _, room := range list {
			lid := core.JStr(room, "id")
			if lid == "" {
				continue
			}
			mu.Lock()
			if _, ok := rooms[lid]; !ok {
				rooms[lid] = room
			}
			mu.Unlock()
		}
	}
	core.Infof("  [%s] %d页抓完: %d 个不重复房间", source, pages, len(rooms))
	return rooms
}

func (Platform) Fetch(sources []core.Source, ctx *core.Ctx) ([]core.Room, error) {
	core.Infof("[kuaishou] %d 个来源", len(sources))
	type req struct {
		target string
		pages  int
	}
	var reqs []req
	for _, s := range sources {
		pages := s.Meta
		if pages <= 0 {
			pages = defaultPages
		}
		if pages > maxPages {
			pages = maxPages
		}
		if ctx.PagesCap > 0 && pages > ctx.PagesCap {
			pages = ctx.PagesCap
		}
		reqs = append(reqs, req{s.Target, pages})
	}
	results := core.Parallel(5, reqs, func(r req) map[string]map[string]any {
		return fetchSource(r.target, r.pages)
	})
	all := map[string]map[string]any{}
	for _, m := range results {
		for lid, room := range m {
			if _, ok := all[lid]; !ok {
				all[lid] = room
			}
		}
	}
	var out []core.Room
	seen := map[string]bool{}
	for _, room := range all {
		author := core.JMap(room, "author")
		rid := core.JStr(author, "id")
		if rid == "" || seen[rid] {
			continue
		}
		seen[rid] = true
		if !hasPlayURL(room) {
			continue
		}
		game := strings.TrimSpace(core.JStr(core.JMap(room, "gameInfo"), "name"))
		if game == "" {
			game = "kuaishou"
		}
		out = append(out, core.Room{
			Platform: "kuaishou", RID: rid,
			Title:    strings.TrimSpace(core.JStr(room, "caption")),
			Nickname: strings.TrimSpace(core.JStr(author, "name")),
			URL:      fmt.Sprintf(playerBase, rid),
			Group:    game, Avatar: core.JStr(room, "cover"),
		})
	}
	core.Infof("[kuaishou] 完成: %d 房间（无直链/无 author.id 已跳过）", len(out))
	return out, nil
}

var _ platform.Platform = Platform{}
