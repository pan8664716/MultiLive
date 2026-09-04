// Package fourgtv 4gtv 台湾直播：内置静态频道清单。
package fourgtv

import (
	"embed"
	"fmt"
	"strings"

	"multilive/internal/core"
	"multilive/internal/platform"
)

//go:embed channels.tsv
var channelsFS embed.FS

const playerBase = "https://astar.cc.cd/4gtv/%s"

type Platform struct{ platform.Base }

func (Platform) Name() string    { return "4gtv" }
func (Platform) KeepStale() bool { return true }

func (Platform) Parse(line string) ([]core.Source, error) {
	if strings.ToUpper(strings.TrimSpace(line)) != "ALL" {
		return nil, nil
	}
	return []core.Source{{Platform: "4gtv", Kind: "list", Target: "ALL"}}, nil
}

func (Platform) Fetch(sources []core.Source, ctx *core.Ctx) ([]core.Room, error) {
	core.Infof("[4gtv] %d 个来源", len(sources))
	hasList := false
	for _, s := range sources {
		if s.Kind == "list" {
			hasList = true
		}
	}
	if !hasList {
		return nil, nil
	}
	raw, err := channelsFS.ReadFile("channels.tsv")
	if err != nil {
		return nil, err
	}
	var out []core.Room
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		parts := strings.Split(line, "\t")
		if len(parts) < 2 {
			continue
		}
		cid, name := strings.TrimSpace(parts[0]), strings.TrimSpace(parts[1])
		group := ""
		if len(parts) > 2 {
			group = strings.TrimSpace(parts[2])
		}
		if group == "" {
			group = "4gtv"
		}
		if cid == "" {
			continue
		}
		out = append(out, core.Room{
			Platform: "4gtv", RID: cid, Title: name,
			URL: fmt.Sprintf(playerBase, cid), Group: group,
		})
	}
	core.Infof("[4gtv] 完成: %d 频道（内置清单，地址走 Worker 动态解析）", len(out))
	return out, nil
}

var _ platform.Platform = Platform{}
