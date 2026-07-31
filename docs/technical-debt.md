# Registro de deuda tecnica

| ID | Descripcion | Razon | Impacto y riesgo | Condicion de resolucion | Objetivo |
|---|---|---|---|---|---|
| TD-001 | Cola PostgreSQL provisional | Cloud/servicio administrado no seleccionado | No representa escalado ni operacion productiva | Seleccionar cloud y adaptar el puerto conservando contratos/idempotencia | Antes de primer cliente |
| TD-002 | Worker usa `BYPASSRLS` | Debe descubrir trabajo global en la cola local | Mayor privilegio; un query sin tenant podria cruzar datos | Cola administrada o funcion de claim con seguridad definida y rol NOBYPASSRLS | Junto con TD-001 |
| TD-003 | Stack Python tiene estado provisional | No se documento experiencia operativa del equipo | Posible costo de cambio temprano | Validar B-05 con equipo y convertir o reemplazar ADR-016 | Antes de Iteracion 2 |
| TD-004 | TestClient emite deprecacion hacia `httpx2` | Versiones bloqueadas de FastAPI/Starlette estan en transicion | Ruido de test; riesgo futuro al actualizar | Adoptar cliente recomendado cuando sea estable y compatible | Iteracion 2 |
| TD-005 | Worker solo ofrece metricas, sin health HTTP propio | Suficiente para ejecucion local | Orquestador productivo no puede evaluar readiness del worker | Definir despliegue/cloud y agregar probe acorde a la cola definitiva | Antes de despliegue productivo |
| TD-006 | Politicas y evaluaciones telecom no tienen persistencia ni adaptador | Entrega 1 valida primero el dominio; Lead/Conversation aun no tienen propietario | No puede atender trafico real ni reconstruir historial tras reinicio | Implementar versiones append-only, evaluaciones, RLS y auditoria al aprobar Entregas 3/4 | Bloqueante antes de piloto |
| TD-007 | La politica base se materializa desde codigo | Se evito introducir un rule engine o CRUD generico antes de validar reglas | Cambiar puntos por tenant requiere despliegue y no existe simulador administrativo | Definir esquema de configuracion, validacion, comparador, publicacion atomica y rollback en Entrega 4 | Alta antes de piloto multiempresa |
| TD-008 | Extracciones y clave de idempotencia solo existen en memoria | Lead/Conversation aun no tienen propietario o transaccion durable | Reinicio pierde auditoria/dedupe; no puede aplicarse a trafico real | Entrega 3 debe persistir ejecucion, version de perfil y aplicacion atomica tenant-scoped con RLS/retencion | Bloqueante antes de piloto |
| TD-009 | Adaptador OpenAI validado offline, sin calibracion productiva | No se usan secretos/trafico real en CI y no existe dataset aprobado | Precision, latencia, coste y drift reales aun desconocidos | Evaluacion offline aprobada por negocio/privacidad y canary con presupuesto/SLO antes de habilitar | Alta antes de trafico productivo |

La ausencia de mensajeria real, maquina de estados, ejecucion de handoff, API y
metricas comerciales sigue siendo alcance pendiente de Entregas 3 y 5. La
extraccion y siguiente pregunta existen en memoria en Entrega 2; TD-008/TD-009
registran lo que impide operarlas en produccion.
