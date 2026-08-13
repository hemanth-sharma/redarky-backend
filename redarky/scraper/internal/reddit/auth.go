package reddit

import (
	"bytes"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"math/big"
	mrand "math/rand"
	"sync"
	"sync/atomic"
	"time"

	http "github.com/bogdanfinn/fhttp"
	tls_client "github.com/bogdanfinn/tls-client"
	"github.com/bogdanfinn/tls-client/profiles"
)

const (
	AuthEndpoint    = "https://www.reddit.com/auth/v2/oauth/access-token/loid"
	AndroidClientID = "ohXpoqrZYub1kg"
)

var appVersions = []string{
	"2024.18.0", "2024.19.0", "2024.20.0", "2024.21.0",
}

type Session struct {
	AccessToken   string
	ExpiresAt     time.Time
	DeviceID      string
	UserAgent     string
	Loid          string
	SessionHeader string
	AndroidVer    int
	QoS           string
	MediaCodecs   string
}

type TokenManager struct {
	mu            sync.RWMutex
	current       atomic.Pointer[Session]
	httpClient    tls_client.HttpClient
	isRollingOver atomic.Bool
	rateLimitRem  atomic.Int32
}

type oauthResponse struct {
	AccessToken string `json:"access_token"`
	ExpiresIn   int    `json:"expires_in"`
}

func NewTokenManager() *TokenManager {
	clientOptions := []tls_client.HttpClientOption{
		tls_client.WithTimeoutSeconds(15),
		tls_client.WithClientProfile(profiles.Chrome_124),
		tls_client.WithNotFollowRedirects(),
	}
	httpClient, err := tls_client.NewHttpClient(tls_client.NewNoopLogger(), clientOptions...)
	if err != nil {
		log.Fatalf("Failed to create TLS client: %v", err)
	}

	tm := &TokenManager{
		httpClient: httpClient,
	}
	tm.rateLimitRem.Store(100)
	return tm
}

func (tm *TokenManager) InitDaemon() error {
	sess, err := tm.fetchAndroidSession()
	if err != nil {
		return fmt.Errorf("initial auth failed: %w", err)
	}
	tm.current.Store(sess)

	go tm.tokenDaemon()
	return nil
}

func (tm *TokenManager) GetSession() (*Session, error) {
	sess := tm.current.Load()
	if sess == nil || time.Now().After(sess.ExpiresAt) {
		if err := tm.ForceRefreshToken(); err != nil {
			return nil, err
		}
		return tm.current.Load(), nil
	}

	if tm.rateLimitRem.Load() < 10 {
		go func() { _ = tm.ForceRefreshToken() }()
	}

	return sess, nil
}

func (tm *TokenManager) ForceRefreshToken() error {
	if !tm.isRollingOver.CompareAndSwap(false, true) {
		for i := 0; i < 20; i++ {
			time.Sleep(100 * time.Millisecond)
			if !tm.isRollingOver.Load() {
				return nil
			}
		}
		return nil
	}
	defer tm.isRollingOver.Store(false)

	log.Println("[REDDIT OAUTH] Refreshing Android OAuth session...")
	sess, err := tm.fetchAndroidSession()
	if err != nil {
		return err
	}

	tm.current.Store(sess)
	tm.rateLimitRem.Store(99)
	return nil
}

func (tm *TokenManager) UpdateRateLimit(remaining float64) {
	tm.rateLimitRem.Store(int32(remaining))
}

func (tm *TokenManager) tokenDaemon() {
	for {
		sess := tm.current.Load()
		if sess != nil {
			sleepDuration := time.Until(sess.ExpiresAt.Add(-2 * time.Minute))
			if sleepDuration > 0 {
				time.Sleep(sleepDuration)
			}
		} else {
			time.Sleep(10 * time.Second)
		}
		_ = tm.ForceRefreshToken()
	}
}

func (tm *TokenManager) fetchAndroidSession() (*Session, error) {
	deviceID := generateUUID()
	androidVer := cryptoRandInt(9, 14)
	appVer := appVersions[cryptoRandInt(0, len(appVersions)-1)]
	userAgent := fmt.Sprintf("Reddit/%s/Android %d", appVer, androidVer)
	qos := fmt.Sprintf("%.3f", 1.0+mrand.Float64()*99.0)

	codecs := "available-codecs=video/avc, video/hevc"
	if mrand.Float32() > 0.5 {
		codecs += ", video/x-vnd.on2.vp9"
	}

	payload := map[string]interface{}{
		"scopes": []string{"*", "email", "pii"},
	}
	reqBody, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("auth payload marshal error: %w", err)
	}

	// Uses fhttp.NewRequest instead of standard net/http
	req, err := http.NewRequest(http.MethodPost, AuthEndpoint, bytes.NewBuffer(reqBody))
	if err != nil {
		return nil, err
	}

	authHeader := "Basic " + base64.StdEncoding.EncodeToString([]byte(AndroidClientID+":"))

	req.Header.Set("Authorization", authHeader)
	req.Header.Set("User-Agent", userAgent)
	req.Header.Set("Content-Type", "application/json; charset=UTF-8")
	req.Header.Set("X-Reddit-Device-Id", deviceID)
	req.Header.Set("client-vendor-id", deviceID)
	req.Header.Set("x-reddit-retry", "algo=no-retries")
	req.Header.Set("x-reddit-compression", "1")
	req.Header.Set("x-reddit-qos", qos)
	req.Header.Set("x-reddit-media-codecs", codecs)

	resp, err := tm.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("auth request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		respBytes, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("auth HTTP %d: %s", resp.StatusCode, string(respBytes))
	}

	var res oauthResponse
	if err := json.NewDecoder(resp.Body).Decode(&res); err != nil {
		return nil, fmt.Errorf("auth JSON decode: %w", err)
	}

	return &Session{
		AccessToken:   res.AccessToken,
		ExpiresAt:     time.Now().Add(time.Duration(res.ExpiresIn) * time.Second),
		DeviceID:      deviceID,
		UserAgent:     userAgent,
		Loid:          resp.Header.Get("x-reddit-loid"),
		SessionHeader: resp.Header.Get("x-reddit-session"),
		AndroidVer:    androidVer,
		QoS:           qos,
		MediaCodecs:   codecs,
	}, nil
}

func generateUUID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	hexStr := hex.EncodeToString(b)
	return fmt.Sprintf("%s-%s-%s-%s-%s", hexStr[0:8], hexStr[8:12], hexStr[12:16], hexStr[16:20], hexStr[20:32])
}

func cryptoRandInt(min, max int) int {
	nBig, err := rand.Int(rand.Reader, big.NewInt(int64(max-min+1)))
	if err != nil {
		return min
	}
	return int(nBig.Int64()) + min
}
