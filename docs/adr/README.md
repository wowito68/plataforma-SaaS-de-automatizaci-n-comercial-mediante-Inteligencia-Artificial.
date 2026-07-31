# Registro de ADRs

El repositorio sincronizado al iniciar Iteracion 1 no incluia los documentos
originales ADR-001 a ADR-015. No se reconstruyen ni renumeran silenciosamente.
Las decisiones de `Architecture Baseline v1` aplicables se preservan en
[arquitectura](../architecture/README.md) y el Readiness Check deja constancia de
esta ausencia documental.

| ADR | Estado | Decision |
|---|---|---|
| [ADR-016](ADR-016-provisional-python-stack.md) | Provisional aceptado | Python 3.12, uv, FastAPI, SQLAlchemy Core, Alembic y Psycopg para Iteracion 1 |
| [ADR-017](ADR-017-provisional-postgres-queue.md) | Provisional aceptado | Cola PostgreSQL durable local hasta seleccionar plataforma administrada |
| [ADR-018](ADR-018-deterministic-telecom-scoring.md) | Aceptado para Entrega 1 | El score telecom se calcula con politicas deterministicas versionadas; la IA solo aporta senales |
| [ADR-019](ADR-019-structured-ai-extraction-boundary.md) | Aceptado para Entrega 2 | Salida estructurada no confiable tras un puerto pequeno y validacion local; una llamada MVP y persistencia diferida |

Decisiones vigentes recibidas y aplicadas, sin asignarles un numero inexistente:
monolito modular, PostgreSQL compartido, tenant obligatorio con defensa en
profundidad/RLS, UUIDv7, outbox/inbox, idempotencia, auditoria, logs estructurados,
metricas y autenticacion OIDC administrada como objetivo fuera de esta iteracion.

Cuando el paquete arquitectonico original se sincronice, debe copiarse sin
alterar su historia y enlazarse desde este registro. ADR-016 y ADR-017 deben
confirmarse o reemplazarse antes de ampliar el producto.
