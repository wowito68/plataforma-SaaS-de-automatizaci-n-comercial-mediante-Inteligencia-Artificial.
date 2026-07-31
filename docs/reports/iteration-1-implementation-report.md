# Iteration 1 Implementation Report

## Resumen

El objetivo fue crear una base ejecutable del monolito modular y demostrar una
unica entrega vertical multiempresa. El resultado acepta un evento sintetico por
un adaptador interno, persiste evento/auditoria/outbox atomicamente, publica en
una cola durable provisional y procesa mediante worker con inbox idempotente.

Estado: **ITERACION COMPLETADA CON DEUDA**. La capacidad fue demostrada en local
con dos tenants y PostgreSQL 16 real. No se implementaron capacidades de producto
posteriores.

## Archivos

| Grupo | Cambio | Razon |
|---|---|---|
| `pyproject.toml`, `uv.lock`, `.python-version` | Creados | Runtime, lock, build y herramientas reproducibles |
| `.env.example`, `config.py`, `compose.yaml` | Creados | Configuracion validada y PostgreSQL local sin secretos reales |
| `src/saas_platform/modules/` | Creado | Dominio, casos de uso y puertos de tenancy/eventos |
| `src/saas_platform/infrastructure/` | Creado | Engines, esquema, store, outbox y cola PostgreSQL |
| `src/saas_platform/entrypoints/`, `bootstrap.py` | Creados | API, worker, demo y raiz de composicion |
| `migrations/`, `alembic.ini`, `infra/postgres/init/` | Creados | Migracion inicial, RLS y roles separados |
| `tests/unit/`, `tests/integration/` | Creados | Dominio/configuracion y componentes reales |
| `.github/workflows/ci.yml`, `Makefile`, `.secrets.baseline` | Creados | Calidad local y CI obligatoria |
| `docs/` | Creado | Readiness, arquitectura, ADRs, persistencia, runbook y deuda |
| `README.md` | Modificado | Sustituye el placeholder por una guia reproducible |

No se eliminaron archivos funcionales existentes; el repositorio inicial solo
contenia el README placeholder.

## Arquitectura

Se implementaron `tenancy` y `synthetic_events` dentro de un monolito modular.
El dominio no depende de frameworks. La aplicacion orquesta aceptar, consultar y
procesar. Los puertos pequenos cubren persistencia; adaptadores PostgreSQL y HTTP
traducen sus limites. `Container` compone reloj, UUIDv7, stores, dispatcher, cola
y casos de uso.

Se aplicaron tenant obligatorio, transacciones cortas, SQL parametrizado,
outbox/inbox, entrega at-least-once, leases, reintentos, dead-letter e
idempotencia por hash. ADR-016 y ADR-017 documentan stack/cola provisionales.

## Persistencia

La revision `20260731_0001` crea `tenants`, `synthetic_events`, `outbox_items`,
`queue_messages`, `consumer_receipts` y `audit_events`. Incluye PK/FK compuestas,
unicidad tenant-scoped, checks e indices de dispatch/claim/auditoria.

Las seis tablas tienen RLS habilitado y forzado. `app_runtime` es NOBYPASSRLS;
cada transaccion establece `app.current_tenant_id`. `app_worker` tiene permisos
limitados pero BYPASSRLS provisional para descubrir trabajo global. No hay datos
de negocio en migraciones ni hard delete en esta iteracion.

## Casos de uso

- `AcceptSyntheticEvent`: actor adaptador interno; valida tenant/key/payload;
  devuelve 202 creado o 200 replay; 404/409/422 esperados; genera outbox/audit.
- `GetSyntheticEvent`: lectura tenant-scoped; devuelve estado o 404 sin revelar
  existencia cruzada.
- `ProcessSyntheticEvent`: actor worker; valida envelope/hashes, aplica receipt y
  transicion a processed; una repeticion es no-op.

## Seguridad

Se implementaron validacion estricta, bearer token con comparacion constante,
feature flag prohibido en produccion, RLS forzado, tenant en relaciones/unicidad,
roles separados, SQL parametrizado, respuestas sin detalles internos, payloads
no registrados, redaccion recursiva y baseline de secretos.

Pendientes deliberados: OIDC de usuarios, autorizacion de producto, rate limiting
y validacion de proveedores externos no pertenecen a esta iteracion. El token
interno nunca constituye autenticacion productiva.

## Observabilidad

Logs JSON incluyen timestamp, level, environment, service, module, request,
correlation, tenant, event/message, operation, result y tipo de error cuando
existen. Request/correlation se devuelven en headers. Prometheus expone HTTP,
duracion, aceptaciones, outbox pendiente/dispatch y resultados del worker sin
etiquetas tenant. `/health/live` no consulta externos y `/health/ready` valida
PostgreSQL runtime. El worker expone metricas en puerto separado.

## Pruebas

Se ejecutaron 43 pruebas: dominio, configuracion, redaccion, taxonomia HTTP, API,
migracion, RLS, aislamiento, idempotencia secuencial/concurrente, rollback de
dispatch, lease vencido, ACK parcial, deduplicacion y dead-letter. Resultado:
43 passed, cobertura total 87.47% (minimo 85%).

No se prueba despliegue cloud ni proveedores reales porque siguen fuera de
alcance. FastAPI/Starlette emite una deprecacion de TestClient hacia `httpx2`,
registrada como TD-004.

## Validaciones ejecutadas

| Comando | Proposito | Resultado y correccion |
|---|---|---|
| `uv sync --locked` | Instalacion reproducible | OK; DNS sandbox requirio acceso autorizado |
| `uv lock --check` | Lock consistente | OK, 72 paquetes resueltos |
| `ruff format --check .` / `ruff check .` | Formato y lint | OK; imports/tipos iniciales corregidos |
| `mypy src tests` | Tipado estricto | OK, 33 archivos fuente/test revisados |
| `pytest ... --cov` | Unitarias/integracion/cobertura | OK, 43 passed, 87.47% |
| Alembic en DB vacia y local | Migraciones | OK; revision inicial aplicada |
| `docker compose config --quiet` | Compose valido | OK; host port configurable tras detectar 5432 ocupado |
| `uv build` | sdist y wheel | OK; flag incompatible retirado y build aislado validado |
| `detect-secrets-hook` | Secretos nuevos | OK, 58 archivos; placeholders conocidos en baseline |
| `pip-audit` runtime | Vulnerabilidades | OK, ninguna conocida |
| Curl API + worker | Entrega vertical | 202 -> processed v2; replay 200; conflicto 409 |

## Deuda tecnica

TD-001/TD-002 cubren cola PostgreSQL y BYPASSRLS; prioridad alta antes del primer
cliente. TD-003 exige confirmar el stack antes de Iteracion 2. TD-004 cubre la
deprecacion del cliente de test. TD-005 exige health del worker antes de un
despliegue productivo. Motivos, impacto y condiciones estan en
`docs/technical-debt.md`.

## Desviaciones

La baseline esperaba stack/plataforma aprobados, pero B-05/B-06 y los ADRs
originales no estaban sincronizados. ADR-016 selecciona un stack reversible para
esta iteracion; ADR-017 reemplaza temporalmente la cola administrada por
PostgreSQL. Ninguna desviacion cambia propiedad de datos, limites
transaccionales, contratos externos o aislamiento.

## Proxima iteracion

Prerequisitos: confirmar stack con el equipo, seleccionar cloud/cola administrada,
sincronizar ADR-001..015 y resolver el modelo operativo del worker. Despues puede
proponerse la siguiente capacidad vertical aprobada. No deben iniciarse todavia
WhatsApp, IA/RAG, CRM, billing, microservicios, Kubernetes o multi-region.

## Veredicto

**ITERACION COMPLETADA CON DEUDA**

| Area | Estado | Evidencia | Pendiente |
|---|---|---|---|
| Estructura | OK | Monolito modular ejecutable | Ninguno de Iteracion 1 |
| Configuracion | OK | Settings validados y `.env.example` | Secret manager productivo futuro |
| Dominio | OK | Tenant/evento/idempotencia sin framework | Ninguno |
| Aplicacion | OK | Tres casos de uso | Ninguno |
| Persistencia | OK | Alembic + seis tablas + constraints | Retencion futura |
| API/entradas | OK | Adaptador interno protegido | OIDC fuera de alcance |
| Multiempresa | OK | PK/FK tenant, RLS y pruebas cruzadas | Retirar BYPASSRLS worker |
| Seguridad | OK con deuda | Minimo privilegio, redaccion, secretos | Cola/identidad productivas |
| Observabilidad | OK | JSON, IDs, metricas, health | Health worker productivo |
| Pruebas | OK | 43 passed, 87.47% | Deprecacion TestClient |
| CI | OK | Workflow con todas las puertas | Ejecutar al publicar rama |
| Documentacion | OK | README, arquitectura, runbook, ADR/deuda | Sincronizar ADRs originales |
| Criterios de aceptacion | OK | E2E local y suite real | Ninguno de Iteracion 1 |
