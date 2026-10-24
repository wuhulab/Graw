package resetpw

import (
	"bytes"
	"strings"
	"testing"

	"graw-go/internal/graw"
)

func seedUsers(t *testing.T, dir string) {
	t.Helper()
	hash, err := graw.HashPassword("OldPass#1")
	if err != nil {
		t.Fatalf("HashPassword: %v", err)
	}
	users := graw.Users{
		"admin": graw.User{
			"username": "admin", "password": hash, "role": "admin",
			"must_change_password": true,
		},
		"viewer": graw.User{
			"username": "viewer", "password": hash, "role": "user",
		},
	}
	if err := graw.WriteJSONFile(graw.UsersFilePath(dir), users); err != nil {
		t.Fatalf("write users: %v", err)
	}
}

func runCLI(t *testing.T, dir, input string, args ...string) (int, string, string) {
	t.Helper()
	var out, errOut bytes.Buffer
	code := Run(args, strings.NewReader(input), &out, &errOut, dir)
	return code, out.String(), errOut.String()
}

func TestRunResetSuccess(t *testing.T) {
	dir := t.TempDir()
	seedUsers(t, dir)
	code, out, errOut := runCLI(t, dir, "NewPass#2026\nNewPass#2026\n", "admin")
	if code != 0 {
		t.Fatalf("exit=%d stderr=%s", code, errOut)
	}
	if !strings.Contains(out, "密码已重置") {
		t.Fatalf("unexpected stdout: %s", out)
	}
	users, err := graw.LoadUsersFromDir(dir)
	if err != nil {
		t.Fatalf("load users: %v", err)
	}
	if !graw.VerifyPassword("NewPass#2026", users["admin"].PasswordHash()) {
		t.Fatal("新密码未生效")
	}
	if users["admin"].Bool("must_change_password") {
		t.Fatal("must_change_password 应被清除")
	}
}

func TestRunInteractive(t *testing.T) {
	dir := t.TempDir()
	seedUsers(t, dir)
	code, out, errOut := runCLI(t, dir, "admin\nNewPass#2026\nNewPass#2026\n")
	if code != 0 {
		t.Fatalf("exit=%d stderr=%s", code, errOut)
	}
	if !strings.Contains(out, "可用账号") {
		t.Fatalf("unexpected stdout: %s", out)
	}
	users, _ := graw.LoadUsersFromDir(dir)
	if !graw.VerifyPassword("NewPass#2026", users["admin"].PasswordHash()) {
		t.Fatal("交互式重置未生效")
	}
}

func TestRunList(t *testing.T) {
	dir := t.TempDir()
	seedUsers(t, dir)
	code, out, errOut := runCLI(t, dir, "", "-l")
	if code != 0 {
		t.Fatalf("exit=%d stderr=%s", code, errOut)
	}
	for _, want := range []string{"admin (admin)", "[待改密]", "viewer (user)"} {
		if !strings.Contains(out, want) {
			t.Fatalf("stdout 缺少 %q: %s", want, out)
		}
	}
}

func TestRunErrors(t *testing.T) {
	t.Run("mismatch", func(t *testing.T) {
		dir := t.TempDir()
		seedUsers(t, dir)
		code, _, errOut := runCLI(t, dir, "NewPass#2026\nOtherPass#1\n", "admin")
		if code != 1 || !strings.Contains(errOut, "不一致") {
			t.Fatalf("exit=%d stderr=%s", code, errOut)
		}
	})
	t.Run("short", func(t *testing.T) {
		dir := t.TempDir()
		seedUsers(t, dir)
		code, _, errOut := runCLI(t, dir, "12345\n12345\n", "admin")
		if code != 1 || !strings.Contains(errOut, "长度不足") {
			t.Fatalf("exit=%d stderr=%s", code, errOut)
		}
	})
	t.Run("unknown user", func(t *testing.T) {
		dir := t.TempDir()
		seedUsers(t, dir)
		code, _, errOut := runCLI(t, dir, "", "nobody")
		if code != 1 || !strings.Contains(errOut, "不存在") || !strings.Contains(errOut, "admin, viewer") {
			t.Fatalf("exit=%d stderr=%s", code, errOut)
		}
	})
	t.Run("missing users file", func(t *testing.T) {
		code, _, errOut := runCLI(t, t.TempDir(), "", "admin")
		if code != 1 || !strings.Contains(errOut, "未找到用户数据文件") {
			t.Fatalf("exit=%d stderr=%s", code, errOut)
		}
	})
	t.Run("empty username", func(t *testing.T) {
		dir := t.TempDir()
		seedUsers(t, dir)
		code, _, errOut := runCLI(t, dir, "\n")
		if code != 1 || !strings.Contains(errOut, "账号不能为空") {
			t.Fatalf("exit=%d stderr=%s", code, errOut)
		}
	})
}

func TestRunUsageError(t *testing.T) {
	code, _, _ := runCLI(t, t.TempDir(), "", "a", "b")
	if code != 2 {
		t.Fatalf("want exit 2, got %d", code)
	}
}
