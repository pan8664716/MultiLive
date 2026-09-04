// Package migu 咪咕视频 IPTV：官方 tv-data 批量频道列表。
package migu

import (
	"fmt"
	"strings"

	"multilive/internal/core"
	"multilive/internal/platform"
)

const (
	rootID     = "1ff892f2b5ab4a79be6e25b69d2f5d05"
	tvData     = "https://program-sc.miguvideo.com/live/v2/tv-data/%s"
	playerBase = "https://astar.cc.cd/migu/%s"
)

const maxWorkers = 4

var skipCategories = map[string]bool{"热门": true}

type Platform struct{ platform.Base }

func (Platform) Name() string    { return "migu" }
func (Platform) KeepStale() bool { return true }

func (Platform) Parse(line string) ([]core.Source, error) {
	if strings.ToUpper(strings.TrimSpace(line)) != "ALL" {
		return nil, nil
	}
	return []core.Source{{Platform: "migu", Kind: "list", Target: "ALL"}}, nil
}

func fetchBody(sess *core.Client, u string) (map[string]any, error) {
	var j map[string]any
	_, _, err := sess.GetJSON(u, "https://www.miguvideo.com/", &j, 0)
	if err != nil {
		return nil, err
	}
	if b := core.JMap(j, "body"); b != nil {
		return b, nil
	}
	return map[string]any{}, nil
}

func buildRoom(item map[string]any, group string) *core.Room {
	name := strings.TrimSpace(core.JStr(item, "name"))
	pid := strings.TrimSpace(core.JStr(item, "pID"))
	if pid == "" || name == "" {
		return nil
	}
	pics := core.JMap(item, "pics")
	avatar := ""
	if pics != nil {
		avatar = strings.TrimSpace(core.FirstStr(core.JStr(pics, "highResolutionH"), core.JStr(pics, "lowResolutionH")))
	}
	if group == "" {
		group = "migu"
	}
	return &core.Room{
		Platform: "migu", RID: pid, Title: name,
		URL: fmt.Sprintf(playerBase, pid), Group: group, Avatar: avatar,
	}
}

func (Platform) Fetch(sources []core.Source, ctx *core.Ctx) ([]core.Room, error) {
	core.Infof("[migu] %d 个来源", len(sources))
	hasList := false
	for _, s := range sources {
		if s.Kind == "list" {
			hasList = true
		}
	}
	if !hasList {
		return nil, nil
	}
	rootBody, err := fetchBody(core.NewClient(nil), fmt.Sprintf(tvData, rootID))
	if err != nil {
		return nil, err
	}
	var cats []map[string]any
	for _, c := range core.JList(rootBody, "liveList") {
		if m, ok := c.(map[string]any); ok {
			if skipCategories[strings.TrimSpace(core.JStr(m, "name"))] {
				continue
			}
			cats = append(cats, m)
		}
	}
	rootData := core.JList(rootBody, "dataList")
	results := core.Parallel(maxWorkers, cats, func(cat map[string]any) []core.Room {
		voms := strings.TrimSpace(core.JStr(cat, "vomsID"))
		if voms == "" {
			return nil
		}
		var items []any
		if voms == rootID {
			items = rootData
		} else {
			body, err := fetchBody(core.NewClient(nil), fmt.Sprintf(tvData, voms))
			if err != nil {
				core.Infof("  [分类] %s 失败: %s", core.JStr(cat, "name"), core.FmtErr(err, 120))
				return nil
			}
			items = core.JList(body, "dataList")
		}
		gname := strings.TrimSpace(core.JStr(cat, "name"))
		if gname == "" {
			gname = "migu"
		}
		var out []core.Room
		for _, it := range items {
			if m, ok := it.(map[string]any); ok {
				if r := buildRoom(m, gname); r != nil {
					out = append(out, *r)
				}
			}
		}
		return out
	})
	all := map[string]core.Room{}
	for _, list := range results {
		for _, r := range list {
			if _, ok := all[r.RID]; !ok {
				all[r.RID] = r
			}
		}
	}
	out := make([]core.Room, 0, len(all))
	for _, r := range all {
		out = append(out, r)
	}
	core.Infof("  [列表] %d 个分类, %d 个不重复频道", len(cats), len(all))
	core.Infof("[migu] 完成: %d 频道（地址走 Worker 动态解析）", len(out))
	return out, nil
}

var _ platform.Platform = Platform{}
