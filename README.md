# Skladová evidence (Inventory Tracker)

Třívrstvá aplikace pro správu skladu: **frontend** (Flask + Jinja, server-side rendering) → **backend REST API** (Flask + SQLAlchemy, gunicorn) → **PostgreSQL**.
Inicializaci schématu dělá jednorázový kontejner **setup** (v Kubernetes z něj bude `Job`).

```
            frontend_network                 backend_network
 browser ──► frontend:5000 ──► backend:5000 ──────► postgresqldb:5432
  :8080                          ▲                        ▲  (named volume db-data)
                                 │                        │
                                 └──── setup (jednorázově: schéma + seed)
```

## Struktura

```
.devcontainer/
├── docker-compose.yml       # lokální běh (sítě, named volume, env)
├── .env / .env.example      # parametry (DB přístupy, registry, porty)
├── Dockerfile.backend       # python:3.12-slim + gunicorn, non-root, HEALTHCHECK
├── Dockerfile.frontend      # python:3.12-slim + gunicorn, non-root, HEALTHCHECK
├── Dockerfile.setup         # postgres:17-alpine (psql) + SQL skripty
├── Dockerfile.devel         # vývojový kontejner pro devcontainer
└── src/
    ├── python/backend/      # REST API (backend.py, models.py)
    ├── python/frontend/     # UI (frontend.py, templates/, static/)
    └── setup/               # create_schema.sql, populate_schema.sql, setup.sh
```

## Funkcionalita

- **CRUD položek** – SKU, název, popis, minimální zásoba, cena.
- **Hlídání limitních zásob** – stav se počítá z `quantity` a `min_quantity`:
  - `OUT_OF_STOCK` … `quantity = 0`
  - `LOW_STOCK` … `0 < quantity <= min_quantity`
  - `OK` … jinak

  Na frontendu barevné odznaky/řádky, souhrnné dlaždice a filtr **K doobjednání** (`LOW_STOCK` + `OUT_OF_STOCK`).
- **Auditní log pohybů** – počet kusů se mění *jen* přes pohyb (naskladnění/vyskladnění). Pohyb běží v jedné transakci
  se zamčeným řádkem (`SELECT … FOR UPDATE`), odmítne záporný stav (HTTP 409) a zapíše záznam do `stock_movements`
  (čas, delta, stav po pohybu, důvod). Záznamy přežijí smazání položky.
- **Healthchecky** – `/health/live` (proces běží, bez DB) a `/health/ready` (`/health`) ověřující spojení do DB;
  připraveno pro liveness/readiness sondy.

## Obrazy v Docker Hubu

Hotové obrazy jsou veřejně v registru Docker Hub (účet `mvelc`):

| Služba | Obraz |
|---|---|
| backend | [`docker.io/mvelc/inventory-backend:latest`](https://hub.docker.com/r/mvelc/inventory-backend) |
| frontend | [`docker.io/mvelc/inventory-frontend:latest`](https://hub.docker.com/r/mvelc/inventory-frontend) |
| setup | [`docker.io/mvelc/inventory-setup:latest`](https://hub.docker.com/r/mvelc/inventory-setup) |
| postgresqldb | oficiální `postgres:17` (nepushuje se) |

## Spuštění (z Docker Hubu)

Potřeba je jen Docker s Compose v2 (`docker compose version`).

```bash
git clone https://github.com/matyasvelc/DCE1.git
cd DCE1/.devcontainer
docker compose pull postgresqldb setup backend frontend   # stáhne obrazy z Docker Hubu
docker compose up -d frontend                              # spustí db → setup → backend → frontend
```

Compose dodrží pořadí: `postgresqldb` (healthy) → `setup` (proběhne a skončí s kódem 0) → `backend` (healthy) → `frontend`.
Parametry (přístupy do DB, porty, registry) jsou v souboru `.devcontainer/.env`.

> Pokud je port 5432 nebo 8080 obsazený, změňte v `.env` hodnotu `DB_HOST_PORT` / `FRONTEND_PORT`.

## Ověření funkčnosti

1. **Stav kontejnerů** – `docker compose ps`: `postgresqldb`, `backend`, `frontend` mají stav `healthy`,
   `setup` je `Exited (0)` (`docker compose ps -a`).
2. **Logy setupu** – `docker compose logs setup` končí řádkem `Database setup finished.`
3. **Healthcheck API** (backend je publikovaný na portu z rozsahu 8091–8093, viz `docker compose ps`):
   ```bash
   curl localhost:8091/health/ready      # {"database": "UP", "status": "UP"} – backend vidí databázi
   curl localhost:8091/items/stats       # počty položek podle stavu
   ```
4. **Webové UI** – <http://localhost:8080>: tabulka s 6 ukázkovými položkami, souhrnné dlaždice a filtr *K doobjednání*.
5. **Zápis přes API** – vyskladnění 5 ks první položky a kontrola auditního logu:
   ```bash
   curl -X POST localhost:8091/items/1/movements -H 'Content-Type: application/json' \
        -d '{"delta": -5, "reason": "Test"}'
   curl localhost:8091/items/1/movements
   ```
   Pokus o vyskladnění víc kusů, než je skladem, vrátí HTTP 409.
6. **Perzistence** – `docker compose down` a znovu `docker compose up -d frontend`: změny zůstanou
   (named volume `db-data`). Úplný úklid včetně dat: `docker compose down -v`.

Škálování backendu: `docker compose up -d --scale backend=3` (proto rozsah portů 8091–8093).

## Sestavení a nahrání obrazů do registru (pro autora)

Obrazy jsou v compose pojmenované `${REGISTRY}/inventory-{backend,frontend,setup}:${TAG}`; `REGISTRY` a `TAG`
se nastavují v `.devcontainer/.env` (zde `REGISTRY=docker.io/mvelc`, `TAG=latest`).

```bash
cd .devcontainer
docker login                       # přihlášení do Docker Hubu
docker compose build setup backend frontend
docker compose push setup backend frontend
```

Lokální vývoj bez registru: `docker compose up --build -d frontend` (obrazy se sestaví z Dockerfilů).

## REST API

| Metoda | Cesta | Popis |
|---|---|---|
| GET | `/health/live` | liveness (bez DB) |
| GET | `/health/ready`, `/health` | readiness (ověří DB), 503 při výpadku |
| GET | `/items?status=OK\|LOW_STOCK\|OUT_OF_STOCK\|REORDER&q=` | seznam položek s filtrem |
| GET | `/items/stats` | počty položek podle stavu |
| GET | `/items/{id}` | detail |
| POST | `/items` | vytvoření (`sku`, `name`, `description`, `quantity`, `min_quantity`, `unit_price`) |
| PUT | `/items/{id}` | úprava (vše kromě `quantity`) |
| DELETE | `/items/{id}` | smazání |
| POST | `/items/{id}/movements` | pohyb `{"delta": -3, "reason": "Výdejka 42"}`; 409 při nedostatku kusů |
| GET | `/items/{id}/movements` | historie pohybů položky |
| GET | `/movements?limit=100` | globální auditní log |

Příklad:

```bash
curl -X POST localhost:8091/items/1/movements -H 'Content-Type: application/json' \
     -d '{"delta": -5, "reason": "Výdej na zakázku"}'
```

## Konfigurace (env)

| Proměnná | Služba | Význam |
|---|---|---|
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | backend, setup | připojení k DB (alternativně `DATABASE_URL` pro backend) |
| `DB_SEED` | setup | nahrát ukázková data (`true`/`false`) |
| `BACKEND_URL` | frontend | adresa API (`http://backend:5000`) |
| `FRONTEND_SECRET_KEY` | frontend | klíč pro session/flash zprávy |
| `DISPLAY_TZ` | frontend | časové pásmo pro zobrazení časů |
| `GUNICORN_CMD_ARGS` | backend, frontend | např. počet workerů |
| `LOG_LEVEL` | backend, frontend | úroveň logování |
