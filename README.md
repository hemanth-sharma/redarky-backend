User logs in
User creates his profile 
Adds information about his goal (What he wants from the platfrom (redarky) it's a social listening so he obviously wants to 
    track or monitor some keywords but there is still a goal like what is he trying to find or do by tracking the keywords,
    like finding leads for buying intent or brand sentiment mention or monitor his company's image etc)
User adds his company links or any information he has (if he has a company, optional)
user adds keywords to monitor (We will later add a suggest keywords feature as well based on all his profile information but for now user adds them manually)
Once user adds keywords, then user activates the pipeline that listens (on frontend there will be a button that activates the session)
Once the pipelien is running then for every batch (5 mins or 30 mins) data will pulled from the reddit (or other socail platfors later)
We hit go scraper to pull data from reddit 
Now this data goes to a data lake (unncessary maybe or we do something cheaper and efficient for now but we store raw data as well for some time)
And now this data goes to transformation into the format we need 
And now this data is matched according to keywords that user's specified etc stufs 
And then later on we also do some sementic matches (without llm) between user's provided profile data and extracted data
we select the data which matches based on keywords and passes initial filters 
then this data goes into posts (table which contains data that we show on frontend as matched clean tracked posts etc (later on we pass high intent posts to LLM so we can either sort them with priority and show the user which needs immediate action or completely filter irrelvant pots and only show important ones (for example only leads are shown)))
now this cleaned posts data is passed to the frontend. 
Now the frontend displays this data to the user. 
Now user can click on a post (link) and go that post (we also highlight the matched keyword that helped us decide on this post)
User clicks on the post and can see exactly where the matching happened so he can go to relevant comments or information.
User can view posts, sort them or apply other filters etc. 
User's posts data that we collect will be deleted automatically from the database within 30 days, we don't store reddit's data for more than 30 days, it automatically is removed by default, user can also choose to remove this data in less time (I'll add a setting to allow 10 days or 5 days etc information later on)

I know social listening is abosolutely crowded therefore i want to make sure that the project is focussed on leads generation type of things, think of it's primary focus as "Find people actively looking to buy your competitor's product right now" something like that, so we try to position it like a high intent lead generation and automated revenue capture.
I also don't want to have users signing up and then running the pipeline to get leads and once they get leads they just leave the platform and sign off, Another issue is API Rate Limits (for now i only have reddit keyword). Therefore I don't know how i will handle this but I will need to figure out. 
First I want to make the product MVP as quick as possible (I have the data extraction part ready in Go), I to be honest don't know if I can place this platform as a lead generation because agencies usually use social listening to make their 
company known more by commenting somewhere etc I'm not sure if they find leads or not, another thing is my product's is not yet validated with real users so I need to make a working MVP flow that atleast feels right first and then I try to get some customers. And if my product can do Cold outbound using my own tool for itself then that also is good (finding people who look for my competitors)





# ListeningAgentAi (Social Listening & Lead Generation System)
#### Backend: https://listening-agent-backend-6k9s.onrender.com/docs
#### Frontend: https://redarky.vercel.app/queue
A high-performance, asynchronous AI-driven social listening system built to track, ingest, filter, and process real-time market data from platforms like Reddit to extract intent-driven B2B leads. The system features a monolithic multi-process architecture orchestrated via Docker to run core API routing, database layers, caching layers, and background worker queues seamlessly in a single lightweight instance.

---

## 🏗 System Architecture & Flow

1. **Ingestion Layer:** Real-time data or webhook streams from scraping actors (e.g., Apify Reddit actors) feed transactional data into the FastAPI web server via token-validated routing (`/ingestion/reddit`).
2. **Task Distribution (Broker):** Ingested raw payloads are offloaded immediately to a localized internal Redis cache cluster to ensure zero-blocking, low-latency API response times.
3. **Background Worker Engine:** A distributed Celery worker pool processes the active queue, handling tokenization, system keyword alignment, and state mapping.
4. **AI Processing Layer (LangGraph):** The core AI engine executes structured mission graphs using state-of-the-art LLM supervisors to extract market gaps, intent patterns, and target lead profiles.
5. **Storage & Vector Layer:** Processed information and structured lead insights are finalized inside a vectorized PostgreSQL relational database using schema-managed extensions for efficient storage and later vector retrieval.

---

## 🛠 Tech Stack

* **Core Framework:** FastAPI (Asynchronous Python 3.11, Pydantic v2 data verification)
* **Database Layers:** PostgreSQL 17 (Vector-enabled schema configuration)
* **Asynchronous Processing:** Celery (Worker orchestration engine)
* **Caching & Brokerage:** Redis Server (High-throughput message broker)
* **AI & Multi-Agent Framework:** LangGraph / LangChain Core (Hierarchical multi-agent supervisor/worker architecture)
* **Schema & Migrations:** Alembic (Asynchronous database schema version control)

---

## 🚀 Monolithic Production Deployment Architecture

To achieve absolute cost-efficiency and reliable operation for demonstration environments, the system utilizes a **Monolithic Process Bootloader Model** encapsulated cleanly within a single production Docker container. 

When the system initializes, a low-level Linux shell bootloader script (`start.sh`) natively provisions, manages, and executes all structural subsystems concurrently:
1. **PostgreSQL Daemon Service** initialization, cluster creation, and active runtime management.
2. **Alembic Database Migration** auto-discovery and sequential execution against localhost targets.
3. **Redis Caching Server** daemonization to serve as the local atomic message broker.
4. **Celery Worker Engine** spawning and routing to asynchronous task queues (`default`, `matching`).
5. **FastAPI Web Engine** execution via Uvicorn on exposed production routing ports.

---

## 💻 Local Development Setup

### Prerequisites
* Python 3.11+
* Docker & Docker Compose (Optional, for multi-container local execution)

### 1. Manual Multi-Process Setup (Without Docker Compose)

**Initialize local cache database (Redis):**
```bash
docker run -d -p 6379:6379 redis
