# Persistencia de Iteracion 1

Todas las tablas pertenecen al modulo de eventos sinteticos o a fundamentos
inmediatos de tenancy, entrega confiable y auditoria. No se crean tablas de
capacidades futuras.

| Entidad | Responsabilidad y propietario | Invariantes e indices | Ciclo de vida y eliminacion | Tenant |
|---|---|---|---|---|
| `tenants` | Raiz minima de tenancy | PK UUID; estado valido; version positiva | Creado por bootstrap/admin; sin hard delete en esta iteracion | `id` es la clave tenant |
| `synthetic_events` | Solicitud sintetica y estado | PK `(tenant_id,id)`; idempotencia unica por tenant; hash inmutable | accepted -> processed/failed; sin delete | RLS forzado y FK tenant |
| `outbox_items` | Evento de integracion atomico | Unico por tenant/agregado/tipo; indice status/available | pending -> published; conservar para trazabilidad | PK/FK compuestas y RLS |
| `queue_messages` | Transporte durable provisional | Unico por source outbox; indice claim; intentos >= 0 | available -> leased -> completed/dead | PK/FK compuestas y RLS |
| `consumer_receipts` | Inbox idempotente por consumidor | PK `(tenant_id,consumer_name,message_id)`; hash de payload | Inmutable; retencion por definir con cola definitiva | FK compuesta y RLS |
| `audit_events` | Registro append-only de acciones | PK `(tenant_id,id)`; indice tenant/created | Solo INSERT para roles tecnicos; sin delete | FK tenant y RLS |

## Concurrencia e idempotencia

La clave enviada por el adaptador se conserva en `synthetic_events` y su alcance
es un tenant durante toda la vida del registro. `INSERT ... ON CONFLICT` hace
segura la concurrencia: payload identico devuelve el evento existente; hash
distinto produce conflicto.

Outbox y queue se confirman en la misma base para que no exista una ventana de
publicacion parcial. El worker usa leases recuperables. El receipt y el cambio de
estado comparten transaccion; un ACK perdido no repite el efecto.

## Privilegios

- `app_owner`: ejecuta migraciones y es propietario del esquema.
- `app_runtime`: SELECT de tenant/evento e INSERT de evento/outbox/audit.
- `app_worker`: acceso limitado a las seis tablas; puede actualizar estados.

La migracion no contiene tenants ni datos de produccion. El bootstrap demo es un
comando separado, idempotente y solo usa identificadores conocidos de desarrollo.
No se edita la migracion aplicada como mecanismo de evolucion: el siguiente
cambio debe agregar una revision Alembic.

## Calificacion telecom: sin persistencia en Entrega 1

El modulo `lead_qualification` no reutiliza `synthetic_events` ni agrega tablas.
Sus perfiles, politicas y resultados son contratos de dominio inmutables; el
provider de politicas es un puerto sin adaptador productivo. Esto evita asignar
datos de Lead a un agregado sintetico y evita afirmar auditoria durable donde no
existe.

El modelo futuro y sus requisitos de historial, PK/FK tenant-scoped, RLS y
retencion se documentan en
[Telecommunications Lead Qualification](architecture/telecommunications-lead-qualification.md#modelo-de-datos-futuro).
La primera migracion de esa capacidad debe llegar junto con la propiedad de
Lead/Conversation y conservar cada evaluacion, no solo el score mas reciente.
