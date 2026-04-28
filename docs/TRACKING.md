# TRACKING — AI Assistant Working Document

> **Purpose:** Personal reference for AI assistant to maintain context across sessions.  
> **Last updated:** 2026-04-28

---

## 1. Project Overview

**Lambda Architecture for TradingView-Style Platform** — real-time crypto price monitoring and charting platform using Lambda Architecture (speed + batch + serving layers).

- **Repo:** `StupidDuck64/Lambda-Architecture-for-TradingView-Style-Platform`
- **Latest commit:** `0802fe0` (main)
- **Deployment target:** AWS t3a.2xlarge (8 vCPU, 32GB RAM, 100GB gp3)
- **Docker services:** 21 containers via `docker-compose.yml`
- **Data sources:** Binance WebSocket API (~400 USDT pairs)

📄 **For full technical details, see [DOCUMENTATION.md](./DOCUMENTATION.md)**  

### Quick Reference — Tech Stack

| Layer | Technology | Version |
|---|---|---|
| Message broker | Apache Kafka (KRaft) | 3.9.0 |
| Schema registry | Apicurio | 2.6.2 |
| Stream processing | Apache Flink (PyFlink) | 1.18.1 |
| Batch processing | Apache Spark | 3.5 |
| Hot cache | KeyDB | latest |
| Time-series DB | InfluxDB | 2.7 |
| Cold storage | Iceberg + MinIO | 1.5.2 + latest |
| Federated query | Trino | 442 |
| Orchestration | Dagster | latest |
| API server | FastAPI + Uvicorn | 0.115+ |
| Frontend | React 19 + lightweight-charts | v5.1.0 |
| CSS framework | TailwindCSS | 3.4.4 |
| Language | TypeScript (strict) | 5.7+ |
| Build tool | Vite | 6.4 |
| Reverse proxy | Nginx | 1.27 |
| Metadata DB | PostgreSQL | 16 |

### Quick Reference — Key File Sizes

| File | Lines | Role |
|---|---|---|
| `src/processing/pipeline.py` | ~210 | Flink job entry point (writers are split into modules) |
| `src/batch/backfill.py` | ~510 | Multi-mode backfill (Spark/direct) |
| `src/producer/main.py` | ~260 | Exchange-agnostic WS → Kafka producer |
| `frontend/src/components/CandlestickChart.tsx` | ~1020 | Main chart component (TypeScript) |
| `frontend/src/services/marketDataService.ts` | ~388 | Frontend API service layer |
| `backend/services/candle_service.py` | ~280 | Core OHLCV business logic (shared) |
| `backend/api/klines.py` | ~170 | OHLCV REST endpoint (thin handler) |
| `backend/api/historical.py` | ~90 | Historical range queries |
| `backend/api/websocket.py` | ~135 | WebSocket real-time stream |
| `src/lakehouse/pipeline.py` | ~220 | Spark Streaming → Iceberg |
| `frontend/src/App.tsx` | ~240 | Main React app layout |
| `src/batch/maintenance.py` | ~130 | Iceberg compaction |
| `src/batch/aggregate.py` | ~120 | Spark 1m→1h aggregation |
| `orchestration/assets.py` | 151 | Dagster assets + schedules |

### Quick Reference — Directory Layout

```
project-root/
├── backend/                   # FastAPI API layer (MVC architecture)
│   ├── app.py                 # App entry point, router registration
│   ├── api/                   # Route handlers (thin controllers)
│   │   ├── health.py, klines.py, historical.py, websocket.py
│   │   ├── ticker.py, orderbook.py, trades.py, symbols.py, indicators.py
│   ├── core/                  # Config, constants, database connections
│   │   ├── config.py, constants.py, database.py
│   ├── services/              # Business logic layer
│   │   └── candle_service.py    # Shared OHLCV logic (validate, aggregate, query)
│   └── models/                # Pydantic response models
│       ├── candle.py, ticker.py, health.py
├── src/                       # Data processing layer
│   ├── common/                # Shared config, Kafka, Avro, logging
│   ├── exchanges/             # Exchange abstraction (binance/ implementations)
│   ├── producer/              # Kafka quad-stream producer
│   ├── processing/            # Flink pipeline and writers
│   ├── lakehouse/             # Spark structured streaming to Iceberg
│   └── batch/                 # Historical backfill and maintenance jobs
├── frontend/                  # React 19 SPA (Vite + TypeScript)
│   └── src/
│       ├── components/        # 16 components (.tsx) + chart/ subdir
│       │   └── chart/         # chartConstants.ts, indicatorUtils.ts, OHLCVBar.tsx, etc.
│       ├── services/          # marketDataService.ts, symbolMetaService.ts
│       ├── hooks/             # useApiCall.ts, useSymbolMeta.ts
│       ├── contexts/          # AuthContext.tsx
│       ├── i18n/              # translations.ts, index.tsx (~130 keys, en + vi)
│       ├── types/             # index.ts (shared TS interfaces)
│       ├── data/              # fallbackSymbolMeta.ts (~90 symbols)
│       └── utils/             # storageHelpers.ts, errors.ts
├── orchestration/             # Dagster assets + workspace.yaml
├── schemas/                   # 4 Avro schemas (ticker, kline, trade, depth)
├── config/                    # spark-defaults.conf
├── docker/                    # Dockerfiles (9 subdirs)
├── scripts/                   # Shell scripts
├── tests/                     # pytest (unit/, integration/, e2e/, security/, performance/)
├── docs/                      # Documentation
├── docker-compose.yml         # 21 services (base)
├── docker-compose.override.yml # Dev mode overrides
├── docker-compose.prod.yml    # Production overrides
├── Makefile                   # Dev/prod/test convenience targets
├── pyproject.toml             # Pytest config
└── .env.example               # Environment template
```

---

## 2. Operating Principles

> These are the rules I must follow when making changes to the project. The owner can modify these at any time.

### 2.1 General Rules

1. **Always update this TRACKING.md** after completing any task — add to changelog, update file sizes if changed, note any new patterns or gotchas discovered.
2. **Read this file first** at the start of every session to re-establish context.
3. **Preserve existing comments and docstrings** unless the user explicitly says to modify them.
4. **Follow existing code patterns** — match the style, naming conventions, and structure already in use.
5. **Incremental refactoring** — refactoring is crucial but can break functional code. Treat it step-by-step and double-check to ensure new code runs as performant or better without breaking anything major.

### 2.2 Code Style & Patterns

1. **Python (backend):** Follow existing patterns in the codebase:
   - MVC architecture in `backend/` (`api/`, `services/`, `models/`, `core/`)
   - Route handlers in `backend/api/` should be thin, delegating logic to `backend/services/`
   - Data validation and response serialization using Pydantic models in `backend/models/`
   - Singleton connections in `backend/core/database.py`
   - Environment variables read from `backend/core/config.py`
   - Flux queries for InfluxDB, SQL for Trino
   - Avro serialization for Kafka (Confluent wire format)
2. **TypeScript (frontend):** Follow existing patterns:
   - React 19 functional components with hooks (`.tsx` extension, strict TypeScript)
   - Vite 6 for build tooling (`import.meta.env` for environment variables)
   - All props and state must have explicit TypeScript interfaces
   - TailwindCSS for styling
   - lightweight-charts v5.1.0 API (use `any` refs for series instances)
   - `marketDataService.ts` as the single API service layer
   - `symbolMetaService.ts` for dynamic symbol metadata (CoinGecko + localStorage 24h cache)
   - Centralized error handling via `useApiCall` hook + `ToastProvider`
   - Full i18n via `useI18n()` hook — no hardcoded user-facing strings
   - Time convention: lightweight-charts uses seconds, API uses milliseconds
   - Shared types live in `src/types/index.ts`
3. **Docker:** Changes to services must be reflected in `docker-compose.yml`. Use existing build patterns. For local development, prefer using `make dev` and `make prod` workflows.

### 2.3 Language

- **Documentation** is written in Vietnamese (the project owner's preference). Match the existing language.
- **Code comments** can be in English or Vietnamese, matching what's already in each file.
- **Commit messages** are in English.

### 2.4 Testing & Verification

1. Before finalizing changes, verify the logic is consistent across layers (e.g., Flink writer → KeyDB key → FastAPI reader → Frontend consumer).
2. If modifying API endpoints, ensure frontend `marketDataService.ts` is updated accordingly.
3. If changing docker-compose, verify dependencies and health checks are correct.

### 2.5 Key Gotchas to Remember

1. **Time units:** lightweight-charts uses seconds, all backend APIs use milliseconds. Frontend converts: `openTime / 1000 → time`.
2. **Timeframe casing:** Frontend uses uppercase `1H`, `4H`, `1D`, `1W` but `.toLowerCase()` before API calls.
3. **KeyDB dedup:** Sorted sets deduplicate by `(score, member)` pair — always `ZREMRANGEBYSCORE` before `ZADD` for klines.
4. **Ticker staleness:** `!ticker@arr` has 14–30s delay. Only use ticker to enrich candle close if `ticker.event_time > last_1s_candle.kline_start`.
5. **WS vs Poll coordination:** WS is authoritative for live bar (1m+), poll should skip the last candle to avoid flicker.
6. **InfluxDB scroll-left:** Must use absolute `range(start: RFC3339, stop: RFC3339)` for `endTime` queries, not relative `range(start: -Nh)`.
7. **Flink safety timer:** Must cancel old timer before registering new one (KlineWindowAggregator).
8. **Frontend chart re-render:** Use `.update()` for single bar updates, `.setData()` only for bulk operations (initial load, scroll-left merge).
9. **Producer WebSocket limit:** Max 200 symbols per WS connection to avoid Binance 502.

### 2.6 Rebuild Commands After Code Changes

| Changed | Command |
|---|---|
| `backend/` | `make dev` or `docker compose up -d --build fastapi` |
| `frontend/` | `make dev` or `docker compose up -d --build nginx` |
| `src/` (Flink job) | Cancel running Flink job, re-submit via REST |
| `src/` (Spark job) | Re-submit via `spark-submit` |
| `docker/` | `docker compose up -d --build <service>` |
| Test execution | `make test` or `make test-all` |

---

## 3. Current State & Notes

### Known Stable Commit
- `0802fe0` — Latest known working commit before refactoring.

### Active Configuration
- Flink parallelism: 1
- Flink TaskManager slots: 2
- TaskManager memory: 6144m (cap 7168m)
- KeyDB maxmemory: 2560mb
- InfluxDB 1m retention: 90 days
- KeyDB 1s TTL: 8h (28800s)
- KeyDB 1m TTL: 7d (604800s)
- Kafka retention: 48h
- Dagster schedules: daily 04:00 (aggregate), weekly Sunday 03:00 (iceberg maintenance)
- HTTPS: Let's Encrypt via certbot + DuckDNS dynamic DNS

### Frontend Component Tree (current)
```
App.tsx (TradingDashboard)
├── ErrorBoundary.tsx
├── ToastProvider.tsx
├── I18nProvider (i18n/index.tsx)
├── AuthContext.Provider (contexts/AuthContext.tsx)
├── Header.tsx
│   ├── Navigation drawer
│   └── LanguageSwitcher.tsx
├── DrawingToolbar.tsx
│   └── ToolSettingsPopup.tsx
├── CandlestickChart.tsx (~1020 lines — CORE)
│   ├── MarketSelector.tsx (+ useSymbolMeta)
│   ├── DateRangePicker.tsx
│   ├── chart/IndicatorPanel.tsx
│   ├── chart/OHLCVBar.tsx
│   ├── chart/OscillatorPane.tsx
│   ├── chart/chartConstants.ts
│   ├── chart/indicatorUtils.ts
│   ├── ChartOverlay.tsx (drawings)
│   ├── OrderBook.tsx
│   └── RecentTrades.tsx
├── Watchlist.tsx (+ useSymbolMeta)
├── OverviewChart.tsx (+ useSymbolMeta)
├── SystemHealthCard.tsx
└── AuthModal.tsx
```

### Frontend Type System
```
src/types/index.ts:
  Candle, RawCandle, Ticker, OrderBookEntry, OrderBookData, Trade,
  SymbolInfo, SymbolMeta, WatchlistItem, HistoricalRange, HealthData,
  IndicatorSettings, DrawingPoint, Drawing, TooltipData,
  UserSession, AuthResult, Timeframe, WatchlistFilter
```

### Frontend Build Output
```
dist/index.html                   0.74 kB │ gzip:   0.40 kB
dist/assets/index-*.css          22.77 kB │ gzip:   4.77 kB
dist/assets/index-*.js          471.79 kB │ gzip: 146.62 kB
```

---

## 4. Changelog

All changes made by AI assistant, in reverse chronological order.

### 2026-04-28 — Session 5: Frontend TypeScript Migration

**Task:** Complete migration of the frontend from JavaScript/JSX to TypeScript/TSX. Upgrade React 18→19. Add centralized error handling, dynamic symbol metadata service, and full i18n coverage.

**Changes:**
1. **TypeScript Toolchain** — Added `tsconfig.json` (strict mode), `tsconfig.node.json`, `vite-env.d.ts`. Updated `package.json` with React 19, TypeScript 5.7+, `@types/react` 19.
2. **Type Definitions (`src/types/index.ts`)** — Created 18 shared interfaces: `Candle`, `Ticker`, `Drawing`, `IndicatorSettings`, `HistoricalRange`, etc.
3. **Core Service Migration** — `marketDataService.js` → `.ts`, `storageHelpers.js` → `.ts`, `translations.js` → `.ts` (TranslationKey type), `AuthContext.jsx` → `.tsx`, `i18n/index.jsx` → `.tsx`.
4. **Centralized Error Handling** — Created `src/utils/errors.ts` (AppError hierarchy), `src/hooks/useApiCall.ts` (generic fetcher with retry), `src/components/ToastProvider.tsx` (toast notification system). Wired into `index.tsx`.
5. **Dynamic Symbol Metadata** — Created `src/services/symbolMetaService.ts` (CoinGecko API + 24h localStorage cache), `src/data/fallbackSymbolMeta.ts` (~90 symbols), `src/hooks/useSymbolMeta.ts`. Integrated into MarketSelector, OverviewChart, Watchlist.
6. **Component Migration (20 files)** — Migrated all 16 components + 4 chart sub-modules from `.jsx`/`.js` to `.tsx`/`.ts`. Added typed props, state, and refs throughout. Key files: CandlestickChart (~1020 lines), ChartOverlay (~430 lines).
7. **i18n Completion** — Added ~50 new translation keys (en + vi). Replaced all hardcoded English and Vietnamese strings with `t()` calls. Total: ~130 keys.
8. **Entry Points** — `index.jsx` → `index.tsx`, `App.jsx` → `App.tsx`.
9. **Nginx** — Updated asset caching from `/static/` to `/assets/` (Vite output path). Added font/image caching rules.
10. **Build Verification** — `tsc --noEmit` = 0 errors. `vite build` succeeds (471.79 kB JS gzipped to 146.62 kB).

**Deleted files:** All `.jsx` and `.js` source files in `frontend/src/` (replaced by `.tsx`/`.ts` counterparts).

**Notes/Gotchas discovered:**
- lightweight-charts v5 `lineWidth` only accepts integer union `1 | 2 | 3 | 4`, not floats like `1.5`. Must cast.
- lightweight-charts `time` must be cast to `UTCTimestamp` when passing raw `number` values.
- `tsconfig.json` `references` to `tsconfig.node.json` requires `composite: true` on the referenced config, which conflicts with `noEmit: true`. Simplest fix: remove the reference since it's only for `vite.config.ts`.
- `IndicatorSettings` should be a flexible per-indicator interface (with index signature) rather than a rigid top-level structure, since components access it as `Record<string, IndicatorSettings>`.

**Impact:**
- Frontend: Complete rewrite — 27 files migrated, 7 new files created, all legacy deleted.
- Infrastructure: nginx.conf asset caching updated.
- Docs: TRACKING.md and DOCUMENTATION.md updated.

### 2026-04-25 — Session 4: Data Processing Layer Refactoring

**Task:** Refactor monolithic `src/` folder into a clean modular architecture, preparing for future multi-exchange support.

**Changes:**
1. **Exchange Abstraction (`src/exchanges/`)** — Created `ExchangeClient` base class and `BinanceClient` implementation. Replaced hardcoded Binance WS/REST endpoints.
2. **Shared Infrastructure (`src/common/`)** — Centralized `config.py` (eliminated 15+ duplicated env blocks), extracted thread-safe `kafka_client.py` and `avro_serializer.py`.
3. **Producer Service (`src/producer/`)** — Rewrote the 632-line monolith into a ~250-line exchange-agnostic orchestrator. Upgraded container to Python 3.14.
4. **Flink Pipeline (`src/processing/`)** — Split the 996-line monolith into `pipeline.py` + 7 individual writer modules (e.g., `keydb_ticker.py`, `kline_aggregator.py`).
5. **Batch Jobs (`src/batch/`)** — Renamed and refactored maintenance and backfill jobs. Translated Vietnamese docstrings to English in `backfill.py`.
6. **Lakehouse (`src/lakehouse/`)** — Cleaned up the Spark structured streaming pipeline.
7. **Infrastructure Updates** — Updated `orchestration/assets.py`, `scripts/auto_submit_jobs.sh`, and `docker-compose.yml` to reflect new paths.

**Notes/Gotchas discovered:**
- When refactoring PyFlink streams, writer logic inside `FlatMapFunction` or `KeyedProcessFunction` MUST read environment variables natively inside the `open()` method, as importing module-level envs from other files causes serialization issues across the Flink cluster.

**Impact:**
- Data Processing: Complete restructuring of `src/` from 7 monolithic files to 20+ cleanly separated modules.
- Infra: Docker path updates.



### 2026-04-25 — Session 3: CRA to Vite Migration & Python 3.14 Upgrade

**Task:** Migrate frontend from Create React App to Vite, upgrade backend Python from 3.11 to 3.14, and update TRACKING principles.

**Changes:**
1. **Frontend Migration:** Removed `react-scripts`, added `vite` and `@vitejs/plugin-react`. Renamed all 21 React component files from `.js` to `.jsx`. Updated `package.json`, created `vite.config.js`, moved `index.html` to root, converted tailwind/postcss configs to ESM. Updated environment variables to `VITE_` prefix and `import.meta.env`. Build time improved significantly (~2.5s).
2. **Backend Upgrade:** Updated FastAPI Dockerfile to `python:3.14-slim`. Cleaned up `from __future__ import annotations` across the backend files while maintaining compatibility for Pydantic models with `Optional[]` type hints.
3. **Docs Update:** Updated `docs/TRACKING.md` principles to reflect `backend/` MVC architecture, Vite frontend, and Docker Make command patterns.

**Notes/Gotchas discovered:**
- When migrating to Vite, explicitly renaming files containing JSX to `.jsx` is required for Vite's esbuild transform to work without extra configuration.
- Local tests use Python 3.9 where `X | None` syntax throws an error on type hints without `from __future__ import annotations`. However, Pydantic evaluates annotations at class definition time, so `Optional[float]` must be used instead for models.

**Impact:**
- Frontend: `package.json`, Vite configs, component extensions, Dockerfile path.
- Backend: Dockerfile base image, removed 6 redundant imports.
- Docs: Updated TRACKING.md principles.

### 2026-04-25 — Session 1: Initial Setup

**Task:** Create TRACKING.md and update DOCUMENTATION.md

**Changes:**
1. **Created `docs/TRACKING.md`** (this file) — AI assistant working document with:
   - Project overview & quick references
   - Operating principles and rules
   - Current state snapshot
   - Changelog section
2. **Updated `.gitignore`** — Added `docs/TRACKING.md` to exclusion list
3. **Updated `docs/DOCUMENTATION.md`** — Refreshed to match current project state (v2.1):
   - Updated file line counts to actual current values
   - Updated frontend component tree (added SystemHealthCard, OscillatorPane)
   - Updated Flink TaskManager memory config to match docker-compose (7168m cap)
   - Updated version info and last-updated date
   - Added note about HTTPS automation (certbot, DuckDNS)

### 2026-04-25 — Session 2: Full Project Refactoring

**Task:** Comprehensive project refactoring across 8 batches

**Changes:**
1. **Batch 1: Project Structure** — Migrated `serving/` → `backend/` (MVC: api/, services/, models/, core/). Updated Dockerfile and docker-compose.yml. Deleted debug artifacts, dead candle-aggregator block, old test files.
2. **Batch 2: Backend MVC** — Created `core/constants.py` (DRY), Pydantic models (candle, ticker, health), `services/candle_service.py` (280 lines of shared logic). Replaced urllib with httpx. Extracted health endpoint.
3. **Batch 3: Frontend Cleanup** — Removed all tech-stack references (Iceberg, Trino, FastAPI, Nginx, Flink) from UI text and comments. Sanitized SystemHealthCard dependency names.
4. **Batch 4: Dev/Prod Switching** — Created docker-compose.override.yml (dev), docker-compose.prod.yml (prod), Makefile with 11 targets.
5. **Batch 5: Docker Optimization** — Added deploy.resources.limits.memory to all 14 services. Pinned Python dependencies.
6. **Batch 6: Testing** — Created pytest framework (pyproject.toml, conftest.py, 5 test directories). Wrote 40 tests (20 unit, 9 model, 9 security + 2 extras). Fixed Python 3.9 compatibility.
7. **Batch 7: Security** — Added nginx rate limiting (30r/s API, 5r/s WS), security headers (HSTS, X-Content-Type-Options, X-Frame-Options, X-XSS-Protection, Referrer-Policy), request size limits.
8. **Batch 8: Documentation** — Updated DOCUMENTATION.md and TRACKING.md to reflect new architecture.

**Notes/Gotchas discovered:**
- Python 3.9 on macOS doesn't support `X | None` syntax — needs `from __future__ import annotations` for runtime, but Pydantic v2 evaluates annotations at class definition time, so `Optional[float]` is required for Pydantic models specifically.
- `from __future__ import annotations` works fine for all non-Pydantic files (FastAPI Query params still work because FastAPI evaluates them differently).
- docker-compose.yml `mem_limit` is deprecated — use `deploy.resources.limits.memory` instead.

**Impact:**
- Backend: complete restructure (22 Python files)
- Frontend: comment/text cleanup (4 files)
- Infrastructure: 5 new files (Makefile, docker-compose.override.yml, docker-compose.prod.yml, pyproject.toml, requirements-test.txt)
- Testing: 40 passing tests across 3 test files
- Docs: updated 2 files

---

<!-- TEMPLATE FOR FUTURE ENTRIES:

### YYYY-MM-DD — Session N: [Title]

**Task:** [What was requested]

**Changes:**
1. **[File/Component]** — [What changed and why]
2. ...

**Notes/Gotchas discovered:**
- [Any new patterns, bugs, or things to remember]

**Impact:**
- [Which layers were affected: backend/frontend/infra/docs]

-->
- **April 25, 2026**: Merged `SYSTEM_ARCHITECTURE.md` into `DOCUMENTATION.md`. Created a new user-friendly `README.md` with badges and quick start guide.
