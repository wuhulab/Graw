package graw

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha1"
	"crypto/subtle"
	"encoding/base32"
	"encoding/binary"
	"fmt"
	"net/url"
	"strconv"
	"strings"
	"time"
)

var b32NoPad = base32.StdEncoding.WithPadding(base32.NoPadding)

// GenerateOTPSecret 生成 Base32 TOTP 密钥（20 字节随机数，无填充）。
func GenerateOTPSecret() string {
	buf := make([]byte, 20)
	_, _ = rand.Read(buf)
	return b32NoPad.EncodeToString(buf)
}

func totpAt(secret string, counter uint64) string {
	key, err := b32NoPad.DecodeString(strings.ToUpper(strings.TrimSpace(secret)))
	if err != nil {
		return ""
	}
	var msg [8]byte
	binary.BigEndian.PutUint64(msg[:], counter)
	mac := hmac.New(sha1.New, key)
	mac.Write(msg[:])
	digest := mac.Sum(nil)
	offset := digest[len(digest)-1] & 0x0F
	code := binary.BigEndian.Uint32(digest[offset:offset+4]) & 0x7FFFFFFF
	return fmt.Sprintf("%06d", code%1000000)
}

// VerifyTOTP 校验 6 位动态码，允许 ±1 个时间窗（共 3 个窗口）。
func VerifyTOTP(secret, code string) bool {
	code = strings.TrimSpace(code)
	if len(code) != 6 {
		return false
	}
	if _, err := strconv.Atoi(code); err != nil {
		return false
	}
	counter := uint64(time.Now().Unix() / 30)
	for i := -1; i <= 1; i++ {
		c := counter
		if i < 0 {
			if counter == 0 {
				continue
			}
			c = counter - 1
		} else if i > 0 {
			c = counter + 1
		}
		expect := totpAt(secret, c)
		if expect != "" && subtle.ConstantTimeCompare([]byte(expect), []byte(code)) == 1 {
			return true
		}
	}
	return false
}

// OTPAuthURI 构造 otpauth:// URI（供 Authenticator 扫码/手动录入）。
func OTPAuthURI(secret, username string, issuer string) string {
	return "otpauth://totp/" + url.PathEscape(issuer) + ":" + url.PathEscape(username) +
		"?secret=" + url.QueryEscape(secret) + "&issuer=" + url.QueryEscape(issuer)
}
