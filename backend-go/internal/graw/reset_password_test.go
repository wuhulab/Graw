package graw

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeResetTestUsers(t *testing.T, dir string) {
	t.Helper()
	hash, err := HashPassword("OldPass#1")
	if err != nil {
		t.Fatalf("HashPassword: %v", err)
	}
	users := Users{
		"admin": User{
			"username": "admin", "password": hash, "role": "admin",
			"must_change_password": true, "token_version": 2,
		},
	}
	if err := WriteJSONFile(UsersFilePath(dir), users); err != nil {
		t.Fatalf("WriteJSONFile: %v", err)
	}
}

func TestResetUserPasswordInDir(t *testing.T) {
	dir := t.TempDir()
	writeResetTestUsers(t, dir)

	if err := ResetUserPasswordInDir(dir, "admin", "NewPass#2026"); err != nil {
		t.Fatalf("ResetUserPasswordInDir: %v", err)
	}
	users, err := LoadUsersFromDir(dir)
	if err != nil {
		t.Fatalf("LoadUsersFromDir: %v", err)
	}
	u := users["admin"]
	if u == nil {
		t.Fatal("admin 账号丢失")
	}
	if !VerifyPassword("NewPass#2026", u.PasswordHash()) {
		t.Fatal("新密码校验失败")
	}
	if VerifyPassword("OldPass#1", u.PasswordHash()) {
		t.Fatal("旧密码仍然有效")
	}
	if u.Bool("must_change_password") {
		t.Fatal("must_change_password 应被清除")
	}
	if u.Int("token_version") != 2 {
		t.Fatalf("token_version 不应变化，got %d", u.Int("token_version"))
	}
	raw, err := os.ReadFile(UsersFilePath(dir))
	if err != nil {
		t.Fatalf("读取 users.json: %v", err)
	}
	if strings.Contains(string(raw), "NewPass#2026") {
		t.Fatal("users.json 不应保存明文密码")
	}
}

func TestResetUserPasswordErrors(t *testing.T) {
	t.Run("missing users file", func(t *testing.T) {
		err := ResetUserPasswordInDir(t.TempDir(), "admin", "NewPass#2026")
		if !errors.Is(err, ErrUsersFileMissing) {
			t.Fatalf("want ErrUsersFileMissing, got %v", err)
		}
	})

	t.Run("unknown user", func(t *testing.T) {
		dir := t.TempDir()
		writeResetTestUsers(t, dir)
		err := ResetUserPasswordInDir(dir, "nobody", "NewPass#2026")
		if !errors.Is(err, ErrUserNotFound) {
			t.Fatalf("want ErrUserNotFound, got %v", err)
		}
	})

	t.Run("short password", func(t *testing.T) {
		dir := t.TempDir()
		writeResetTestUsers(t, dir)
		err := ResetUserPasswordInDir(dir, "admin", "12345")
		if !errors.Is(err, ErrPasswordTooShort) {
			t.Fatalf("want ErrPasswordTooShort, got %v", err)
		}
	})

	t.Run("empty username", func(t *testing.T) {
		dir := t.TempDir()
		writeResetTestUsers(t, dir)
		err := ResetUserPasswordInDir(dir, "   ", "NewPass#2026")
		if !errors.Is(err, ErrUsernameEmpty) {
			t.Fatalf("want ErrUsernameEmpty, got %v", err)
		}
	})

	t.Run("empty users table", func(t *testing.T) {
		dir := t.TempDir()
		if err := WriteJSONFile(UsersFilePath(dir), Users{}); err != nil {
			t.Fatalf("WriteJSONFile: %v", err)
		}
		err := ResetUserPasswordInDir(dir, "admin", "NewPass#2026")
		if !errors.Is(err, ErrUsersFileMissing) {
			t.Fatalf("want ErrUsersFileMissing, got %v", err)
		}
	})
}

func TestLoadUsersFromDirMissing(t *testing.T) {
	_, err := LoadUsersFromDir(t.TempDir())
	if err == nil || !os.IsNotExist(err) {
		t.Fatalf("want not-exist error, got %v", err)
	}
	if got := UsersFilePath(filepath.Join("a", "b")); got != filepath.Join("a", "b", "users.json") {
		t.Fatalf("UsersFilePath = %q", got)
	}
}
