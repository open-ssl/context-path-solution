package config

import (
	"os"
	"strconv"
)

// Config holds the streamer's runtime settings, read from the environment.
type Config struct {
	NATSURL string
	DataDir string
	TickMS  int
}

func Load() Config {
	return Config{
		// Composed from atomic parts, mirroring the Python shared config's
		// nats_url computed field (nats://{host}:{port}).
		NATSURL: "nats://" + getenv("NATS_HOST", "nats") + ":" + getenv("NATS_PORT", "4222"),
		DataDir: getenv("DATA_DIR", "/data"),
		TickMS:  getenvInt("TICK_MS", 25),
	}
}

func getenv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func getenvInt(key string, def int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}
