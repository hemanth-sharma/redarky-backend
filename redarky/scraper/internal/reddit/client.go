package reddit

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/url"
	"strconv"
	"strings"
	"time"

	"redarky/internal/models"

	http "github.com/bogdanfinn/fhttp"
	tls_client "github.com/bogdanfinn/tls-client"
	"github.com/bogdanfinn/tls-client/profiles"
)

const OAuthBaseURL = "https://oauth.reddit.com"

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

type redditListing struct {
	Data struct {
		Children []struct {
			Kind string `json:"kind"`
			Data struct {
				ID         string  `json:"id"`
				Name       string  `json:"name"`
				Title      string  `json:"title"`
				Selftext   string  `json:"selftext"`
				Body       string  `json:"body"`
				URL        string  `json:"url"`
				Permalink  string  `json:"permalink"`
				Author     string  `json:"author"`
				Score      int     `json:"score"`
				Subreddit  string  `json:"subreddit"`
				CreatedUtc float64 `json:"created_utc"`
				IsSelf     bool    `json:"is_self"`
			} `json:"data"`
		} `json:"children"`
	} `json:"data"`
}

func (c *Client) FetchSearchBatch(query string, subreddit string, since int64, before string, mode models.ScrapeMode, excludeKeywords []string) ([]models.ScrapedItem, error) {
	sess, err := c.tokenMgr.GetSession()
	if err != nil {
		return nil, fmt.Errorf("session error: %w", err)
	}

	sortParam := "new"
	timeParam := "day"
	if mode == models.ModeBackfill {
		sortParam = "relevance"
		timeParam = "month"
	}

	var rawURL string
	if subreddit != "" {
		rawURL = fmt.Sprintf(
			"%s/r/%s/search.json?q=%s&restrict_sr=1&sort=%s&t=%s&limit=100&raw_json=1",
			OAuthBaseURL,
			url.PathEscape(subreddit),
			url.QueryEscape(query),
			sortParam,
			timeParam,
		)
	} else {
		rawURL = fmt.Sprintf(
			"%s/search.json?q=%s&sort=%s&t=%s&limit=100&raw_json=1",
			OAuthBaseURL,
			url.QueryEscape(query),
			sortParam,
			timeParam,
		)
	}

	if before != "" {
		rawURL += "&before=" + url.QueryEscape(before)
	}

	req, err := http.NewRequest(http.MethodGet, rawURL, nil)
	if err != nil {
		return nil, err
	}

	// Set headers cleanly on fhttp.Request
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

	if remStr := resp.Header.Get("x-ratelimit-remaining"); remStr != "" {
		if remVal, err := strconv.ParseFloat(remStr, 64); err == nil {
			c.tokenMgr.UpdateRateLimit(remVal)
		}
	}

	if resp.StatusCode == http.StatusTooManyRequests || (resp.StatusCode == http.StatusForbidden && resp.Header.Get("Retry-After") != "") {
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

	now := time.Now().Format(time.RFC3339)
	results := make([]models.ScrapedItem, 0, len(listing.Data.Children))

	for _, child := range listing.Data.Children {
		d := child.Data

		if int64(d.CreatedUtc) < since {
			continue
		}

		textToCheck := strings.ToLower(d.Title + " " + d.Selftext + " " + d.Body)
		if containsAny(textToCheck, excludeKeywords) {
			continue
		}

		if d.Author == "[deleted]" || d.Selftext == "[removed]" {
			continue
		}

		postURL := d.URL
		if d.IsSelf || d.Permalink != "" {
			postURL = "https://www.reddit.com" + d.Permalink
		}

		postType := "post"
		content := d.Selftext
		title := d.Title
		if child.Kind == "t1" {
			postType = "comment"
			content = d.Body
			title = "(comment)"
			postURL = "https://www.reddit.com" + d.Permalink
		}

		results = append(results, models.ScrapedItem{
			Source:     "reddit",
			ExternalID: d.Name,
			Title:      title,
			Content:    content,
			URL:        postURL,
			Author:     d.Author,
			Score:      d.Score,
			Subreddit:  d.Subreddit,
			PostType:   postType,
			CreatedAt:  int64(d.CreatedUtc),
			ScrapedAt:  now,
		})
	}

	return results, nil
}

func containsAny(text string, keywords []string) bool {
	for _, kw := range keywords {
		if kw != "" && strings.Contains(text, strings.ToLower(kw)) {
			return true
		}
	}
	return false
}
