package models

// ─────────────────────────────────────────────────────────────────────────────
// types.go — Go scraper payload contract.
//
// These structs MUST mirror app/scraper/schemas.py on the FastAPI side:
//
//   ScraperPayload  ↔  ScrapeRequest
//   ScrapedItem     ↔  ScrapedItem
//   GoScrapeResult  ↔  ScrapeResult
//   SourceError     ↔  SourceError
//
// Field names use snake_case JSON tags so they serialize identically to the
// Pydantic models. Do NOT rename a JSON tag without updating the Python side.
//
// CHANGES FROM THE OLD CONTRACT (v1 → v2):
//   • Removed: project_id, exclude_keywords, brand_keywords, platforms,
//     before, mode (live/backfill).
//   • Renamed: include_keywords → keywords.
//   • Added:   sort, since_timestamp (nullable), include_comments.
//   • Item:    removed matched_keyword / is_brand_mention / scraped_at
//              (matching now happens in Python Stage 1, not in Go).
//   • Item:    renamed created_at → created_at_platform.
//   • Item:    added comments_count.
// ─────────────────────────────────────────────────────────────────────────────

// ScrapeRequest is the body FastAPI POSTs to /scrape.
//
//   keywords         — global Reddit search terms (one search per keyword)
//   subreddits       — direct subreddit pulls (one fetch per subreddit)
//   sort             — "new" | "hot" | "top" (default "new")
//   since_timestamp  — Unix epoch cutoff; older posts are dropped. nil = no cutoff.
//   include_comments — when true, Reddit search may also return t1 (comment)
//                      results. Subreddit pulls are always posts-only.
type ScrapeRequest struct {
	Keywords        []string `json:"keywords"`
	Subreddits      []string `json:"subreddits"`
	Sort            string   `json:"sort"`
	SinceTimestamp  *int64   `json:"since_timestamp"`
	IncludeComments bool     `json:"include_comments"`
}

// ScrapedItem is the normalised record returned to FastAPI.
// Must match Go's internal struct exactly — see app/scraper/schemas.py::ScrapedItem.
type ScrapedItem struct {
	Source            string `json:"source"`      // "reddit"
	ExternalID        string `json:"external_id"` // Reddit fullname e.g. "t3_abc123"
	Title             string `json:"title"`       // "" for comments
	Content           string `json:"content"`     // selftext for posts, body for comments
	Author            string `json:"author"`      // "unknown" if missing
	URL               string `json:"url"`         // permalink on reddit.com
	Score             int    `json:"score"`
	CommentsCount     int    `json:"comments_count"`      // num_comments from Reddit API
	PostType          string `json:"post_type"`           // "post" | "comment"
	Subreddit         string `json:"subreddit"`           // e.g. "SaaS" (no r/ prefix)
	CreatedAtPlatform *int64 `json:"created_at_platform"` // Unix epoch from Reddit; nil if unknown
}

// ScrapeResult is the JSON body returned by POST /scrape.
type ScrapeResult struct {
	Items  []ScrapedItem `json:"items"`
	Errors []SourceError `json:"errors,omitempty"`
}

// SourceError represents a per-source failure (rate limit, 4xx, etc.).
// One entry per failed fetch — does NOT fail the whole batch.
type SourceError struct {
	Source  string `json:"source"`  // "reddit"
	Query   string `json:"query"`   // the keyword or "r/<subreddit>" that failed
	Message string `json:"message"` // human-readable error
}
