// Package tiktok TikTok LIVE：浏览器签名一次 -> 纯 HTTP 批量重放。
package tiktok

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"strings"
	"time"

	"multilive/internal/core"
	"multilive/internal/platform"
)

const (
	playerBase  = "https://astar.cc.cd/tiktok/%s"
	browserTO   = 180 * time.Second
	browserFile = "browser_fetch_tiktok.mjs"
)

var liveRe = regexp.MustCompile(`^https?://(?:www\.|m\.)?tiktok\.com/live`)

type Platform struct{ platform.Base }

func (Platform) Name() string    { return "tiktok" }
func (Platform) KeepStale() bool { return false }

func (Platform) Parse(line string) ([]core.Source, error) {
	t := strings.TrimSpace(line)
	if liveRe.MatchString(t) {
		if i := strings.Index(t, "?"); i >= 0 {
			t = t[:i]
		}
		return []core.Source{{Platform: "tiktok", Kind: "page", Target: t}}, nil
	}
	if strings.ToUpper(t) == "LIVE" {
		return []core.Source{{Platform: "tiktok", Kind: "page", Target: "https://www.tiktok.com/live"}}, nil
	}
	return nil, nil
}

type signData struct {
	Cookies    map[string]string `json:"cookies"`
	SignedURLs []string          `json:"signedUrls"`
}

func pick(v any) string {
	if s, ok := v.(string); ok {
		return strings.TrimSpace(s)
	}
	return ""
}

func avatarOf(obj map[string]any) string {
	if obj == nil {
		return ""
	}
	for _, k := range []string{"url_list", "urlList", "image_urls"} {
		if lst, ok := obj[k].([]any); ok && len(lst) > 0 {
			if s, ok := lst[0].(string); ok && s != "" {
				return s
			}
		}
	}
	return ""
}

func browserSign(urls []string, projectRoot string) (*signData, error) {
	script := projectRoot + "/tools/" + browserFile
	if _, err := os.Stat(script); err != nil {
		return nil, fmt.Errorf("缺少 %s", browserFile)
	}
	if _, err := exec.LookPath("node"); err != nil {
		return nil, fmt.Errorf("缺少 Node")
	}
	ctx, cancel := contextWithTimeout(browserTO)
	defer cancel()
	cmd := exec.CommandContext(ctx, "node", append([]string{script}, urls...)...)
	out, err := cmd.Output()
	if err != nil {
		msg := ""
		if ee, ok := err.(*exec.ExitError); ok {
			msg = string(ee.Stderr)
			if len(msg) > 200 {
				msg = msg[len(msg)-200:]
			}
		}
		return nil, fmt.Errorf("浏览器签名失败: %s", msg)
	}
	var data signData
	if err := json.Unmarshal([]byte(strings.TrimSpace(string(out))), &data); err != nil {
		return nil, fmt.Errorf("解析浏览器输出失败: %s", core.FmtErr(err, 120))
	}
	return &data, nil
}

func (Platform) Fetch(sources []core.Source, ctx *core.Ctx) ([]core.Room, error) {
	core.Infof("[tiktok] %d 个来源", len(sources))
	set := map[string]bool{}
	for _, s := range sources {
		u := "https://www.tiktok.com/live"
		if s.Kind == "page" && s.Target != "" {
			u = s.Target
		}
		set[u] = true
	}
	var urls []string
	for u := range set {
		urls = append(urls, u)
	}
	// 排序保证确定性（对标 Python sorted）。
	for i := 1; i < len(urls); i++ {
		for j := i; j > 0 && urls[j] < urls[j-1]; j-- {
			urls[j], urls[j-1] = urls[j-1], urls[j]
		}
	}
	signed, err := browserSign(urls, ctx.ProjectRoot)
	if err != nil || signed == nil || len(signed.SignedURLs) == 0 {
		core.Warnf("[tiktok] 未获取到签名 URL")
		return nil, nil
	}
	core.Infof("[tiktok] %d 个签名 URL, %d 个 cookies", len(signed.SignedURLs), len(signed.Cookies))
	var cookieParts []string
	for _, k := range []string{"ttwid", "tt_csrf_token", "tt_chain_token", "msToken"} {
		if v, ok := signed.Cookies[k]; ok && v != "" {
			cookieParts = append(cookieParts, k+"="+v)
		}
	}
	cookieStr := strings.Join(cookieParts, "; ")
	type roomMeta struct {
		rid, title, nickname, avatar string
	}
	all := map[string]roomMeta{}
	for i, u := range signed.SignedURLs {
		sess := core.NewClient(map[string]string{
			"Referer": "https://www.tiktok.com/",
			"Cookie":  cookieStr,
		})
		var data map[string]any
		st, _, err := sess.GetJSON(u, "", &data, 20*time.Second)
		if err != nil {
			core.Warnf("[tiktok] HTTP[%d/%d] 失败: %s", i+1, len(signed.SignedURLs), core.FmtErr(err, 120))
			continue
		}
		raw, ok := data["data"].([]any)
		if !ok {
			continue
		}
		for _, item := range raw {
			im, _ := item.(map[string]any)
			if im == nil {
				continue
			}
			inner := core.JMap(im, "data")
			if inner == nil {
				inner = im
			}
			owner := core.JMap(inner, "owner")
			if owner == nil {
				continue
			}
			rid := core.FirstStr(pick(owner["display_id"]), pick(owner["unique_id"]))
			if rid == "" {
				continue
			}
			if _, ok := all[rid]; ok {
				continue
			}
			all[rid] = roomMeta{
				rid: rid, title: pick(inner["title"]), nickname: pick(owner["nickname"]),
				avatar: core.FirstStr(
					avatarOf(core.JMap(owner, "avatar_thumb")),
					avatarOf(core.JMap(owner, "avatarThumb")),
					avatarOf(core.JMap(owner, "avatarLarger")),
					avatarOf(core.JMap(owner, "avatar_medium")),
					avatarOf(core.JMap(owner, "avatar"))),
			}
		}
		core.Infof("[tiktok] HTTP[%d/%d] status=%d ok", i+1, len(signed.SignedURLs), st)
	}
	core.Infof("[tiktok] 共 %d 个不重复房间", len(all))
	var out []core.Room
	for rid, r := range all {
		out = append(out, core.Room{
			Platform: "tiktok", RID: rid, Title: r.title, Nickname: r.nickname,
			URL: fmt.Sprintf(playerBase, rid), Group: "tiktok", Avatar: r.avatar,
		})
	}
	core.Infof("[tiktok] 完成: %d 在播房间", len(out))
	return out, nil
}

func contextWithTimeout(d time.Duration) (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.Background(), d)
}

var _ platform.Platform = Platform{}
