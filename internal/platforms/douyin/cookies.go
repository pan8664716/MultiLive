package douyin

import (
	"context"
	"net/http"
	"net/url"
	"time"

	"multilive/internal/core"
)

func contextWithTimeout(d time.Duration) (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.Background(), d)
}

type httpCookie = http.Cookie

var cookieURL, _ = url.Parse("https://live.douyin.com/")

func exportCookies(sess *core.Client) []*httpCookie {
	if sess == nil || sess.HTTP == nil || sess.HTTP.Jar == nil {
		return nil
	}
	return sess.HTTP.Jar.Cookies(cookieURL)
}

func importCookies(sess *core.Client, cookies []*httpCookie) {
	if sess == nil || sess.HTTP == nil || sess.HTTP.Jar == nil || len(cookies) == 0 {
		return
	}
	sess.HTTP.Jar.SetCookies(cookieURL, cookies)
}
