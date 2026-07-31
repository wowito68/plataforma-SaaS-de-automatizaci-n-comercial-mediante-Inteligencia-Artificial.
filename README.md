# Plataforma SaaS de automatizacion comercial mediante IA

Base tecnica de un monolito modular multiempresa. La Iteracion 1 demuestra que
un evento sintetico puede recorrer API, PostgreSQL, outbox, cola durable e inbox
hasta un worker sin perder aislamiento, trazabilidad ni idempotencia. La Entrega
1 de calificacion telecom agrega un motor de scoring puro, determinista,
versionado y explicable por tenant y tipo de oportunidad.

No contiene WhatsApp, extraccion con IA, RAG, contactos, conversaciones, leads
persistidos, CRM, billing ni autenticacion de usuarios. Tampoco expone API o
panel de calificacion. El adaptador sintetico existe solo para desarrollo y
pruebas y la configuracion impide habilitarlo en produccion.

## Arquitectura resumida

- Python 3.12, FastAPI, SQLAlchemy Core, Psycopg y Alembic.
- PostgreSQL 16 compartido con `tenant_id`, claves compuestas y RLS forzado.
- Transaccion de entrada: `synthetic_events + outbox_items + audit_events`.
- Dispatcher con `FOR UPDATE SKIP LOCKED` y cola PostgreSQL provisional.
- Consumidor at-least-once con lease e inbox en `consumer_receipts`.
- Logs JSON, request/correlation IDs, metricas Prometheus y health checks.
- UUIDv7, configuracion validada y credenciales runtime/worker/migration separadas.
- Perfil comercial inmutable, senales con confianza y contradicciones trazables.
- Politicas telecom 0..100 con topes, exclusiones, penalizaciones y handoff.
- Elegibilidad financiera separada y referencia historica de politica/fingerprint.

La seleccion del stack y la cola son provisionales y estan registradas en
[ADR-016](docs/adr/ADR-016-provisional-python-stack.md) y
[ADR-017](docs/adr/ADR-017-provisional-postgres-queue.md). El scoring determinista
se registra en [ADR-018](docs/adr/ADR-018-deterministic-telecom-scoring.md).

## Requisitos

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Docker con Compose
- PostgreSQL client opcional para diagnostico

## Inicio rapido

```bash
cp .env.example .env
uv sync --locked
docker compose up -d postgres
uv run alembic upgrade head
uv run saas-bootstrap-demo
```

Inicia la API y el worker en terminales separadas:

```bash
uv run saas-api
uv run saas-worker
```

La API queda en `http://127.0.0.1:8000`; las metricas del worker, en
`http://127.0.0.1:9001`. Los tenants demo son estables:

- Alpha: `019867ab-cdef-7abc-8def-0123456789ab`
- Beta: `019867ab-cdef-7abc-8def-0123456789ac`

Envia un evento:

```bash
curl -i -X POST \
  http://127.0.0.1:8000/internal/v1/tenants/019867ab-cdef-7abc-8def-0123456789ab/synthetic-events \
  -H 'Authorization: Bearer replace-with-at-least-24-local-characters' \
  -H 'Idempotency-Key: local-demo-001' \
  -H 'Content-Type: application/json' \
  -d '{"kind":"demo.accepted","attributes":{"source":"local"}}'
```

La primera llamada responde `202`; una repeticion identica responde `200` con
el mismo ID; la misma clave con otro payload responde `409`. Consulta el estado
con el `id` devuelto:

```bash
curl -H 'Authorization: Bearer replace-with-at-least-24-local-characters' \
  http://127.0.0.1:8000/internal/v1/tenants/019867ab-cdef-7abc-8def-0123456789ab/synthetic-events/ID_DEL_EVENTO
```

## Configuracion

`.env.example` describe todas las variables. Las obligatorias para API/worker
son `APP_ENV`, `SERVICE_NAME`, `DATABASE_URL` y `DATABASE_WORKER_URL`.
`DATABASE_MIGRATION_URL` pertenece solo a Alembic y `DATABASE_ADMIN_URL` solo al
bootstrap local. Ninguna credencial de ejemplo es apta para otro entorno.

La API falla al iniciar si la configuracion es incompleta o si se intenta
habilitar `SYNTHETIC_ADAPTER_ENABLED` en produccion.

## Health y metricas

- `GET /health/live`: valida que el proceso ASGI responde; no consulta externos.
- `GET /health/ready`: ejecuta `SELECT 1` con la credencial runtime.
- `GET /metrics`: metricas Prometheus de API, outbox observado y proceso local.
- Puerto `WORKER_METRICS_PORT`: metricas del proceso worker independiente.

## Calidad y pruebas

```bash
make format
make lint
make typecheck
make test
make build
make audit
```

`make test` levanta un PostgreSQL 16 efimero con Testcontainers, crea roles de
minimo privilegio, migra desde cero y ejecuta pruebas unitarias e integracion.
Tambien puede usarse `TEST_DATABASE_SUPERUSER_URL` para apuntar a una base vacia
de CI. La cobertura minima obligatoria es 85%. Los casos telecom A-E se ejecutan
como pruebas unitarias sin red, base de datos o proveedor de IA.

## Estructura

```text
src/saas_platform/
  modules/             dominio, casos de uso y puertos por capacidad
  infrastructure/      PostgreSQL, RLS, outbox y cola
  entrypoints/          API, worker y bootstrap local
  config.py             carga y validacion centralizada
  observability.py      logging, contexto y metricas
migrations/             historial Alembic reproducible
tests/unit/             reglas sin infraestructura
tests/integration/      API, persistencia, RLS y worker reales
infra/postgres/init/    roles locales; no contiene datos de negocio
docs/                   arquitectura, ADRs, runbook y deuda
```

Consulta [arquitectura](docs/architecture/README.md),
[calificacion telecom](docs/architecture/telecommunications-lead-qualification.md),
[informe de Entrega 1](docs/reports/telecommunications-lead-qualification-delivery-1-report.md),
[persistencia](docs/persistence.md), [runbook](docs/runbook.md),
[dependencias](docs/dependencies.md) y [deuda tecnica](docs/technical-debt.md).

## Limitaciones y siguiente paso

La cola PostgreSQL y el rol worker con `BYPASSRLS` son decisiones temporales.
Antes de produccion deben confirmarse cloud, cola administrada y stack con el
equipo. La calificacion actual no persiste perfiles, politicas o resultados y no
procesa texto libre. Su siguiente vertical recomendado es extraccion IA con
esquemas y prompts versionados; la IA entregara senales, nunca el score final.
