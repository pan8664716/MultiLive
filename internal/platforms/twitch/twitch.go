// Package twitch Twitch 直播：匿名 GQL 批量列表。
package twitch

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"

	"multilive/internal/core"
	"multilive/internal/platform"
)

const (
	gqlURL     = "https://gql.twitch.tv/gql"
	clientID   = "b31o4btkqth5bzbvr9ub2ovr79umhh"
	playerBase = "https://astar.cc.cd/twitch/%s"
	pageSize   = 30
)

const (
	defaultPages = 40
	maxPages     = 120
	pageSleep    = 500 * time.Millisecond
)

const query = `query Streams($first: Int, $after: Cursor) {
  streams(first: $first, after: $after) {
    edges { node { id title viewersCount type game { name }
                   broadcaster { id login displayName } previewImageURL } }
    pageInfo { hasNextPage endCursor }
  }
}`

var (
	dirRe = regexp.MustCompile(`^https?://www\.twitch\.tv/directory(:\d+)?`)
	allRe = regexp.MustCompile(`^ALL(:\d+)?$`)
)

type Platform struct{ platform.Base }

func (Platform) Name() string    { return "twitch" }
func (Platform) KeepStale() bool { return false }

func pagesOf(suffix string) int {
	pages := defaultPages
	if suffix != "" {
		if n, err := strconv.Atoi(suffix[1:]); err == nil {
			pages = n
		}
	}
	if pages < 1 {
		pages = 1
	}
	if pages > maxPages {
		pages = maxPages
	}
	return pages
}

func (Platform) Parse(line string) ([]core.Source, error) {
	t := strings.TrimSpace(line)
	if m := dirRe.FindStringSubmatch(t); m != nil {
		return []core.Source{{Platform: "twitch", Kind: "list", Target: "ALL", Meta: pagesOf(m[1])}}, nil
	}
	if m := allRe.FindStringSubmatch(strings.ToUpper(t)); m != nil {
		return []core.Source{{Platform: "twitch", Kind: "list", Target: "ALL", Meta: pagesOf(m[1])}}, nil
	}
	return nil, nil
}

type twMeta struct {
	title, nickname, group, avatar string
}

func fetchPage(sess *core.Client, after any) (map[string]any, error) {
	vars := map[string]any{"first": pageSize, "after": after}
	payload := map[string]any{"query": query, "variables": vars}
	var j map[string]any
	var last error
	for attempt := 0; attempt < 3; attempt++ {
		j = nil
		_, _, err := sess.PostJSON(gqlURL, payload, &j,
			"https://www.twitch.tv/directory", "", 45*time.Second,
			map[string]string{"Client-Id": clientID})
		if err == nil {
			if sm := core.JMap(core.JMap(j, "data"), "streams"); sm != nil {
				return sm, nil
			}
			return map[string]any{}, nil
		}
		last = err
		if attempt < 2 {
			time.Sleep([]time.Duration{2 * time.Second, 6 * time.Second}[attempt])
		}
	}
	return nil, last
}

func (Platform) Fetch(sources []core.Source, ctx *core.Ctx) ([]core.Room, error) {
	core.Infof("[twitch] %d 个来源", len(sources))
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
	sess := core.NewClient(nil)
	rooms := map[string]twMeta{}
	var after any
	for page := 1; page <= pages; page++ {
		data, err := fetchPage(sess, after)
		if err != nil {
			core.Infof("  [列表] 第%d页失败: %s", page, core.FmtErr(err, 120))
			break
		}
		edges, _ := data["edges"].([]any)
		for _, e := range edges {
			em, _ := e.(map[string]any)
			if em == nil {
				continue
			}
			node, _ := em["node"].(map[string]any)
			if node == nil {
				continue
			}
			bc, _ := node["broadcaster"].(map[string]any)
			if bc == nil {
				continue
			}
			login := strings.ToLower(strings.TrimSpace(core.JStr(bc, "login")))
			if login == "" {
				continue
			}
			if _, ok := rooms[login]; ok {
				continue
			}
			game, _ := node["game"].(map[string]any)
			avatar := strings.ReplaceAll(strings.TrimSpace(core.JStr(node, "previewImageURL")), "{width}x{height}", "320x180")
			nick := strings.TrimSpace(core.JStr(bc, "displayName"))
			if nick == "" {
				nick = login
			}
			g := ""
			if game != nil {
				g = strings.TrimSpace(core.JStr(game, "name"))
			}
			if g == "" {
				g = "twitch"
			}
			rooms[login] = twMeta{
				title:    strings.TrimSpace(core.JStr(node, "title")),
				nickname: nick, group: g, avatar: avatar,
			}
		}
		pi, _ := data["pageInfo"].(map[string]any)
		if len(edges) == 0 || pi == nil {
			break
		}
		hasNext, _ := pi["hasNextPage"].(bool)
		if !hasNext {
			break
		}
		after = pi["endCursor"]
		core.Infof("  [列表] 第%d页: %d 个, 累计 %d", page, len(edges), len(rooms))
		time.Sleep(pageSleep)
	}
	core.Infof("  [列表] %d页共 %d 个不重复直播间", pages, len(rooms))
	out := make([]core.Room, 0, len(rooms))
	for login, m := range rooms {
		out = append(out, core.Room{
			Platform: "twitch", RID: login, Title: m.title, Nickname: m.nickname,
			URL: fmt.Sprintf(playerBase, login), Group: m.group, Avatar: m.avatar,
		})
	}
	core.Infof("[twitch] 完成: %d 在播直播间（地址走 Worker 动态解析）", len(out))
	return out, nil
}

var _ platform.Platform = Platform{}
