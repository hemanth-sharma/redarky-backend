package scraper

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"

	"redarky/internal/models"
	"redarky/internal/reddit"

	"github.com/sony/gobreaker"
	"golang.org/x/time/rate"
)

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

type fetchBatchJob struct {
	combinedQuery string
	subreddit     string
}

type jobResult struct {
	items []models.ScrapedItem
	err   error
	job   fetchBatchJob
}

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

	if len(req.IncludeKeywords) == 0 && len(req.BrandKeywords) == 0 {
		http.Error(w, "at least one keyword required", http.StatusBadRequest)
		return
	}

	batchJobs := buildBatchJobs(&req)

	resultsCh := make(chan jobResult, len(batchJobs))
	var wg sync.WaitGroup

	for _, job := range batchJobs {
		wg.Add(1)
		go func(j fetchBatchJob) {
			defer wg.Done()
			items, err := executeRedditBatch(j, &req)
			if err != nil {
				log.Printf("[SCRAPER ERROR] Query: %q | Subreddit: %q | Err: %v", j.combinedQuery, j.subreddit, err)
			}
			resultsCh <- jobResult{items: items, err: err, job: j}
		}(job)
	}

	go func() {
		wg.Wait()
		close(resultsCh)
	}()

	seen := make(map[string]struct{})
	allItems := make([]models.ScrapedItem, 0)
	var sourceErrors []models.SourceError

	allKeywords := append(req.IncludeKeywords, req.BrandKeywords...)

	for res := range resultsCh {
		if res.err != nil {
			sourceErrors = append(sourceErrors, models.SourceError{
				Source:  "reddit",
				Query:   res.job.combinedQuery,
				Message: res.err.Error(),
			})
			continue
		}

		for _, item := range res.items {
			if _, exists := seen[item.ExternalID]; exists {
				continue
			}
			seen[item.ExternalID] = struct{}{}

			matchedKw := findMatchingKeyword(item.Title+" "+item.Content, allKeywords)
			if matchedKw != "" {
				item.MatchedKeyword = matchedKw
				item.IsBrandMention = isBrandKeyword(matchedKw, req.BrandKeywords)
				allItems = append(allItems, item)
			}
		}
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(models.ScrapeResult{
		Items:  allItems,
		Errors: sourceErrors,
	})
}

func executeRedditBatch(job fetchBatchJob, req *models.ScrapeRequest) ([]models.ScrapedItem, error) {
	ctx := context.Background()
	if err := redditLimiter.Wait(ctx); err != nil {
		return nil, err
	}

	result, err := redditBreaker.Execute(func() (interface{}, error) {
		return redditClient.FetchSearchBatch(
			job.combinedQuery,
			job.subreddit,
			req.Since,
			req.Before,
			req.Mode,
			req.ExcludeKeywords,
		)
	})

	if err != nil {
		return nil, err
	}

	return result.([]models.ScrapedItem), nil
}

func buildBatchJobs(req *models.ScrapeRequest) []fetchBatchJob {
	allKws := append(req.IncludeKeywords, req.BrandKeywords...)
	if len(allKws) == 0 {
		return nil
	}

	var combinedQuery string
	if len(allKws) == 1 {
		combinedQuery = allKws[0]
	} else {
		var terms []string
		for _, kw := range allKws {
			if strings.Contains(kw, " ") {
				terms = append(terms, fmt.Sprintf("%q", kw))
			} else {
				terms = append(terms, kw)
			}
		}
		combinedQuery = strings.Join(terms, " OR ")
	}

	var jobs []fetchBatchJob
	if len(req.Subreddits) > 0 {
		for _, sub := range req.Subreddits {
			jobs = append(jobs, fetchBatchJob{
				combinedQuery: combinedQuery,
				subreddit:     sub,
			})
		}
	} else {
		jobs = append(jobs, fetchBatchJob{
			combinedQuery: combinedQuery,
			subreddit:     "",
		})
	}

	return jobs
}

func findMatchingKeyword(text string, keywords []string) string {
	lowerText := strings.ToLower(text)
	for _, kw := range keywords {
		if kw != "" && strings.Contains(lowerText, strings.ToLower(kw)) {
			return kw
		}
	}
	return ""
}

func isBrandKeyword(kw string, brandKws []string) bool {
	lowerKW := strings.ToLower(kw)
	for _, bKw := range brandKws {
		if strings.ToLower(bKw) == lowerKW {
			return true
		}
	}
	return false
}
