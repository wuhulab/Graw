package graw

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/pem"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"

	"golang.org/x/crypto/ssh"
)

// SSH 密钥管理（数据文件与 Python 版一致：data/sshkeys.json + data/sshkeys/<id>/{id,pub}）。

var (
	sshKeyMu     sync.Mutex
	sshKeyIDRe   = regexp.MustCompile(`^key_[0-9a-fA-F]{6,32}$`)
	sshKeyTypeRe = regexp.MustCompile(`^(ed25519|rsa)$`)
	sshKeyMaxPEM = 64 * 1024
	sshPubLineRe = regexp.MustCompile(`^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256)\s+[A-Za-z0-9+/=]+\s*([A-Za-z0-9@._\-/ ]*)$`)
)

func sshKeysMetaPath() string { return DataPath("sshkeys.json") }
func sshKeysDir() string      { return DataPath("sshkeys") }

func loadSSHKeyMeta() map[string]any {
	sshKeyMu.Lock()
	defer sshKeyMu.Unlock()
	data := map[string]any{"version": 1, "keys": []any{}}
	if err := ReadJSON(sshKeysMetaPath(), &data); err != nil || data == nil {
		return map[string]any{"version": 1, "keys": []any{}}
	}
	if _, ok := data["keys"]; !ok {
		data["keys"] = []any{}
	}
	return data
}

func saveSSHKeyMeta(data map[string]any) error {
	sshKeyMu.Lock()
	defer sshKeyMu.Unlock()
	return WriteJSONFile(sshKeysMetaPath(), data)
}

func sshKeyList(data map[string]any) []map[string]any {
	out := []map[string]any{}
	if arr, ok := data["keys"].([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok {
				out = append(out, m)
			}
		}
	}
	return out
}

func sshFingerprint(pubLine string) string {
	parts := strings.Fields(pubLine)
	if len(parts) < 2 {
		return ""
	}
	raw, err := base64.StdEncoding.DecodeString(parts[1])
	if err != nil {
		return ""
	}
	sum := sha256.Sum256(raw)
	return "SHA256:" + base64.RawStdEncoding.EncodeToString(sum[:])
}

func sshKeyTypeOf(key ssh.PublicKey) string {
	switch key.Type() {
	case "ssh-ed25519":
		return "ed25519"
	case "ssh-rsa":
		return "rsa"
	default:
		if strings.HasPrefix(key.Type(), "ecdsa-") {
			return "ecdsa"
		}
	}
	return "unknown"
}

func sshPrivateDir(keyID string) string { return filepath.Join(sshKeysDir(), keyID) }

func writeSSHKeyFiles(keyID string, rawKey any, pubLine string) error {
	block, err := ssh.MarshalPrivateKey(rawKey, "")
	if err != nil {
		return err
	}
	dir := sshPrivateDir(keyID)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	privPath := filepath.Join(dir, "id")
	if err := os.WriteFile(privPath, pem.EncodeToMemory(block), 0o600); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(dir, "pub"), []byte(pubLine+"\n"), 0o600)
}

func readSSHPub(keyID string) (string, bool) {
	b, err := os.ReadFile(filepath.Join(sshPrivateDir(keyID), "pub"))
	if err != nil {
		return "", false
	}
	return strings.TrimSpace(string(b)), true
}

func registerSSHKeyRoutes(a *App) {
	a.Get("/api/sshkeys", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		data := loadSSHKeyMeta()
		keys := []map[string]any{}
		for _, k := range sshKeyList(data) {
			keys = append(keys, map[string]any{
				"id": k["id"], "name": k["name"], "key_type": k["key_type"],
				"comment": k["comment"], "fingerprint": k["fingerprint"],
				"created_at": k["created_at"],
			})
		}
		return WriteJSON(w, 200, map[string]any{"keys": keys})
	})
	a.Get("/api/sshkeys/nodes", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		store := loadNodeStore()
		nodes := []map[string]any{}
		for id, node := range store.Nodes {
			nodes = append(nodes, publicNode(id, node))
		}
		return WriteJSON(w, 200, map[string]any{"nodes": nodes})
	})
	a.Post("/api/sshkeys", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Name    string `json:"name"`
			KeyType string `json:"key_type"`
			Comment string `json:"comment"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		name := strings.TrimSpace(req.Name)
		if name == "" || len(name) > 64 {
			return NewHTTPError(400, "密钥名称不能为空且不超过 64 字符")
		}
		if req.KeyType == "" {
			req.KeyType = "ed25519"
		}
		if !sshKeyTypeRe.MatchString(req.KeyType) {
			return NewHTTPError(422, "密钥类型仅支持 ed25519 / rsa")
		}
		comment := strings.TrimSpace(req.Comment)
		if comment == "" {
			comment = "graw-" + name
		}
		if len(comment) > 128 {
			return NewHTTPError(400, "注释过长")
		}
		var rawKey any
		if req.KeyType == "rsa" {
			key, err := rsa.GenerateKey(rand.Reader, 3072)
			if err != nil {
				return NewHTTPError(500, "密钥生成失败")
			}
			rawKey = key
		} else {
			_, key, err := ed25519.GenerateKey(rand.Reader)
			if err != nil {
				return NewHTTPError(500, "密钥生成失败")
			}
			rawKey = key
		}
		signer, err := ssh.NewSignerFromKey(rawKey)
		if err != nil {
			return NewHTTPError(500, "密钥生成失败")
		}
		pubLine := strings.TrimSpace(string(ssh.MarshalAuthorizedKey(signer.PublicKey()))) + " " + comment
		keyID := "key_" + randomHex(10)
		if err := writeSSHKeyFiles(keyID, rawKey, pubLine); err != nil {
			return NewHTTPError(500, "密钥文件写入失败")
		}
		meta := map[string]any{
			"id": keyID, "name": name, "key_type": sshKeyTypeOf(signer.PublicKey()),
			"comment": comment, "fingerprint": sshFingerprint(pubLine),
			"created_at": time.Now().Format("2006-01-02T15:04:05"),
		}
		data := loadSSHKeyMeta()
		data["keys"] = append(sshKeyList(data), meta)
		if err := saveSSHKeyMeta(data); err != nil {
			return err
		}
		return WriteJSON(w, 200, meta)
	})
	a.Post("/api/sshkeys/import", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		var req struct {
			Name       string `json:"name"`
			PrivateKey string `json:"private_key"`
			Passphrase string `json:"passphrase"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		name := strings.TrimSpace(req.Name)
		pemText := strings.TrimSpace(req.PrivateKey)
		if name == "" || len(name) > 64 || pemText == "" || len(pemText) > sshKeyMaxPEM {
			return NewHTTPError(400, "名称或私钥长度非法")
		}
		var rawKey any
		var err error
		if req.Passphrase != "" {
			rawKey, err = ssh.ParseRawPrivateKeyWithPassphrase([]byte(pemText), []byte(req.Passphrase))
		} else {
			rawKey, err = ssh.ParseRawPrivateKey([]byte(pemText))
		}
		if err != nil {
			return NewHTTPError(400, "私钥解析失败: "+err.Error())
		}
		signer, err := ssh.NewSignerFromKey(rawKey)
		if err != nil {
			return NewHTTPError(400, "私钥解析失败")
		}
		pubLine := strings.TrimSpace(string(ssh.MarshalAuthorizedKey(signer.PublicKey())))
		keyID := "key_" + randomHex(10)
		if err := writeSSHKeyFiles(keyID, rawKey, pubLine); err != nil {
			return NewHTTPError(500, "密钥文件写入失败")
		}
		meta := map[string]any{
			"id": keyID, "name": name, "key_type": sshKeyTypeOf(signer.PublicKey()),
			"comment": "", "fingerprint": sshFingerprint(pubLine),
			"created_at": time.Now().Format("2006-01-02T15:04:05"),
		}
		data := loadSSHKeyMeta()
		data["keys"] = append(sshKeyList(data), meta)
		if err := saveSSHKeyMeta(data); err != nil {
			return err
		}
		return WriteJSON(w, 200, meta)
	})
	a.Get("/api/sshkeys/{key_id}/public", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		keyID := PathParam(r, "key_id")
		if !sshKeyIDRe.MatchString(keyID) {
			return NewHTTPError(400, "非法密钥 ID")
		}
		pub, ok := readSSHPub(keyID)
		if !ok || !sshPubLineRe.MatchString(pub) {
			return NewHTTPError(404, "公钥文件缺失")
		}
		return WriteJSON(w, 200, map[string]any{
			"id": keyID, "public_key": pub, "fingerprint": sshFingerprint(pub),
		})
	})
	a.Post("/api/sshkeys/{key_id}/deploy", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		keyID := PathParam(r, "key_id")
		if !sshKeyIDRe.MatchString(keyID) {
			return NewHTTPError(400, "非法密钥 ID")
		}
		var req struct {
			NodeID string `json:"node_id"`
		}
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		pub, ok := readSSHPub(keyID)
		if !ok || !sshPubLineRe.MatchString(pub) {
			return NewHTTPError(500, "公钥内容异常")
		}
		node := loadNodeStore().Nodes[req.NodeID]
		if node == nil || str(node["type"]) != "ssh" {
			return NewHTTPError(400, "目标节点不存在或非 SSH 节点")
		}
		if _, err := sshConnect(node, 10*time.Second); err != nil {
			return NewHTTPError(400, "目标节点不可达："+err.Error())
		}
		cmd := "mkdir -p ~/.ssh && chmod 700 ~/.ssh && " +
			"grep -qxF " + shellQuote(pub) + " ~/.ssh/authorized_keys 2>/dev/null || " +
			"printf '%s\\n' " + shellQuote(pub) + " >> ~/.ssh/authorized_keys; " +
			"chmod 600 ~/.ssh/authorized_keys"
		_, stderr, code, err := runShellOnNode(req.NodeID, cmd, 30*time.Second)
		if err != nil || code != 0 {
			return NewHTTPError(500, "部署失败："+truncateStr(strings.TrimSpace(stderr), 200))
		}
		return WriteJSON(w, 200, map[string]any{
			"ok": true, "node_id": req.NodeID,
			"node_name":   defaultString(str(node["name"]), req.NodeID),
			"fingerprint": sshFingerprint(pub),
		})
	})
	a.Delete("/api/sshkeys/{key_id}", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		keyID := PathParam(r, "key_id")
		if !sshKeyIDRe.MatchString(keyID) {
			return NewHTTPError(400, "非法密钥 ID")
		}
		data := loadSSHKeyMeta()
		keys := sshKeyList(data)
		kept := []map[string]any{}
		found := false
		for _, k := range keys {
			if str(k["id"]) == keyID {
				found = true
			} else {
				kept = append(kept, k)
			}
		}
		if !found {
			return NewHTTPError(404, "密钥不存在")
		}
		data["keys"] = kept
		if err := saveSSHKeyMeta(data); err != nil {
			return err
		}
		_ = os.RemoveAll(sshPrivateDir(keyID))
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
}

// sshKeyForNode 用于节点密钥登录时定位面板保管的私钥（兼容 Python 目录结构）。
func sshKeyForNode(node map[string]any) (string, bool) {
	keyPath := strings.TrimSpace(str(node["key_path"]))
	if keyPath == "" {
		return "", false
	}
	if Exists(keyPath) {
		return keyPath, true
	}
	if Exists(HostPath(keyPath)) {
		return HostPath(keyPath), true
	}
	return "", false
}
