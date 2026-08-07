package persistence

import (
	"database/sql"
	"fmt"
	"os"
	"sync"
	"time"

	_ "github.com/go-sql-driver/mysql"
)

var (
	dbOnce sync.Once
	dbInst *sql.DB
	dbErr  error
)

// DB returns the shared MySQL handle. Reads DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME —
// same env vars as lea-server/lea-config so一套配置多进程共享。
//
// Returns nil, nil when persistence is intentionally disabled (LEA_AGENT_DB=off) —
// callers should skip writes silently in that case.
func DB() (*sql.DB, error) {
	dbOnce.Do(func() {
		if os.Getenv("LEA_AGENT_DB") == "off" {
			return
		}
		host := envOr("DB_HOST", "localhost")
		port := envOr("DB_PORT", "3306")
		user := envOr("DB_USER", "root")
		pass := os.Getenv("DB_PASSWORD")
		name := envOr("DB_NAME", "lea-platform-test")

		dsn := fmt.Sprintf(
			"%s:%s@tcp(%s:%s)/%s?charset=utf8mb4&parseTime=true&loc=Local",
			user, pass, host, port, name,
		)
		db, err := sql.Open("mysql", dsn)
		if err != nil {
			dbErr = fmt.Errorf("open mysql: %w", err)
			return
		}
		db.SetMaxOpenConns(20)
		db.SetMaxIdleConns(5)
		db.SetConnMaxLifetime(30 * time.Minute)
		if err := db.Ping(); err != nil {
			dbErr = fmt.Errorf("ping mysql: %w", err)
			_ = db.Close()
			return
		}
		dbInst = db
	})
	return dbInst, dbErr
}

func envOr(k, fallback string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return fallback
}
