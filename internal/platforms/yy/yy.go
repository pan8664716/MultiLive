// Package yy YY 直播：频道页 SSR 第一页 + /more/page.action 分页补齐。
package yy

import (
	"fmt"
	"regexp"
	"sort"
	"strings"

	"multilive/internal/core"
	"multilive/internal/platform"
)

const playerBase = "https://astar.cc.cd/yy/%s"
const catAPI = "https://www.yy.com/more/page.action"
const pageSize = 200

var (
	catRe   = regexp.MustCompile(`^https?://www\.yy\.com/([a-zA-Z0-9_]+)/?$`)
	liRe    = regexp.MustCompile(`data-url="/(\d+)/(\d+)\?[^"]*?"[^>]*?data-title="([^"]*)"`)
	nickRe  = regexp.MustCompile(`<span class="intro">([^<]*)</span>`)
	avRe    = regexp.MustCompile(`data-original="([^"]+)"`)
	statRe  = regexp.MustCompile(`data-stat-name="([^"]+)"`)
	barRe   = regexp.MustCompile(`pageBar\s*:\s*\{([^}]*)\}`)
	numRe   = func(key string) *regexp.Regexp { return regexp.MustCompile(key + `\s*:\s*(\d+)`) }
	strRe   = func(key string) *regexp.Regexp { return regexp.MustCompile(key + `\s*:\s*'([^']*)'`) }
	titleRe = regexp.MustCompile(`\s*正在直播$`)
)

var groupMap = map[string]string{
	"dancing": "舞蹈", "pretty": "颜值", "music": "音乐", "sing": "音乐",
}

type meta struct {
	sid, ssid, title, nickname, group, avatar string
}

type Platform struct{ platform.Base }

func (Platform) Name() string    { return "yy" }
func (Platform) KeepStale() bool { return false }

func (Platform) Parse(line string) ([]core.Source, error) {
	if m := catRe.FindStringSubmatch(strings.TrimSpace(line)); m != nil {
		return []core.Source{{Platform: "yy", Kind: "category", Target: m[1]}}, nil
	}
	return nil, nil
}

func grabNum(bar, key string) int {
	m := numRe(key).FindStringSubmatch(bar)
	if m == nil {
		return 0
	}
	n := 0
	fmt.Sscanf(m[1], "%d", &n)
	return n
}

func grabStr(bar, key string) string {
	m := strRe(key).FindStringSubmatch(bar)
	if m == nil {
		return ""
	}
	return m[1]
}

func fetchCategory(cat string) map[string]meta {
	rooms := map[string]meta{}
	sess := core.NewClient(nil)
	_, body, _, err := sess.GetText("https://www.yy.com/"+cat, "https://www.yy.com/")
	if err != nil {
		core.Infof("  [频道] %s 页面失败: %s", cat, core.FmtErr(err, 120))
		return rooms
	}
	group := groupMap[cat]
	if group == "" {
		if m := statRe.FindStringSubmatch(body); m != nil {
			group = strings.TrimSpace(m[1])
		}
		if group == "" {
			group = cat
		}
	}
	for _, block := range strings.Split(body, "<li") {
		m := liRe.FindStringSubmatch(block)
		if m == nil {
			continue
		}
		sid, ssid, title := m[1], m[2], titleRe.ReplaceAllString(strings.TrimSpace(m[3]), "")
		nick := ""
		if nm := nickRe.FindStringSubmatch(block); nm != nil {
			nick = strings.TrimSpace(nm[1])
		}
		avatar := ""
		if am := avRe.FindStringSubmatch(block); am != nil {
			avatar = strings.TrimSpace(am[1])
		}
		if nick == "" {
			nick = title
		}
		rooms[sid] = meta{sid: sid, ssid: ssid, title: title, nickname: nick, group: group, avatar: avatar}
	}
	if pm := barRe.FindStringSubmatch(body); pm != nil {
		bar := pm[1]
		moduleID := grabNum(bar, "moduleId")
		total := grabNum(bar, "totalCount")
		biz := grabStr(bar, "biz")
		sub := grabStr(bar, "subBiz")
		if biz != "" && moduleID != 0 && total != 0 {
			pages := (total + pageSize - 1) / pageSize
			for page := 1; page <= pages; page++ {
				api := fmt.Sprintf("%s?biz=%s&subBiz=%s&page=%d&moduleId=%d&pageSize=%d",
					catAPI, biz, sub, page, moduleID, pageSize)
				var j map[string]any
				_, _, err := sess.GetJSON(api, "https://www.yy.com/"+cat, &j, 0)
				if err != nil {
					core.Infof("  [频道] %s 第%d页失败: %s", cat, page, core.FmtErr(err, 120))
					continue
				}
				var items []any
				if dm := core.JMap(j, "data"); dm != nil {
					if lst, ok := dm["data"].([]any); ok {
						items = lst
					}
				}
				for _, it := range items {
					mm, _ := it.(map[string]any)
					if mm == nil {
						continue
					}
					sid := core.JStr(mm, "sid")
					if sid == "" {
						continue
					}
					if _, ok := rooms[sid]; ok {
						continue
					}
					name := strings.TrimSpace(core.JStr(mm, "name"))
					desc := strings.TrimSpace(core.JStr(mm, "desc"))
					rooms[sid] = meta{
						sid: sid, ssid: core.JStr(mm, "ssid", "sid"),
						title:    titleRe.ReplaceAllString(desc, ""),
						nickname: core.FirstStr(name, desc),
						group:    group,
						avatar:   strings.TrimSpace(core.JStr(mm, "thumb2", "avatar")),
					}
				}
			}
		}
	}
	core.Infof("  [频道] %s: %d 个在播房间", cat, len(rooms))
	return rooms
}

func (Platform) Fetch(sources []core.Source, ctx *core.Ctx) ([]core.Room, error) {
	core.Infof("[yy] %d 个来源", len(sources))
	set := map[string]bool{}
	for _, s := range sources {
		if s.Kind == "category" {
			set[s.Target] = true
		}
	}
	cats := make([]string, 0, len(set))
	for c := range set {
		cats = append(cats, c)
	}
	sort.Strings(cats)
	type res struct {
		m map[string]meta
	}
	results := core.Parallel(3, cats, func(c string) map[string]meta { return fetchCategory(c) })
	merged := map[string]meta{}
	for _, m := range results {
		for sid, v := range m {
			if _, ok := merged[sid]; !ok {
				merged[sid] = v
			}
		}
	}
	core.Infof("  [列表] 共 %d 个不重复房间", len(merged))
	out := make([]core.Room, 0, len(merged))
	for sid, m := range merged {
		g := m.group
		if g == "" {
			g = "yy"
		}
		out = append(out, core.Room{
			Platform: "yy", RID: sid, Title: m.title, Nickname: m.nickname,
			URL: fmt.Sprintf(playerBase, sid), Group: g, Avatar: m.avatar,
		})
	}
	core.Infof("[yy] 完成: %d 在播房间（地址走 Worker 动态解析，未逐个取流）", len(out))
	return out, nil
}

var _ platform.Platform = Platform{}
