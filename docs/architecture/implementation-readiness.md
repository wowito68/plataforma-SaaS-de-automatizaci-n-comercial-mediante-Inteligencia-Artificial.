# Implementation Readiness Check

## Veredicto recibido

La arquitectura fue auditada como **APROBADA CON CONDICIONES**. La
`Architecture Baseline v1` autoriza una primera entrega vertical despues de
resolver las decisiones aplicables de stack y entorno.

## Resolucion minima

El repositorio sincronizado contenia solo un README y no registraba las
decisiones B-05 (stack) y B-06 (plataforma). Para no inventar decisiones
irreversibles:

- ADR-016 selecciona provisionalmente Python 3.12, uv, FastAPI,
  SQLAlchemy/Alembic y Psycopg.
- ADR-017 usa PostgreSQL como cola local durable solo durante Iteracion 1.
  La seleccion de una cola administrada queda pendiente de la plataforma cloud.

Ambas decisiones son reversibles y no modifican el modelo de dominio, la
propiedad de datos ni el aislamiento multiempresa.

## Alcance exacto

- Configuracion validada y separacion de credenciales runtime/worker/admin.
- TenantId, TenantContext y persistencia tenant-scoped.
- Evento sintetico recibido por un adaptador interno de desarrollo.
- Idempotencia de recepcion.
- Persistencia atomica de evento, auditoria y outbox.
- Dispatcher, cola durable, inbox/consumer receipt y worker.
- Health checks, logs JSON, correlation IDs y metricas Prometheus.
- Migracion reproducible desde una base vacia.
- Pruebas de aislamiento, concurrencia, idempotencia y fallos.
- CI, documentacion y runbook local.

## Fuera de alcance

WhatsApp, autenticacion de usuarios, IA, RAG, contactos, conversaciones
comerciales, leads, handoff, billing, CRM, analitica avanzada y despliegue
productivo.

## Riesgos y supuestos

- La plataforma cloud definitiva sigue pendiente.
- La cola PostgreSQL no es la cola administrada objetivo.
- El adaptador sintetico es interno, se protege con token y debe estar
  deshabilitado en produccion.
- El worker usa una credencial tecnica con BYPASSRLS para descubrir trabajo de
  todos los tenants. Sus permisos se limitan a las tablas de Iteracion 1 y se
  retirara al adoptar una cola administrada.
- PostgreSQL 16 y Docker estan disponibles para desarrollo e integracion.

## Criterios de aceptacion

1. Dos tenants procesan eventos sin lectura ni referencia cruzada.
2. La repeticion identica produce un solo efecto.
3. La misma clave con otro payload produce conflicto.
4. Un fallo de publicacion conserva el outbox.
5. Worker y dispatcher pueden reintentar sin duplicar efectos.
6. Todos los registros propagados contienen correlation ID.
7. Configuracion, migraciones, lint, tipos, pruebas y build son reproducibles.

## Definition of Done

La iteracion termina solamente cuando todas las validaciones obligatorias pasan,
la operacion local esta documentada y no existe funcionalidad de iteraciones
posteriores.
