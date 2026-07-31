# Telecommunications Lead Qualification - Delivery 1 Report

## Analisis de impacto

La capacidad afecta el dominio comercial, pero el repositorio solo tenia
`tenancy` y el walking skeleton `synthetic_events`. No existian propietarios de
Lead, Contact o Conversation, ni adaptadores de IA, catalogo, cobertura,
inventario, elegibilidad o handoff.

Se agrego un modulo de negocio puro `lead_qualification`, reutilizando
`TenantId`, errores independientes de transporte y puertos `Clock`/`IdGenerator`.
No se modificaron transacciones, tablas, API ni worker. Los principales riesgos
y decisiones son:

| Riesgo | Decision aplicada |
|---|---|
| Convertirlo en CRM/CRUD generico | Limitar el modulo a calificacion telecom |
| Score inventado por IA | Solo la politica determinista asigna puntos |
| Doble conteo | Grupos exclusivos en intencion, presupuesto y urgencia |
| Fuga entre empresas | Provider tenant-scoped y segunda validacion en motor |
| Cambio retroactivo | ID, version y fingerprint en cada resultado |
| Confundir score con aprobacion | Elegibilidad externa e independiente |
| Persistir bajo propietario incorrecto | Posponer migracion hasta definir Lead/Conversation |

El detalle completo esta en
[Lead Qualification Impact Analysis](../architecture/lead-qualification-impact-analysis.md)
y [ADR-018](../adr/ADR-018-deterministic-telecom-scoring.md).

## Diseno resultante

- `LeadProfile` es un snapshot inmutable con hechos, procedencia, faltantes y
  contradicciones.
- `Opportunity` conserva un tipo primario y tipos secundarios.
- `CommercialSignal` tiene confianza, origen, metodo y validacion.
- `ScoringPolicy` es tenant/opportunity-scoped, inmutable, publicada, versionada
  y validada al construirse.
- `LeadScoringEngine` calcula dimensiones, topes, penalizaciones, bloqueos,
  clasificacion, accion, handoff y explicacion.
- `QualificationResult` conserva entrada de auditoria, politica/fingerprint,
  desglose, elegibilidad y metadata de extraccion.
- `EvaluateLeadQualification` obtiene la politica por puerto y aporta ID/fecha.

El pipeline implementado comienza en senales ya normalizadas y cubre evaluacion,
explicacion y referencia de version. Extraccion, actualizacion conversacional,
persistencia, eventos, metricas y ejecucion de handoff permanecen fuera de esta
entrega. El modelo completo esta en
[Telecommunications Lead Qualification](../architecture/telecommunications-lead-qualification.md).

## Implementacion

Archivos creados para esta capacidad:

- `src/saas_platform/modules/lead_qualification/domain.py`;
- `src/saas_platform/modules/lead_qualification/scoring.py`;
- `src/saas_platform/modules/lead_qualification/default_policy.py`;
- `src/saas_platform/modules/lead_qualification/ports.py`;
- `src/saas_platform/modules/lead_qualification/application.py`;
- `tests/unit/test_lead_qualification.py`;
- analisis, ADR, documento de arquitectura y este informe.

Archivos modificados: `README.md`, indices de arquitectura/ADR,
`docs/persistence.md` y `docs/technical-debt.md`.

No hubo migraciones, endpoints, SDKs, dependencias ni UI. El unico caso de uso
nuevo es `EvaluateLeadQualification`; no esta compuesto en la API hasta que
exista un recurso Lead autenticado y persistente.

## Politica inicial

| Dimension | Peso |
|---|---:|
| Necesidad/compatibilidad | 25 |
| Intencion | 25 |
| Presupuesto/pago | 20 |
| Urgencia | 15 |
| Participacion | 10 |
| Potencial | 5 |

Umbrales: exploracion 0-29, frio 30-49, tibio 50-69, caliente
70-84 y prioritario 85-100. Confianza automatica minima: `0.70`.

La intencion selecciona uno entre 3/7/10/12/15/18/20/22/25; presupuesto uno
entre 2/5/10/15/20; urgencia uno entre 2/5/9/12/15. Necesidad aporta cinco puntos
por necesidad, plan, equipo, cobertura y requisitos aplicables. Participacion
suma 3/3/2/2 y potencial 2/1/3/5 con tope 5.

Penalizaciones: sin cobertura -20, equipo agotado -10, presupuesto incompatible
sin alternativas -15, mas de seis meses -8, contradiccion -5 e inactividad -5.
No contacto vale 0 y actua como control: detener, estado dedicado, sin handoff.

Handoff por solicitud humana, empresa con multiples lineas, contratacion
inmediata, portabilidad, cotizacion, alternativa aceptada tras falta de
inventario, revision humana, contradiccion o banda caliente/prioritaria.

Por oportunidad, las reglas de plan/equipo solo se incluyen cuando aplican. La
politica completa y sus prioridades estan documentadas en el documento principal.

## Validacion

| Validacion | Resultado |
|---|---|
| Casos A-E e invariantes telecom | 81 pruebas del archivo nuevo, todas pasan |
| Suite completa con PostgreSQL 16 efimero | 124 passed |
| Cobertura global | 92.78%, minimo 85% |
| Cobertura del modulo telecom medida por separado | 98.79% |
| Ruff formato/lint | OK, 56 archivos |
| Mypy estricto | OK, 40 archivos fuente/prueba |
| Lock `uv` offline | OK, 72 paquetes resueltos |
| Build sdist/wheel offline | OK |
| Secret scan, incluyendo untracked | OK |
| `pip-audit` runtime | Sin vulnerabilidades conocidas |
| Compose | Configuracion valida; PostgreSQL healthy |
| Smoke API/worker | live/ready/metricas OK |

Errores corregidos durante la implementacion: tipado de ranking de senales,
reutilizacion de variables inferidas, explicacion de evidencia principal,
prioridad absoluta de no contacto, doble conteo y regla combinada de inventario
agotado con alternativa aceptada.

Limitaciones de validacion: FastAPI/Starlette emite la advertencia conocida hacia
`httpx2` (TD-004). La primera ejecucion sandboxed no pudo usar Docker/DNS/puertos;
se repitio con un PostgreSQL efimero y permisos locales autorizados. No se
probaron IA, API telecom, persistencia o panel porque no existen en esta entrega.

## Deuda tecnica

| ID | Impacto | Motivo | Prioridad | Condicion de resolucion |
|---|---|---|---|---|
| TD-006 | Sin operacion ni historial durable telecom | Primero se valido el dominio y falta propietario Lead/Conversation | Bloqueante pre-piloto | Persistencia append-only, RLS y auditoria en Entregas 3/4 |
| TD-007 | Cambios tenant requieren despliegue | Se evito un rule engine/CRUD prematuro | Alta pre-piloto | Esquema, simulador y publicacion atomica en Entrega 4 |

Extraccion IA, siguiente pregunta, maquina de estados operativa, API, handoff y
metricas son entregas pendientes declaradas, no funcionalidad silenciosamente
incompleta dentro del motor.

## Proximo paso

Implementar **Entrega 2: extraccion estructurada mediante IA**. Debe producir
oportunidad, hechos, senales y contradicciones contra esquemas versionados;
validar confianza; soportar timeout, salida invalida y proveedor caido; y mantener
al modelo fuera del calculo autoritativo. No debe incluir aun WhatsApp, CRM ni un
constructor visual de reglas.

## Veredicto

**IMPLEMENTACIÓN PARCIAL**

| Area | Estado | Evidencia | Pendiente |
|---|---|---|---|
| Tipos de oportunidad | Implementado | 11 tipos y primario/secundarios | Clasificacion desde texto |
| Perfil comercial | Implementado en dominio | Hechos, procedencia, faltantes, conflictos | Actualizacion/persistencia |
| Motor de scoring | Implementado | 0..100, topes, exclusiones y reglas | Adaptador productivo |
| Configuracion multiempresa | Parcial | Tenant/oportunidad/version y aislamiento probado | Persistencia/admin/publicacion |
| Extraccion con IA | No implementado | Metadata preparada, sin SDK | Entrega 2 completa |
| Explicabilidad | Implementado | Dimensiones, reglas, senales, bloqueos y fingerprint | Historial durable/UI |
| Estados | Parcial | Vocabulario y recomendaciones | Maquina transaccional |
| Handoff | Parcial | Decision y motivos | Asignacion/pausa/reanudacion |
| Elegibilidad | Implementado en dominio | Estado independiente, nunca inferido | Integracion autorizada |
| Auditoria | Parcial | Trigger/correlacion/politica en resultado | Persistencia append-only |
| Metricas | No implementado | Diseno documentado | Entrega 5 |
| Seguridad | Parcial | Tenant estricto y allowlist comercial | OIDC/RBAC/RLS de nuevas tablas |
| Pruebas | Implementado | 124 passed, 92.78% | Entregas futuras no aplican aun |
| Documentacion | Implementado | Impacto, ADR, politica, pipeline y reporte | Actualizar por entrega |
