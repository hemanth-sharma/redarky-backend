package main

import (
	"log"
	"net/http"
	"time"

	"redarky/internal/scraper"
)

func main() {
	mux := http.NewServeMux()

	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		// Prevent "/" from hijacking all unmatched paths
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok","service":"redarky-scraper-active"}`))
	})

	// POST /scrape — main data extraction endpoint
	mux.HandleFunc("/scrape", scraper.HandleScrape)

	// GET /health — used by Docker health checks and load balancers
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"status":"ok","service":"redarky-scraper"}`))
	})

	server := &http.Server{
		Addr:         ":8081",
		Handler:      mux,
		ReadTimeout:  10 * time.Second,
		WriteTimeout: 50 * time.Second,
		IdleTimeout:  120 * time.Second,
	}

	log.Println("redarky-scraper listening on :8081")
	log.Fatal(server.ListenAndServe())
}
