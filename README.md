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

## Lokální spuštění

```bash
cd .devcontainer
cp .env.example .env        # (soubor .env už je připravený s vývojovými hodnotami)
docker compose up --build -d frontend
```

- UI: <http://localhost:8080>
- API: <http://localhost:8091> (port z rozsahu 8091–8093, viz `docker compose ps`)
- DB: `localhost:5432`

Data jsou ve named volume `db-data` – přežijí `docker compose down`, smažou se až `docker compose down -v`.
Seed se nahraje jen při prázdné tabulce `items`.

Škálování backendu: `docker compose up -d --scale backend=3` (proto rozsah portů 8091–8093).

## Nahrání obrazů do registru

Obrazy jsou v compose pojmenované `${REGISTRY}/inventory-{backend,frontend,setup}:${TAG}`. Stačí v `.env` nastavit
`REGISTRY` (např. `docker.io/<uzivatel>` nebo cestu ke container registry projektu v KIV GitLabu) a `TAG`:

```bash
cd .devcontainer
docker login <registry-host>     # Docker Hub: docker login
docker compose build
docker compose push setup backend frontend
```

Databáze používá oficiální obraz `postgres:17`, ten se nepushuje.

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
