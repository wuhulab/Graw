package graw

// 本文件（totp.go）实现面板二次验证（TOTP，RFC 6238）的密钥生成与校验。
//
// 核心流程：GenerateOTPSecret 生成 20 字节随机密钥并以无填充 Base32 输出 →
// 用 OTPAuthURI 生成二维码/手动录入串交给 Authenticator → 登录时 VerifyTOTP
// 校验 6 位动态码。参数固定为 30 秒时间步、HMAC-SHA1、6 位数字，
// 与 Google/Microsoft/Authy 等客户端的默认参数一致。
//
// 安全约束 / 易踩坑：
//   - 校验比较必须用 subtle.ConstantTimeCompare，避免通过响应时间爆破动态码；
//   - 允许 ±1 个时间窗（共 3 个窗口）用于容忍客户端与服务端时钟偏差，
//     窗口越大可尝试的码越多，不要随意放宽；
//   - 密钥用无填充 Base32：这是 otpauth URI secret 参数的约定格式，兼容各类客户端。

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

// b32NoPad 无填充（NoPadding）的 Base32 编码器：TOTP 密钥与 otpauth URI 的约定格式。
var b32NoPad = base32.StdEncoding.WithPadding(base32.NoPadding)

// GenerateOTPSecret 生成 Base32 TOTP 密钥（20 字节随机数，无填充）。
//
// 20 字节 = 160 bit，是 RFC 4226 推荐的最小密钥长度；返回值可直接写入 otpauth URI。
// 熵源不可用时返回错误：全零密钥可被任何人推算动态码，等于关掉两步验证。
func GenerateOTPSecret() (string, error) {
	buf := make([]byte, 20)
	if _, err := rand.Read(buf); err != nil {
		return "", fmt.Errorf("crypto/rand 不可用: %w", err)
	}
	return b32NoPad.EncodeToString(buf), nil
}

// totpAt 计算给定时间计数器对应的 6 位 TOTP 码（RFC 6238 / HOTP 动态截断）。
//
// secret 非法（非合法 Base32）时返回空串，调用方据此跳过比较，不会误判为通过。
// counter 通常取 Unix()/30；独立出来便于校验函数复用相邻时间窗的计算。
func totpAt(secret string, counter uint64) string {
	key, err := b32NoPad.DecodeString(strings.ToUpper(strings.TrimSpace(secret)))
	if err != nil {
		return ""
	}
	var msg [8]byte
	// HOTP 的消息是 8 字节大端计数值（RFC 4226 §5.2）。
	binary.BigEndian.PutUint64(msg[:], counter)
	mac := hmac.New(sha1.New, key)
	mac.Write(msg[:])
	digest := mac.Sum(nil)
	// 动态截断：取摘要最后一字节的低 4 位作为偏移（RFC 4226 §5.3）。
	offset := digest[len(digest)-1] & 0x0F
	// 丢弃符号位后对 1e6 取模，确保结果是 0–999999 的 6 位数（含前导零）。
	code := binary.BigEndian.Uint32(digest[offset:offset+4]) & 0x7FFFFFFF
	return fmt.Sprintf("%06d", code%1000000)
}

// VerifyTOTP 校验 6 位动态码，允许 ±1 个时间窗（共 3 个窗口）。
//
// code 会先去掉首尾空白；长度不为 6 或含非数字字符时直接拒绝。
// secret 非法、时钟偏差超出窗口、码不匹配都返回 false（不区分原因，避免泄露信息）。
func VerifyTOTP(secret, code string) bool {
	code = strings.TrimSpace(code)
	if len(code) != 6 {
		// 只接受 6 位，长度不符直接拒绝，避免被当成子串/模糊匹配使用。
		return false
	}
	if _, err := strconv.Atoi(code); err != nil {
		// 必须全是十进制数字（Atoi 会拒绝空串与含字母的输入）。
		return false
	}
	// 30 秒一个时间步，与 Authenticator 默认 period 一致。
	counter := uint64(time.Now().Unix() / 30)
	for i := -1; i <= 1; i++ {
		c := counter
		if i < 0 {
			if counter == 0 {
				// 防止 uint64 下溢让 counter-1 变成极大值（1970 年附近才会遇到）。
				continue
			}
			c = counter - 1
		} else if i > 0 {
			c = counter + 1
		}
		expect := totpAt(secret, c)
		// 定长比较：即使失败也不泄露已匹配的前缀长度。
		if expect != "" && subtle.ConstantTimeCompare([]byte(expect), []byte(code)) == 1 {
			return true
		}
	}
	return false
}

// OTPAuthURI 构造 otpauth:// URI（供 Authenticator 扫码/手动录入）。
//
// issuer 同时出现在 label 前缀与 issuer 参数中；username 位于 label 的冒号之后。
// 未附加 algorithm/digits/period 参数，客户端将使用标准默认值（SHA1/6 位/30 秒），
// 与 totpAt 的实现保持一致。
func OTPAuthURI(secret, username string, issuer string) string {
	// label 部分用 PathEscape（保留 ":" 分隔符），query 参数用 QueryEscape，
	// 避免 issuer/username 中的空格或特殊字符破坏 URI 结构。
	return "otpauth://totp/" + url.PathEscape(issuer) + ":" + url.PathEscape(username) +
		"?secret=" + url.QueryEscape(secret) + "&issuer=" + url.QueryEscape(issuer)
}
