package models

type ScrapeMode string

const (
	ModeLive     ScrapeMode = "live"
	ModeBackfill ScrapeMode = "backfill"
)

type ScrapeRequest struct {
	ProjectID       string     `json:"project_id"`
	IncludeKeywords []string   `json:"include_keywords"`
	ExcludeKeywords []string   `json:"exclude_keywords"`
	BrandKeywords   []string   `json:"brand_keywords"`
	Platforms       []string   `json:"platforms"`
	Subreddits      []string   `json:"subreddits"`
	Since           int64      `json:"since"`
	Before          string     `json:"before"` // Native Reddit ID cursor (e.g. "t3_1c8xyz")[cite: 2]
	Mode            ScrapeMode `json:"mode"`
}

type ScrapedItem struct {
	Source         string `json:"source"`
	ExternalID     string `json:"external_id"` // Fullname e.g., t3_xxx
	Title          string `json:"title"`
	Content        string `json:"content"`
	URL            string `json:"url"`
	Author         string `json:"author"`
	Score          int    `json:"score"`
	Subreddit      string `json:"subreddit"`
	PostType       string `json:"post_type"`
	MatchedKeyword string `json:"matched_keyword"`
	IsBrandMention bool   `json:"is_brand_mention"`
	CreatedAt      int64  `json:"created_at"`
	ScrapedAt      string `json:"scraped_at"`
}

type ScrapeResult struct {
	Items  []ScrapedItem `json:"items"`
	Errors []SourceError `json:"errors,omitempty"`
}

type SourceError struct {
	Source  string `json:"source"`
	Query   string `json:"query"`
	Message string `json:"message"`
}
