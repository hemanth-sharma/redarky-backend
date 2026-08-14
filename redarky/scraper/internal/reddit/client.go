package reddit

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/url"
	"strconv"
	"strings"

	"redarky/internal/models"

	http "github.com/bogdanfinn/fhttp"
	tls_client "github.com/bogdanfinn/tls-client"
	"github.com/bogdanfinn/tls-client/profiles"
)

const OAuthBaseURL = "https://oauth.reddit.com"

// Client wraps a TLS-fingerprinted http client and a TokenManager.
// The TLS profile (Chrome_124) and noop-logger setup are unchanged from
// the original — this is the surface that makes Reddit see us as a browser.
type Client struct {
	tokenMgr   *TokenManager
	httpClient tls_client.HttpClient
}

func NewClient(tm *TokenManager) *Client {
	clientOptions := []tls_client.HttpClientOption{
		tls_client.WithTimeoutSeconds(15),
		tls_client.WithClientProfile(profiles.Chrome_124),
		tls_client.WithNotFollowRedirects(),
	}
	httpClient, err := tls_client.NewHttpClient(tls_client.NewNoopLogger(), clientOptions...)
	if err != nil {
		log.Fatalf("Failed to create TLS client for scraper: %v", err)
	}

	return &Client{
		tokenMgr:   tm,
		httpClient: httpClient,
	}
}

// redditListing is the JSON envelope returned by Reddit's listing endpoints.
// Field set is the union of what /search.json and /r/{sub}/{sort}.json return.
type redditListing struct {
	Data struct {
		Children []struct {
			Kind string `json:"kind"` // "t3" = post, "t1" = comment
			Data struct {
				ID          string  `json:"id"`
				Name        string  `json:"name"` // Fullname e.g. "t3_abc123"
				Title       string  `json:"title"`
				Selftext    string  `json:"selftext"`
				Body        string  `json:"body"`
				URL         string  `json:"url"`
				Permalink   string  `json:"permalink"`
				Author      string  `json:"author"`
				Score       int     `json:"score"`
				NumComments int     `json:"num_comments"`
				Subreddit   string  `json:"subreddit"`
				CreatedUtc  float64 `json:"created_utc"`
				IsSelf      bool    `json:"is_self"`
			} `json:"data"`
		} `json:"children"`
	} `json:"data"`
}

// ─────────────────────────────────────────────────────────────────────────────
// Public fetch methods
//
// Two distinct entry points now exist (was one combined method):
//
//   FetchSearchBatch    — global Reddit search for a single keyword.
//   FetchSubredditBatch — latest posts from a single subreddit (no keyword).
//
// Both go through the shared doRedditRequest() which preserves the original
// TLS / header / rate-limit / circuit-breaker behaviour byte-for-byte.
// ─────────────────────────────────────────────────────────────────────────────

// FetchSearchBatch does a GLOBAL Reddit search (no subreddit restriction)
// for a single keyword.
//
//	query            — the keyword to search for
//	sort             — "new" | "hot" | "top" (empty → "new")
//	sinceTimestamp    — Unix epoch cutoff; older posts are dropped (nil = no cutoff)
//	includeComments  — when true, the request omits `type=link` so Reddit
//	                    may also return t1 (comment) results
func (c *Client) FetchSearchBatch(query, sort string, sinceTimestamp *int64, includeComments bool) ([]models.ScrapedItem, error) {
	if strings.TrimSpace(query) == "" {
		return nil, fmt.Errorf("empty query")
	}

	sess, err := c.tokenMgr.GetSession()
	if err != nil {
		return nil, fmt.Errorf("session error: %w", err)
	}

	if sort == "" {
		sort = "new"
	}

	// Build the URL. `t=day` matches the original "live" mode — fresh posts
	// only, which is what we want for high-intent lead detection.
	//
	// `type` param: Reddit accepts "link" (posts), "comment", or "sr".
	// To get BOTH posts and comments, OMIT the type param entirely (default
	// is "all"). When includeComments=false, we explicitly set type=link.
	q := url.Values{}
	q.Set("q", query)
	q.Set("sort", sort)
	q.Set("t", "day")
	q.Set("limit", "100")
	q.Set("raw_json", "1")
	if !includeComments {
		q.Set("type", "link")
	}
	rawURL := fmt.Sprintf("%s/search.json?%s", OAuthBaseURL, q.Encode())

	return c.doRedditRequest(rawURL, sess, sinceTimestamp)
}

// FetchSubredditBatch pulls the latest posts from a single subreddit
// (no keyword filter). Used for high-precision sources where we want
// everything posted in r/SaaS, r/productivity, etc.
//
//	subreddit       — name without r/ prefix (will be stripped if present)
//	sort            — "new" | "hot" | "top" (empty → "new")
//	sinceTimestamp  — Unix epoch cutoff; older posts are dropped (nil = no cutoff)
//
// Note: subreddit pulls always return posts (t3) only. To get comments you
// would need a separate /comments/{post_id} call per post — out of scope for MVP.
func (c *Client) FetchSubredditBatch(subreddit, sort string, sinceTimestamp *int64) ([]models.ScrapedItem, error) {
	subreddit = strings.TrimSpace(subreddit)
	if subreddit == "" {
		return nil, fmt.Errorf("empty subreddit")
	}
	// Be forgiving about r/ prefix
	subreddit = strings.TrimPrefix(subreddit, "r/")

	sess, err := c.tokenMgr.GetSession()
	if err != nil {
		return nil, fmt.Errorf("session error: %w", err)
	}

	if sort == "" {
		sort = "new"
	}

	q := url.Values{}
	q.Set("limit", "100")
	q.Set("raw_json", "1")
	rawURL := fmt.Sprintf(
		"%s/r/%s/%s.json?%s",
		OAuthBaseURL,
		url.PathEscape(subreddit),
		url.PathEscape(sort),
		q.Encode(),
	)

	return c.doRedditRequest(rawURL, sess, sinceTimestamp)
}

// ─────────────────────────────────────────────────────────────────────────────
// Shared HTTP + parse — preserves original TLS-fingerprint surface.
//
// Every header below (Authorization, User-Agent, X-Reddit-Device-Id,
// client-vendor-id, x-reddit-retry, x-reddit-compression, x-reddit-qos,
// x-reddit-media-codecs, x-reddit-loid, x-reddit-session, Cookie) is
// required for Reddit to identify the request as a Chrome-124 Android
// client. Do NOT remove or reorder headers without testing against
// production Reddit — even header ORDER matters for the TLS fingerprint.
// ─────────────────────────────────────────────────────────────────────────────
func (c *Client) doRedditRequest(rawURL string, sess *Session, sinceTimestamp *int64) ([]models.ScrapedItem, error) {
	req, err := http.NewRequest(http.MethodGet, rawURL, nil)
	if err != nil {
		return nil, err
	}

	// ── Headers (unchanged from original — TLS-fingerprint surface) ──
	req.Header.Set("Authorization", "Bearer "+sess.AccessToken)
	req.Header.Set("User-Agent", sess.UserAgent)
	req.Header.Set("X-Reddit-Device-Id", sess.DeviceID)
	req.Header.Set("client-vendor-id", sess.DeviceID)
	req.Header.Set("Accept", "*/*")
	req.Header.Set("x-reddit-retry", "algo=no-retries")
	req.Header.Set("x-reddit-compression", "1")
	req.Header.Set("x-reddit-qos", sess.QoS)
	req.Header.Set("x-reddit-media-codecs", sess.MediaCodecs)

	if sess.Loid != "" {
		req.Header.Set("x-reddit-loid", sess.Loid)
	}
	if sess.SessionHeader != "" {
		req.Header.Set("x-reddit-session", sess.SessionHeader)
	}

	// Sync Cookie header with LOID/Session if present
	if sess.Loid != "" && sess.SessionHeader != "" {
		req.Header.Set("Cookie", fmt.Sprintf("loid=%s; session=%s", sess.Loid, sess.SessionHeader))
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	// ── Rate-limit accounting (unchanged) ──
	if remStr := resp.Header.Get("x-ratelimit-remaining"); remStr != "" {
		if remVal, err := strconv.ParseFloat(remStr, 64); err == nil {
			c.tokenMgr.UpdateRateLimit(remVal)
		}
	}

	// ── Rate-limit / 403-with-Retry-After handling (unchanged) ──
	if resp.StatusCode == http.StatusTooManyRequests ||
		(resp.StatusCode == http.StatusForbidden && resp.Header.Get("Retry-After") != "") {
		log.Printf("[REDDIT RATE LIMIT] Status %d encountered. Forcing token rotation.", resp.StatusCode)
		_ = c.tokenMgr.ForceRefreshToken()
		return nil, fmt.Errorf("reddit rate limit hit (HTTP %d)", resp.StatusCode)
	}

	if resp.StatusCode != http.StatusOK {
		respBytes, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("reddit oauth status %d: %s", resp.StatusCode, string(respBytes))
	}

	var listing redditListing
	if err := json.NewDecoder(resp.Body).Decode(&listing); err != nil {
		return nil, fmt.Errorf("JSON decode error: %w", err)
	}

	return parseListing(&listing, sinceTimestamp), nil
}

// parseListing extracts ScrapedItems from a Reddit listing, applying the
// sinceTimestamp cutoff and skipping deleted/removed posts.
//
// This was previously inline in FetchSearchBatch; pulled out so both
// FetchSearchBatch and FetchSubredditBatch can share it.
func parseListing(listing *redditListing, sinceTimestamp *int64) []models.ScrapedItem {
	results := make([]models.ScrapedItem, 0, len(listing.Data.Children))

	for _, child := range listing.Data.Children {
		d := child.Data

		// Time cutoff (only if caller provided one)
		if sinceTimestamp != nil && int64(d.CreatedUtc) < *sinceTimestamp {
			continue
		}

		// Skip deleted / removed
		if d.Author == "[deleted]" || d.Selftext == "[removed]" {
			continue
		}

		// Build the post URL
		postURL := d.URL
		if d.IsSelf || d.Permalink != "" {
			postURL = "https://www.reddit.com" + d.Permalink
		}

		// Posts (t3) vs comments (t1) have different field semantics
		postType := "post"
		content := d.Selftext
		title := d.Title
		if child.Kind == "t1" {
			postType = "comment"
			content = d.Body
			title = "(comment)"
			postURL = "https://www.reddit.com" + d.Permalink
		}

		createdUtc := int64(d.CreatedUtc)

		results = append(results, models.ScrapedItem{
			Source:            "reddit",
			ExternalID:        d.Name,
			Title:             title,
			Content:           content,
			URL:               postURL,
			Author:            d.Author,
			Score:             d.Score,
			CommentsCount:     d.NumComments,
			Subreddit:         d.Subreddit,
			PostType:          postType,
			CreatedAtPlatform: &createdUtc,
		})
	}

	return results
}
