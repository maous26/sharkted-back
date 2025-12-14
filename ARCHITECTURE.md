# SharkTed - Architecture Technique

## Vue d'ensemble

SharkTed est une plateforme d'agregation de deals et d'analytics pour le resell de sneakers. L'architecture est distribuee avec un backend FastAPI, des workers RQ pour le scraping, PostgreSQL pour la persistance, Redis pour les queues, et un frontend Next.js.

```
┌─────────────────────────────────────────────────────────────────┐
│                     FRONTEND (Next.js 14)                       │
│                    Railway / sharkted.fr                        │
│  - Landing, Dashboard, Deals, Analytics, Alerts                 │
│  - React Query + Zustand + TailwindCSS                          │
└────────────────────────┬────────────────────────────────────────┘
                         │ HTTPS (axios)
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                    API GATEWAY (FastAPI)                        │
│                    VPS Hostinger :3000                          │
│  - REST API v1                                                  │
│  - JWT Auth + HttpOnly Cookies                                  │
│  - Rate Limiting (Redis)                                        │
└────┬──────────────┬──────────────┬──────────────────────────────┘
     │              │              │
     ▼              ▼              ▼
┌──────────┐  ┌──────────┐  ┌──────────────────┐
│PostgreSQL│  │  Redis   │  │   RQ Workers     │
│  (DB)    │  │ (Queue)  │  │ high/default/low │
└──────────┘  └──────────┘  └──────────────────┘
                                    │
                                    ▼
                            ┌──────────────┐
                            │  Collectors  │
                            │  (Scrapers)  │
                            └──────────────┘
                                    │
                                    ▼
                            ┌──────────────┐
                            │   Sources    │
                            │ Courir, FL,  │
                            │ Size, JD...  │
                            └──────────────┘
```

---

## 1. Backend (`/opt/sharkted-api`)

### Structure des fichiers

```
/opt/sharkted-api/
├── main.py                     # FastAPI app principal
├── worker.py                   # CLI RQ Worker
├── docker-compose.yml          # Orchestration services
├── requirements.txt            # Dependencies Python
│
├── app/
│   ├── models/                 # SQLAlchemy ORM
│   │   ├── deal.py             # Table deals
│   │   ├── user.py             # Table users
│   │   └── source_status.py    # Metriques sources (in-memory)
│   │
│   ├── routers/                # Endpoints FastAPI
│   │   ├── auth.py             # /auth/*
│   │   ├── deals.py            # /v1/deals/*
│   │   ├── sources.py          # /v1/sources/*
│   │   └── collect.py          # /v1/collect/*
│   │
│   ├── services/
│   │   └── deal_service.py     # Logique metier deals
│   │
│   ├── repositories/
│   │   └── deal_repository.py  # Acces DB (upsert, fetch)
│   │
│   ├── collectors/             # Scrapers
│   │   ├── base.py             # BaseCollector
│   │   └── sources/
│   │       ├── courir.py       # Courir (ACTIF)
│   │       ├── footlocker.py   # Footlocker FR (ACTIF)
│   │       ├── size.py         # Size UK (ACTIF)
│   │       ├── jdsports.py     # JD Sports FR (ACTIF)
│   │       └── adidas.py       # Adidas (BLOQUE - Akamai)
│   │
│   ├── normalizers/
│   │   └── item.py             # DealItem (schema normalise)
│   │
│   ├── core/
│   │   ├── config.py           # Configuration JWT
│   │   ├── security.py         # Hashing + tokens
│   │   ├── source_policy.py    # Politiques d'escalade
│   │   ├── rate_limiter.py     # Rate limiting
│   │   └── url_validator.py    # Anti-SSRF
│   │
│   ├── jobs.py                 # Jobs generiques
│   ├── jobs_courir.py          # Job Courir
│   ├── jobs_footlocker.py      # Job Footlocker
│   ├── jobs_size.py            # Job Size
│   └── jobs_jdsports.py        # Job JD Sports
│
└── alembic/                    # Migrations DB
    └── versions/
```

### Schema Base de Donnees

#### Table `users`
| Colonne | Type | Contraintes |
|---------|------|-------------|
| id | INTEGER | PRIMARY KEY |
| email | VARCHAR | UNIQUE, NOT NULL |
| password_hash | VARCHAR | NOT NULL |

#### Table `deals`
| Colonne | Type | Description |
|---------|------|-------------|
| id | INTEGER | PRIMARY KEY |
| source | VARCHAR(50) | courir, footlocker, size, jdsports |
| external_id | VARCHAR(255) | SKU produit source |
| title | VARCHAR(500) | Nom du produit |
| price | FLOAT | Prix actuel |
| currency | VARCHAR(10) | EUR, GBP |
| url | TEXT | Lien produit |
| image_url | TEXT | Image produit |
| original_price | FLOAT | Prix avant promo |
| discount_percent | FLOAT | % reduction |
| in_stock | BOOLEAN | Disponibilite |
| score | FLOAT | FlipScore (0-100) |
| raw_data | JSON | Donnees brutes |
| first_seen_at | DATETIME | Premier scrape |
| last_seen_at | DATETIME | Dernier scrape |
| price_updated_at | DATETIME | Changement prix |

**Index unique**: `(source, external_id)`

### Endpoints API

#### Authentication
| Methode | Endpoint | Description |
|---------|----------|-------------|
| POST | `/auth/register` | Inscription |
| POST | `/auth/login` | Connexion (JWT + Cookie) |
| POST | `/auth/logout` | Deconnexion |
| GET | `/me` | Profil utilisateur |

#### Deals (Lecture)
| Methode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/v1/deals` | Liste avec filtres & pagination |
| GET | `/v1/deals/recent` | Deals des 24h |
| GET | `/v1/deals/stats` | Stats par source |
| GET | `/v1/deals/{source}/{id}` | Detail d'un deal |

#### Sources (Monitoring)
| Methode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/v1/sources/status` | Status toutes sources |
| GET | `/v1/sources/{source}/status` | Status une source |
| POST | `/v1/sources/{source}/unblock` | Debloquer source |

#### Collection (Enqueue Jobs)
| Methode | Endpoint | Auth | Description |
|---------|----------|------|-------------|
| POST | `/v1/collect/courir/product` | JWT | Scraper Courir |
| POST | `/v1/collect/footlocker/product` | JWT | Scraper Footlocker |
| POST | `/v1/collect/size/product` | JWT | Scraper Size |
| POST | `/v1/collect/jdsports/product` | JWT | Scraper JD Sports |

---

## 2. Systeme de Queues (RQ + Redis)

### Architecture des Queues

```
Redis (6379)
├── Queue: high      ← Users Premium (traitement prioritaire)
├── Queue: default   ← Jobs standards
└── Queue: low       ← Users Free (rate-limite)

Workers (Docker):
├── worker_high     → Consomme queue "high"
├── worker_default  → Consomme queue "default"
├── worker_low      → Consomme queue "low"
└── scheduler       → Jobs planifies (rqscheduler)
```

### Flow d'un Job

```
1. User POST /v1/collect/courir/product?url=...
   │
2. Validation URL (anti-SSRF) + Rate limit
   │
3. Determine queue (premium → high, free → low)
   │
4. redis.enqueue(collect_courir_product, url)
   │
5. Worker dequeue et execute:
   ├── cloudscraper.get(url)
   ├── Parse JSON-LD / HTML
   ├── Normalize → DealItem
   └── Persist → DB (upsert)
   │
6. Return: {job_id, status, item, persistence}
```

---

## 3. Collectors (Scrapers)

### Sources Actives

| Source | Status | Mode | Methode |
|--------|--------|------|---------|
| Courir | ACTIF | DIRECT | cloudscraper + JSON-LD |
| Footlocker FR | ACTIF | DIRECT | cloudscraper + JSON-LD |
| Size UK | ACTIF | DIRECT | cloudscraper + JSON-LD |
| JD Sports FR | ACTIF | DIRECT | cloudscraper + JSON-LD |

### Sources Bloquees

| Source | Raison | Solution |
|--------|--------|----------|
| Adidas | Akamai 403 | Proxy residentiel requis |
| Zalando | Cloudflare | Proxy residentiel requis |
| Sarenza | Anti-bot | Proxy residentiel requis |
| Snipes | SPA JavaScript | Playwright requis |

### Politique d'Escalade

```
DIRECT (cloudscraper rapide)
    ↓ si 403/429 x3
DIRECT_SLOW (warmup: home → categorie → produit)
    ↓ si echec persiste
PROXY (rotation IPs - non implemente)
    ↓ si SPA detecte
BROWSER (Playwright - non implemente)
    ↓ si echec x6
BLOCKED (desactive 30 min)
```

### Pattern Collector

```python
class BaseCollector:
    source: str

    def fetch(url) -> str:
        # HTTP request avec cloudscraper

    def parse(html) -> DealItem:
        # Extraction JSON-LD ou HTML

    def run(url) -> DealItem:
        html = self.fetch(url)
        return self.parse(html)
```

---

## 4. Frontend (`/opt/sharkted-front/apps/web`)

### Structure

```
/opt/sharkted-front/apps/web/
├── app/                        # App Router Next.js 14
│   ├── page.tsx                # Landing page
│   ├── auth/
│   │   ├── login/              # Connexion
│   │   └── register/           # Inscription
│   └── dashboard/
│       ├── page.tsx            # Dashboard principal
│       ├── deals/              # Liste des deals
│       ├── alerts/             # Gestion alertes
│       ├── analytics/          # Graphiques
│       └── settings/           # Preferences
│
├── components/
│   ├── deals/                  # DealCard, DealTable, Filters
│   ├── charts/                 # Recharts components
│   └── ui/                     # Button, Card, Input, Badge
│
├── lib/
│   ├── api.ts                  # Client Axios + interceptors
│   └── utils.ts                # Helpers
│
└── hooks/
    ├── use-deals.ts            # React Query hooks
    └── use-auth.ts             # Zustand auth store
```

### Stack Technique

| Technologie | Usage |
|-------------|-------|
| Next.js 14 | Framework React SSR |
| React Query | Data fetching + cache |
| Zustand | State management |
| TailwindCSS | Styling |
| Recharts | Graphiques |
| Axios | HTTP client |
| Lucide | Icons |

### Client API

```typescript
// lib/api.ts
const api = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL,
  withCredentials: true
});

// Modules:
authApi.{login, register, logout}
dealsApi.{list, get, getTopRecommended}
alertsApi.{list, markRead, stats}
analyticsApi.{dashboard, brands, trends}
```

---

## 5. Infrastructure Docker

### docker-compose.yml

```yaml
services:
  api:
    build: .
    ports: ["3000:3000"]
    environment:
      - DATABASE_URL=postgresql://...
      - REDIS_URL=redis://redis:6379/0
      - JWT_SECRET=...

  db:
    image: postgres:16
    volumes: [pgdata:/var/lib/postgresql/data]

  redis:
    image: redis:7-alpine
    ports: ["127.0.0.1:6379:6379"]

  worker_high:
    build: .
    command: python worker.py high

  worker_default:
    build: .
    command: python worker.py default

  worker_low:
    build: .
    command: python worker.py low

  scheduler:
    build: .
    command: rqscheduler --url redis://... --interval 5
```

### Deploiement

| Service | Plateforme | URL |
|---------|------------|-----|
| Frontend | Railway | sharkted.fr |
| API | VPS Hostinger | 72.62.90.196:80 |
| DB | Docker (VPS) | localhost:5432 |
| Redis | Docker (VPS) | localhost:6379 |
| Workers | Docker (VPS) | - |

---

## 6. Securite

### Authentification

- **JWT** avec HS256, expire 24h
- **HttpOnly Cookie** pour XSS protection
- **SameSite=Lax** pour CSRF protection

### Rate Limiting

| Endpoint | Limite |
|----------|--------|
| /auth/login | 5/min par IP |
| /auth/register | 3/min par IP |
| /v1/collect/* | 10/min par user |

### Validation

- **Anti-SSRF** : Whitelist domaines autorises
- **URL Scheme** : http/https uniquement
- **Email** : Validation Pydantic EmailStr

---

## 7. Data Flow Complet

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. USER INITIE COLLECTION                                       │
└─────────────────────────────────────────────────────────────────┘
     │
     ▼
POST /v1/collect/courir/product?url=https://courir.com/produit.html
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. API VALIDATION                                               │
│    - Rate limit check                                           │
│    - URL validation (anti-SSRF)                                 │
│    - User auth (JWT)                                            │
│    - Queue selection (premium → high)                           │
└─────────────────────────────────────────────────────────────────┘
     │
     ▼
redis.enqueue("high", collect_courir_product, url)
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. WORKER EXECUTION                                             │
│    - cloudscraper.get(url)                                      │
│    - Parse JSON-LD                                              │
│    - Extract: name, price, image, sku                           │
│    - Normalize → DealItem                                       │
└─────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. PERSISTENCE                                                  │
│    - Check if (source, external_id) exists                      │
│    - INSERT or UPDATE deal                                      │
│    - Track price changes                                        │
└─────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. FRONTEND DISPLAY                                             │
│    - GET /v1/deals                                              │
│    - React Query cache                                          │
│    - DealCard / DealTable render                                │
└─────────────────────────────────────────────────────────────────┘
```

---

## 8. Metriques & Monitoring

### Logs Structures (JSON)

```json
{
  "timestamp": "2025-01-13T15:45:23Z",
  "level": "INFO",
  "trace_id": "req-xyz789",
  "message": "collect_success",
  "source": "courir",
  "duration_ms": 2345
}
```

### Evenements Traces

- `request_completed` - Requete HTTP
- `collect_start` - Debut job
- `collect_success` - Succes scrape
- `collect_error` - Echec scrape
- `persist_success` - Deal sauvegarde

---

## 9. Evolutions Futures

### Court terme
- [ ] Implementer FlipScore (marge estimee)
- [ ] Alertes Discord webhooks
- [ ] Scraping planifie (cron)

### Moyen terme
- [ ] Proxy rotatif pour sources bloquees
- [ ] Playwright pour SPA (Snipes)
- [ ] Historique des prix

### Long terme
- [ ] ML pour prediction marges
- [ ] Integration Vinted API
- [ ] WebSocket temps reel

---

## 10. Commandes Utiles

```bash
# Demarrer l'infra
cd /opt/sharkted-api
docker compose up -d

# Logs API
docker logs -f sharkted-api

# Logs workers
docker logs -f sharkted-worker-high

# Migrations DB
docker exec -it sharkted-api alembic upgrade head

# Test API
curl http://72.62.90.196/health

# Redis CLI
docker exec -it sharkted-redis redis-cli
```

---

*Document genere le 13 decembre 2025*
