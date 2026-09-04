package core

import (
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
)

var (
	std     = log.New(os.Stdout, "", log.LstdFlags)
	verbose = false
)

// SetupLogging 初始化控制台 + 滚动文件日志（run.log 超 1MB 时轮转，保留 3 份）。
func SetupLogging(v bool, logPath string) {
	verbose = v
	writers := []io.Writer{os.Stdout}
	if logPath != "" {
		if err := os.MkdirAll(filepath.Dir(logPath), 0o755); err == nil {
			rotateLogs(logPath)
			if f, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644); err == nil {
				writers = append(writers, f)
			}
		}
	}
	std = log.New(io.MultiWriter(writers...), "", log.LstdFlags)
}

func rotateLogs(path string) {
	st, err := os.Stat(path)
	if err != nil || st.Size() < 1_000_000 {
		return
	}
	os.Remove(path + ".3")
	os.Rename(path+".2", path+".3")
	os.Rename(path+".1", path+".2")
	os.Rename(path, path+".1")
}

func Infof(format string, args ...any) {
	std.Output(2, "[INFO] "+fmt.Sprintf(format, args...))
}

func Warnf(format string, args ...any) {
	std.Output(2, "[WARN] "+fmt.Sprintf(format, args...))
}

func Errorf(format string, args ...any) {
	std.Output(2, "[ERROR] "+fmt.Sprintf(format, args...))
}

func Debugf(format string, args ...any) {
	if verbose {
		std.Output(2, "[DEBUG] "+fmt.Sprintf(format, args...))
	}
}

// FmtErr 截断错误文本（排查日志用）。
func FmtErr(err error, limit int) string {
	if err == nil {
		return ""
	}
	s := err.Error()
	if len(s) == 0 {
		return fmt.Sprintf("%T", err)
	}
	runes := []rune(s)
	if len(runes) > limit {
		return string(runes[:limit]) + "..."
	}
	return s
}
