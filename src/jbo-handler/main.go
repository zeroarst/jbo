package main

import (
	"encoding/json"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

type Config struct {
	WebStorm      string `json:"webstorm"`
	AndroidStudio string `json:"androidstudio"`
	IntelliJ      string `json:"intellij"`
}

func wslToWindows(p string) string {
	// /mnt/d/foo/bar → D:\foo\bar
	if strings.HasPrefix(p, "/mnt/") && len(p) > 5 {
		drive := strings.ToUpper(string(p[5]))
		rest := strings.ReplaceAll(p[6:], "/", `\`)
		return drive + ":" + rest
	}
	return strings.ReplaceAll(p, "/", `\`)
}

func main() {
	if len(os.Args) < 2 {
		os.Exit(1)
	}

	u, err := url.Parse(os.Args[1])
	if err != nil {
		os.Exit(1)
	}

	q := u.Query()
	file := q.Get("file")
	line := q.Get("line")
	ide := q.Get("ide")
	if ide == "" {
		ide = "webstorm"
	}
	if line == "" {
		line = "1"
	}

	winFile := wslToWindows(file)

	localAppData := os.Getenv("LOCALAPPDATA")
	configPath := filepath.Join(localAppData, "jbo", "config.json")
	data, err := os.ReadFile(configPath)
	if err != nil {
		os.Exit(1)
	}

	var cfg Config
	if err := json.Unmarshal(data, &cfg); err != nil {
		os.Exit(1)
	}

	exeMap := map[string]string{
		"webstorm":      cfg.WebStorm,
		"androidstudio": cfg.AndroidStudio,
		"intellij":      cfg.IntelliJ,
	}
	exe := exeMap[ide]
	if exe == "" {
		exe = cfg.WebStorm
	}
	if exe == "" {
		os.Exit(1)
	}

	cmd := exec.Command(exe, "--line", line, winFile)
	_ = cmd.Start()
}
