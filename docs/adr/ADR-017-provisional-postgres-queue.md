# ADR-017: Cola PostgreSQL provisional

- **Estado:** Provisional, aceptado solo para Iteracion 1 y desarrollo local
- **Contexto:** La baseline exige una cola administrada, pero B-06 no selecciono
  plataforma cloud.
- **Opciones:** Elegir un cloud sin evidencia, RabbitMQ local, cola PostgreSQL.
- **Decision:** Implementar el puerto de cola con una tabla PostgreSQL durable,
  leases, reintentos limitados y `SKIP LOCKED`.
- **Razon:** Valida outbox/inbox e idempotencia sin introducir un broker que no
  sera necesariamente el servicio productivo.
- **Consecuencias:** Un unico datastore en local; el worker necesita una
  credencial tecnica limitada para descubrir trabajo global.
- **Riesgo:** Contencion y mayor privilegio del worker.
- **Mitigacion:** Payload minimo, permisos separados, filtros tenant-scoped y
  prohibicion explicita de usar esta cola como decision de escala productiva.
- **Revision:** Obligatoria al seleccionar cloud o antes del primer cliente.
