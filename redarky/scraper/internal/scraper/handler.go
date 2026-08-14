package scraper

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"redarky/internal/models"
	"redarky/internal/reddit"
	"sync"
	"time"

	"github.com/sony/gobreaker"
	"golang.org/x/time/rate"
)

// ─────────────────────────────────────────────────────────────────────────────
// Globals — initialised once at process start.
//
// The rate limiter and circuit breaker below are UNCHANGED from the original
// implementation. They protect the Reddit OAuth surface from getting us
// IP-banned: 1 request per 500ms with burst of 5, and trips open after 4
// consecutive failures.
// ─────────────────────────────────────────────────────────────────────────────
var (
	redditLimiter = rate.NewLimiter(rate.Every(500*time.Millisecond), 5)

	redditBreaker = gobreaker.NewCircuitBreaker(gobreaker.Settings{
		Name:        "reddit-oauth",
		MaxRequests: 5,
		Interval:    60 * time.Second,
		Timeout:     30 * time.Second,
		ReadyToTrip: func(counts gobreaker.Counts) bool {
			return counts.ConsecutiveFailures >= 4
		},
	})

	tokenManager *reddit.TokenManager
	redditClient *reddit.Client
)

func init() {
	tokenManager = reddit.NewTokenManager()
	if err := tokenManager.InitDaemon(); err != nil {
		log.Printf("[WARNING] Token daemon initialization deferred: %v", err)
	}

	redditClient = reddit.NewClient(tokenManager)
}

// ─────────────────────────────────────────────────────────────────────────────
// Job model
//
// Two distinct job kinds now (was one combined "query + subreddit" job):
//
//	kind="keyword"   → FetchSearchBatch    (global Reddit search)
//	kind="subreddit" → FetchSubredditBatch (latest posts from r/X)
//
// This mirrors the new ScraperPayload contract where keywords and subreddits
// are independent lists — no cross-product fan-out (one job per keyword,
// one job per subreddit, full stop).
// ─────────────────────────────────────────────────────────────────────────────
type batchJob struct {
	kind      string // "keyword" | "subreddit"
	query     string // populated when kind == "keyword"
	subreddit string // populated when kind == "subreddit"
}

type jobResult struct {
	items []models.ScrapedItem
	err   error
	job   batchJob
}

// ─────────────────────────────────────────────────────────────────────────────
// HandleScrape — POST /scrape
//
// Flow:
//  1. Decode ScrapeRequest (new payload shape from FastAPI).
//  2. Build N jobs (one per keyword + one per subreddit).
//  3. Fan out concurrently through the rate limiter + circuit breaker.
//  4. Dedup items by ExternalID (same post can appear in both a keyword
//     search AND a subreddit pull — keep the first occurrence).
//  5. Return ScrapeResult.
//
// NOTE: keyword matching, brand-mention detection, and exclude-keyword
// filtering used to happen here in v1. They now happen on the Python side
// (Stage 1 of the 3-stage matching pipeline). Go just returns raw items.
// ─────────────────────────────────────────────────────────────────────────────
func HandleScrape(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req models.ScrapeRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid JSON: "+err.Error(), http.StatusBadRequest)
		return
	}

	if len(req.Keywords) == 0 && len(req.Subreddits) == 0 {
		http.Error(w, "at least one keyword or subreddit required", http.StatusBadRequest)
		return
	}

	jobs := buildBatchJobs(&req)
	resultsCh := make(chan jobResult, len(jobs))

	var wg sync.WaitGroup
	for _, job := range jobs {
		wg.Add(1)
		go func(j batchJob) {
			defer wg.Done()
			items, err := executeRedditBatch(j, &req)
			if err != nil {
				log.Printf("[SCRAPER ERROR] kind=%s query=%q sub=%q | Err: %v",
					j.kind, j.query, j.subreddit, err)
			}
			resultsCh <- jobResult{items: items, err: err, job: j}
		}(job)
	}

	go func() {
		wg.Wait()
		close(resultsCh)
	}()

	// Dedup by ExternalID — same post can show up in both a keyword search
	// and a subreddit pull. We keep the FIRST occurrence (deterministic given
	// job dispatch order, though results arrive non-deterministically).
	seen := make(map[string]struct{})
	allItems := make([]models.ScrapedItem, 0)
	var sourceErrors []models.SourceError

	for res := range resultsCh {
		if res.err != nil {
			queryLabel := res.job.query
			if queryLabel == "" {
				queryLabel = "r/" + res.job.subreddit
			}
			sourceErrors = append(sourceErrors, models.SourceError{
				Source:  "reddit",
				Query:   queryLabel,
				Message: res.err.Error(),
			})
			continue
		}

		for _, item := range res.items {
			if _, exists := seen[item.ExternalID]; exists {
				continue
			}
			seen[item.ExternalID] = struct{}{}
			allItems = append(allItems, item)
		}
	}

	log.Printf("[SCRAPER] done: %d items, %d errors", len(allItems), len(sourceErrors))

	// Prepare payload map/struct to save
	payload := models.ScrapeResult{
		Items:  allItems,
		Errors: sourceErrors,
	}

	// 1. Ensure the directory "redarky_data_s3/raw" exists (0755 provides read/write/execute permissions)
	dirPath := filepath.Join("redarky_data_s3", "raw")
	if err := os.MkdirAll(dirPath, 0755); err != nil {
		log.Printf("[SCRAPER STORAGE ERROR] failed to create directory: %v", err)
		// We don't return an HTTP error here unless you want local file persistence to be a hard requirement
	} else {
		// 2. Build file name using dynamic timestamp (Unix Nano or Milliseconds ensures uniqueness)
		timestamp := time.Now().UnixNano()
		fileName := fmt.Sprintf("scrape_%d.json", timestamp)
		filePath := filepath.Join(dirPath, fileName)

		// 3. Create the file and write JSON content directly to it
		file, err := os.Create(filePath)
		if err != nil {
			log.Printf("[SCRAPER STORAGE ERROR] failed to create file %s: %v", filePath, err)
		} else {
			defer file.Close()
			encoder := json.NewEncoder(file)
			encoder.SetIndent("", "    ") // Optional: Makes file readable instead of a single minified line

			if err := encoder.Encode(payload); err != nil {
				log.Printf("[SCRAPER STORAGE ERROR] failed to write JSON payload to file: %v", err)
			} else {
				log.Printf("[SCRAPER] locally backed up to: %s", filePath)
			}
		}
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(payload)
}

// executeRedditBatch dispatches a single job through the rate limiter and
// circuit breaker, then to the right reddit.Client method.
//
// The rate-limiter + circuit-breaker wrapping is UNCHANGED from v1 —
// this is the safety net that keeps us from getting IP-banned by Reddit.
func executeRedditBatch(job batchJob, req *models.ScrapeRequest) ([]models.ScrapedItem, error) {
	ctx := context.Background()
	if err := redditLimiter.Wait(ctx); err != nil {
		return nil, err
	}

	result, err := redditBreaker.Execute(func() (interface{}, error) {
		if job.kind == "keyword" {
			return redditClient.FetchSearchBatch(
				job.query,
				req.Sort,
				req.SinceTimestamp,
				req.IncludeComments,
			)
		}
		// subreddit pull — always posts-only, IncludeComments is N/A
		return redditClient.FetchSubredditBatch(
			job.subreddit,
			req.Sort,
			req.SinceTimestamp,
		)
	})

	if err != nil {
		return nil, err
	}
	if result == nil {
		return nil, nil
	}
	return result.([]models.ScrapedItem), nil
}

// buildBatchJobs fans the payload out into individual fetch jobs.
//
//   - one job per keyword       → global Reddit search (no subreddit restriction)
//   - one job per subreddit     → latest-posts pull (no keyword filter)
//
// Empty strings are skipped defensively.
func buildBatchJobs(req *models.ScrapeRequest) []batchJob {
	var jobs []batchJob

	for _, kw := range req.Keywords {
		if kw == "" {
			continue
		}
		jobs = append(jobs, batchJob{kind: "keyword", query: kw})
	}

	for _, sub := range req.Subreddits {
		if sub == "" {
			continue
		}
		jobs = append(jobs, batchJob{kind: "subreddit", subreddit: sub})
	}

	return jobs
}
