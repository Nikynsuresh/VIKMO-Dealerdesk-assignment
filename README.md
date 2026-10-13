# VIKMO DealerDesk — Production-Quality AI-Native B2B Commerce Platform

**Track:** AI / ML Intern Track  
**Role:** AI / ML Engineer & Full-Stack Systems Engineer  
**Evaluation Video Link:** [https://youtu.be/placeholder-vikmo-dealerdesk](https://youtu.be/placeholder-vikmo-dealerdesk)  

---

## 1. Project Overview

**VIKMO DealerDesk** is a production-grade B2B commerce platform built for Indian automotive parts distributors supplying 25 regional dealerships from a catalogue of 600 motorcycle and car parts.

This repository implements the requirements for the **AI / ML Track**, plus all bonus core backend features from the Django Track:
- **Relational Backend & Concurrency Engine (Django REST Framework & PostgreSQL):** Products, inventory, dealers, atomic draft orders, race-condition-safe stock locking with `select_for_update()`, exact line-by-line 18% GST rounding (`ROUND_HALF_UP`), monotonic daily sequence numbers (`ORD-YYYYMMDD-NNNN`), and `Idempotency-Key` headers.
- **AI Dealer Assistant with Native LLM Tool Calling:** Multi-turn session persistence in PostgreSQL, tool execution (`search_parts`, `check_stock`, `create_draft_order`), grounding in live PostgreSQL data, prompt-injection defense against untrusted supplier descriptions, and multi-provider support (Gemini, Groq, OpenAI, Anthropic).
- **Demand Forecasting Pipeline (ML):** 4-week future demand forecasting for 40 SKUs across the weeks starting 2026-09-28 to 2026-10-19. Validated on historical holdout data against 3 assignment baselines, generating exactly 160 predictions in [`ml/forecast.csv`](ml/forecast.csv).
- **Dynamic Reorder Engine:** Exact safety-stock formula implementation ($R = \text{LTD} + \lceil 1.65 \times \sigma \times \sqrt{L} \rceil$) driving live inventory replenishment suggestions for all 40 SKUs.
- **Supplier Synchronization Hub:** Cursor-paginated catalogue integration with exponential backoff on 429 rate limits, timestamp deduplication, atomic database transactions, and zero stock mutation.
- **Executive Operations Portal:** Built with clean, distinctive midnight-navy design tokens, dealer identity switching, live telemetry dashboards, catalogue search, and order lifecycle management.

---

## 2. Mandatory Run Contract

The grader clones the repository, supplies a fresh empty PostgreSQL database via `DATABASE_URL`, and runs:

```bash
pip install -r requirements.txt
python manage.py migrate --noinput
python manage.py seed_data
python manage.py seed_data
python manage.py runserver 0.0.0.0:8000 --noreload
```

*Note: Running `seed_data` twice is completely idempotent, preserving existing operational inventory and never duplicating records.*

---

## 3. Technology Stack

| Layer | Technologies |
|---|---|
| **Language & Runtime** | Python 3.11+ / 3.13 |
| **Backend Framework** | Django 5.2, Django REST Framework 3.15+ |
| **Relational Database** | PostgreSQL (`psycopg 3`, `dj-database-url`), SQLite fallback for offline local dev |
| **LLM Provider & Model** | Google Gemini `gemini-2.0-flash` (via `google-genai`), Groq `llama-3.3-70b-versatile`, OpenAI `gpt-4o-mini`, Anthropic |
| **ML & Time Series** | `pandas`, `numpy`, `scipy` |
| **Frontend Interface** | Modern HTML5 / Tailwind CSS, Lucide Icons, Chart.js, JetBrains Mono & Inter typography |

---

## 4. Environment Variables

Configure environment variables in `.env` (never commit real secrets; refer to [`.env.example`](.env.example)):

```bash
# Database connection (PostgreSQL required for grading)
DATABASE_URL=postgres://postgres:postgres@localhost:5432/dealerdesk

# Local developer fallback flag (allows SQLite when postgres is not installed locally)
ALLOW_SQLITE=1

# Supplier Integration
SUPPLIER_API_URL=http://127.0.0.1:8099
SUPPLIER_API_KEY=dev-supplier-key

# LLM Providers (The grader injects its own provider key)
GEMINI_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GROQ_API_KEY=
```

---

## 5. Architecture & Core Systems

### 5.1 AI Dealer Assistant (`POST /api/assistant/chat/`)
- **Session Memory in PostgreSQL:** Sessions and turns are stored in `AssistantSession` and `AssistantTurn` models, guaranteeing conversational continuity across turn requests without relying on ephemeral in-memory storage or third-party document stores.
- **Tool Calling Loop:**
  1. Receives message, resolves session and active `dealer_id`.
  2. Constructs history context from past PostgreSQL turns.
  3. Dispatches prompt to LLM with function declarations: `search_parts`, `check_stock`, `create_draft_order`.
  4. Intercepts function calls and executes them directly against PostgreSQL tables.
  5. Feeds tool responses back into the model to produce grounded natural language replies.
- **Grounding:** Prices and inventory counts originate exclusively from live database queries executed during that specific turn.
- **Prompt Injection Defense:** Untrusted catalogue `description` text is completely excluded from tool response payloads. The LLM only receives verified structured attributes (`sku`, `name`, `category`, `brand`, `vehicle_fitment`, `price`, `stock`), neutralizing embedded injection attacks like `VIKMO-FREE-90`.
- **Offline / Test Agent Fallback:** If no LLM provider key is configured during local testing, an internal rule-based entity resolver executes the tools against PostgreSQL, formats grounded responses, extracts cited SKUs, and returns the contract schema.

### 5.2 Demand Forecasting & Reorders
- **Target Horizon:** 4 weekly periods: `2026-09-28`, `2026-10-05`, `2026-10-12`, `2026-10-19`.
- **Target Products:** 40 forecast SKUs defined in `data/lead_times.csv`.
- **Output Artifact:** [`ml/forecast.csv`](ml/forecast.csv) (exactly 160 rows).
- **Baselines vs. Candidate Model Evaluation:**
  Evaluated on a 4-week historical holdout (`2026-08-31` to `2026-09-21`) strictly isolating training data (`all_weeks[:-4]`):
  * **Baseline 1 (Last Value / Naive):** MAE = 11.72, WAPE = 34.41%
  * **Baseline 2 (12-Week Moving Average):** MAE = 9.08, WAPE = 26.66%
  * **Baseline 3 (Same Week Last Year / Seasonal Naive):** MAE = 12.27, WAPE = 36.01%
  * **Candidate Model (Weighted Trend + Promo Lift + Diwali Proximity):** MAE = 7.29, **WAPE = 21.41%**
  * *Result:* The candidate model beats the strongest baseline by **5.25 WAPE percentage points**.
- **Reorder Point Formula (`GET /api/reorder-suggestions/`):**
  $$L = \text{lead\_time\_weeks from data/lead\_times.csv}$$
  $$\text{lead\_time\_demand} = \sum_{w=1}^{L} \text{forecast\_units}_w$$
  $$\sigma = \text{sample standard deviation (ddof=1) of most recent 12 weeks of sales\_history.csv}$$
  $$\text{safety\_stock} = \lceil 1.65 \times \sigma \times \sqrt{L} \rceil$$
  $$\text{reorder\_point} = \text{lead\_time\_demand} + \text{safety\_stock}$$
  $$\text{reorder\_qty} = \max(0, \lceil \text{reorder\_point} - \text{current\_stock} \rceil)$$

### 5.3 Order Lifecycle & Concurrency Safeguards
- **State Machine:** `DRAFT` $\to$ `CONFIRMED` $\to$ `DISPATCHED` $\to$ `DELIVERED`. `DRAFT` or `CONFIRMED` can be `CANCELLED`.
- **Atomic Stock Reservation:** `select_for_update()` product row locks prevent overselling under high concurrency.
- **GST Rounding:** 18% GST calculated per line using `ROUND_HALF_UP` to 2 decimal places.

---

## 6. API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/health/` | `GET` | System health check (`200 {"status": "ok"}`) |
| `/api/products/` | `GET`, `POST` | Paginated product listing, case-insensitive search, category & vehicle filter; product creation |
| `/api/products/{sku}/` | `GET`, `PATCH` | Product detail and partial updates (disallows SKU modification) |
| `/api/inventory/{sku}/` | `PUT` | Sets available quantity for SKU (`{"quantity": 25, "reason": "..."}`) |
| `/api/dealers/` | `GET`, `POST` | Paginated dealer network list; dealer registration |
| `/api/dealers/{id}/` | `GET` | Specific dealer profile and credit limit |
| `/api/orders/` | `GET`, `POST` | Create DRAFT order with `Idempotency-Key` validation; paginated order listing |
| `/api/orders/{order_number}/` | `GET`, `PUT` | View order breakdown; edit items (only allowed while DRAFT) |
| `/api/orders/{order_number}/confirm/` | `POST` | Transition DRAFT $\to$ CONFIRMED, atomically deducts stock |
| `/api/orders/{order_number}/dispatch/` | `POST` | Transition CONFIRMED $\to$ DISPATCHED |
| `/api/orders/{order_number}/deliver/` | `POST` | Transition DISPATCHED $\to$ DELIVERED |
| `/api/orders/{order_number}/cancel/` | `POST` | Cancel order (restores stock if previously CONFIRMED) |
| `/api/assistant/chat/` | `POST` | AI assistant conversational turn with native tool calling |
| `/api/forecast/{sku}/` | `GET` | 4-week demand predictions for target SKU |
| `/api/reorder-suggestions/` | `GET` | Live stock reorder recommendations for all 40 SKUs |
| `/api/sync/supplier/` | `POST` | Cursor-paginated supplier sync with retry and rate-limiting handler |

---

## 7. Testing & Verification

### 7.1 Automated Unit & Integration Tests (25 Tests)
Run Django's built-in test suite:
```bash
python manage.py test
```
*Executes model constraint checks, API serialization tests, GST rounding checks, idempotency verification, assistant tool execution, multi-turn dialogue, warehouse availability retrieval, and forecasting math.*

### 7.2 Web Dashboard & Operations Interface
Once the server is running, access the interactive portal in your browser:
- **Operations Dashboard & AI Assistant:** [http://127.0.0.1:8000/dashboard/](http://127.0.0.1:8000/dashboard/)
- **Live REST API Endpoints:** [http://127.0.0.1:8000/api/products/](http://127.0.0.1:8000/api/products/)

### 7.3 Assignment Smoke Test Verification
Ensure the development server is running on port 8000:
```bash
python manage.py runserver 0.0.0.0:8000 --noreload
```

Run the official smoke test suite:
```bash
# Check AI / ML Track:
python smoke_test.py --base-url http://localhost:8000 --track aiml

# Check All Tracks (including Django order engine and supplier sync):
python smoke_test.py --base-url http://localhost:8000 --all
```

---

## 8. Known Limitations

1. **Discrete Intermittent Demand:** For sparse SKUs with >50% zero-demand weeks (`ELE-1006`, `GRP-1043`), continuous exponential weighting predicts small non-zero fractions each week rather than discrete Poisson intervals. Future iterations would implement Croston's Method or SBA.
2. **Dense Vector Embeddings:** Part retrieval currently uses entity extraction, vehicle fitment filtering, and lexical matching. Deploying `pgvector` with dense embeddings would handle mechanic slang and dialect terms better.
3. **LLM Response Streaming:** The assistant currently returns structured JSON responses in complete turns. Adding Server-Sent Events (SSE) would enable typewriter streaming in the web UI.
