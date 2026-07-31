# Delivery 2 Implementation Report

## Resultado

Objetivo: transformar mensajes telecom en informacion comercial estructurada,
validada, explicable y segura para el dominio determinista de Entrega 1.

Estado: capacidad implementada y validada offline de extremo a extremo. Recibe
mensajes en memoria, selecciona/redacta contexto, cruza un puerto de extraccion,
valida salida estricta, normaliza, recalcula confianza, reconcilia conflictos,
selecciona faltantes/pregunta/handoff y propone un `LeadProfile` inmutable con su
`QualificationRequest`.

Alcance real: 11 oportunidades, vocabulario telecom solicitado, procedencia,
confianza, controles, DNC, errores/fallback, OpenAI/scripted, auditoria en memoria,
idempotencia conceptual, logs/metricas y casos A-E/adversariales.

Exclusiones respetadas: no WhatsApp/webhook, persistencia Lead/Conversation,
endpoint, respuesta al cliente, transicion de estado, asignacion de asesor,
catalogo, cobertura, inventario, financiamiento, RAG, agentes, herramientas,
multi-provider, UI ni Entrega 3.

## Arquitectura

| Componente | Implementacion |
|---|---|
| Puerto | `CommercialExtractionProvider`, orientado solo a extraccion comercial estructurada |
| Adaptador productivo inicial | `OpenAICommercialExtractionAdapter` con Responses API |
| Adaptador offline | `ScriptedCommercialExtractionAdapter` con cola de resultados/errores |
| Modelos | Provider, validated, normalized, accepted, final result y audit record |
| Contexto | Ultimos mensajes por cantidad/caracteres/edad, resumen acotado y perfil permitido |
| Procesamiento | Esquema, semantica, normalizacion, confianza, contradiccion y decision |
| Integracion | `ExtractionDomainMapper` crea perfil propuesto y `QualificationRequest` |
| Composicion | Opt-in desde `Settings`; no se incorpora a API/worker sin caso de uso aprobado |

Flujo implementado:

```text
Messages -> context/redaction -> provider port -> raw structured payload
 -> strict schema -> semantic authority -> normalization -> confidence
 -> contradictions -> accepted extraction -> profile proposal
 -> QualificationRequest -> Delivery 1 scoring (caller-controlled)
```

Dependencias nuevas: SDK oficial `openai>=2.45,<3` y declaracion explicita
`pydantic>=2.13,<3`. El lock resolvio `openai 2.52.0` y `pydantic 2.13.4`. No se
agrego framework de agentes ni otra infraestructura.

Decisiones: ADR-019 adopta una llamada estructurada MVP, un puerto pequeno,
validacion local y persistencia diferida. ADR-018 conserva autoridad exclusiva
del scoring.

## Prompts y esquemas

Se registran seis responsabilidades versionadas bajo
`telecom-extraction.v1`: oportunidad, campos/senales, contradicciones, controles,
resumen y pregunta. Se compilan en una sola instruccion para reducir latencia,
coste y complejidad. Un segundo intento solo se permite para reparacion de schema
o error transitorio; la instruccion de reparacion se usa exclusivamente tras una
salida invalida.

Schema `telecom-extraction-result.v1`: Pydantic estricto, todos los objetos con
campos adicionales prohibidos, listas/textos/rangos acotados, moneda `MXN|USD`,
cantidades enteras no negativas, fechas ISO y confianza 0..1. Oportunidad,
intencion, urgencia, controles y handoff conservan evidencia/mensaje/confianza.

No existen propiedades de score, pesos, umbrales, elegibilidad, aprobacion,
politica o estado. Inyectarlas invalida toda la respuesta. Cobertura/inventario y
senales autoritativas incompatibles con IA se eliminan en validacion semantica.

## Extraccion

- Producto/equipo: categoria, marca, modelo, almacenamiento, color, condicion,
  cantidad, precio, presupuesto, financiamiento preferido, enganche, mensualidad
  y plazo.
- Plan: plan, presupuesto, datos, llamadas, mensajes, redes, roaming,
  internacionales, modalidad, lineas y equipo incluido.
- Portabilidad/linea: tipo, conservar numero, operador, cantidad, plazo, motivo,
  corte y estado explicitamente declarado.
- Empresa: nombre/tamano, lineas/equipos, fecha, facturacion, administracion,
  rol, participacion y accion.
- Intencion/urgencia: exploracion hasta contratacion/compra y sin plazo hasta
  inmediato, cada una con procedencia propia.
- Controles: DNC, humano, queja, lenguaje ofensivo, fraude, sensibles, no
  relacionado, comprension baja, contradiccion, insuficiencia, injection y
  reclamos no verificados.

Confianza final combina declaracion, evidencia verificable, observado/inferido,
soporte repetido y contradiccion. Umbrales por defecto: `0.85` autoaceptado,
`0.65` provisional, `0.40` confirmacion y menor rechazado. Solo senales
autoaceptadas llegan al scoring; hechos provisionales quedan `hypothesis`.

Las contradicciones conservan valor previo/nuevo, fuentes/evidencia disponible,
confianza, resolucion conservadora y pregunta. Nunca reemplazan silenciosamente;
un hecho confirmado por humano queda protegido.

Faltantes se calculan por oportunidad. La pregunta del proveedor debe apuntar al
primer faltante/conflicto, no repetirse y pasar limites de longitud, una sola
pregunta y datos sensibles; si no, se usa plantilla local. DNC, humano o handoff
confirmado la suprimen.

DNC tiene prioridad absoluta incluso si el proveedor retorna salida invalida o
timeout. Humano, riesgo, cuenta/volumen, portabilidad urgente y sugerencia del
proveedor validada producen handoff general/especializado sin ejecutarlo.

## Integracion con scoring

El mapper acepta exclusivamente tipos de Entrega 1. Normaliza moneda/fecha,
preserva evidencia/confianza/conflictos y construye un nuevo perfil. La
elegibilidad viene del comando y se copia sin cambios.

Protecciones demostradas:

- score/pesos/eligibilidad extra invalidan schema;
- cobertura/inventario de IA no generan hechos o penalizaciones;
- mensaje ajeno al contexto tenant invalida la salida;
- hecho confirmado no se sobrescribe;
- senal provisional/baja no entra a `QualificationRequest`;
- DNC local no puede omitirse por respuesta invalida;
- no existe campo de score o transicion en resultado/mapeo.

`LeadScoringEngine` y `default_policy.py` no fueron modificados. Una prueba pasa
el DNC mapeado al motor y verifica `STOP_AUTOMATION/DO_NOT_CONTACT`.

## Proveedor

OpenAI usa `responses.parse`, modelo configurable (`gpt-5.6-terra` por defecto),
baja intensidad de razonamiento, `store=False`, salida no streaming, timeout,
limite de tokens y SDK retries en cero. La aplicacion controla como maximo tres,
por defecto uno, con backoff e idempotency key estable.

Se traducen timeout, rate limit, conexion, autenticacion, status temporal/
permanente, rechazo, salida vacia, invalida e incompleta. Ninguna excepcion SDK
sale del adaptador. Tras agotar intentos se retorna `degraded`, perfil intacto y
error explicito; no se inventa extraccion.

No se ejecuto una llamada OpenAI real: no se requirio credencial y la entrega
exige soporte offline. El contrato se valido con tipos reales del SDK y cliente
fake. Una prueba live/canary queda condicionada a dataset, privacidad, presupuesto
y credencial aprobados (TD-009).

## Seguridad

- tenant obligatorio en perfil y cada mensaje; solo message IDs seleccionados;
- email, telefono y RFC/CURP-like redactados antes del proveedor;
- limites absolutos y configurables de entrada;
- profile context allowlisted; sin tenant ID, secretos, politicas, stock,
  cobertura o reclamo financiero;
- mensajes serializados como datos no confiables y detector local de injection;
- no prompt/mensaje/raw payload/API key en logs;
- no ID/contenido/telefono en etiquetas Prometheus;
- API key requerida solo al componer la capacidad habilitada;
- produccion prohibe logging de contenido.

## Observabilidad

Logs JSON: request/correlation/tenant context, execution, provider, model,
prompt/schema, duracion, outcome, error type y tokens. Auditoria en memoria:
message IDs, versiones, proveedor/modelo, payload estructurado bruto, validaciones,
aceptados/rechazados, conflictos, propuesta y motivo de handoff; nunca CoT.

Metricas: started/completed/failed, error normalizado, reparacion, duracion,
confianza, campos, contradicciones, DNC, handoff, tokens y coste opcional. Las
series se verificaron en pruebas y smoke del registry.

## Pruebas

| Evidencia | Resultado |
|---|---|
| Suite enfocada Entrega 2 | 136 passed |
| Cobertura enfocada del modulo nuevo | 99.08% |
| `schema.py` | 98% |
| `processing.py` (validadores/mapper) | 99% |
| Suite completa, incluida integracion PostgreSQL | 265 passed |
| Cobertura global | 95.90%, superior a baseline 92.94% |
| Casos A-E | Todos reproducibles offline |
| Adicionales/seguridad/SDK | Ambiguedad, conflicto, cambio, humano, injection, finanzas, stock/cobertura y errores cubiertos |

Advertencia residual: Starlette comunica la deprecacion conocida de TestClient
hacia `httpx2` (TD-004); no es regresion de Entrega 2.

## Validaciones

| Comando | Resultado | Error/correccion |
|---|---|---|
| `.venv/bin/pytest -q --cov=saas_platform --cov-report=term-missing` | 265 passed, 95.90% | Ningun fallo final |
| `make lint` | 81 archivos formateados, Ruff OK | Primer check detecto cinco tests sin formato; se formatearon |
| `make typecheck` | 61 archivos, Mypy estricto OK | Se anotaron dos helpers y casts de tests |
| `uv lock --check` | 77 paquetes resueltos, lock vigente | Sin cambios pendientes |
| `make build` | sdist y wheel creados | Primer intento sin DNS; repeticion autorizada OK |
| detect-secrets sobre tracked + untracked | OK, sin hallazgos | Credenciales/identificador ficticios marcados allowlist puntualmente |
| `make audit` | No known vulnerabilities found | Primer intento sin red; repeticion autorizada OK |
| suite de integracion PostgreSQL | Incluida en 265 passed | Testcontainers aprobado |
| `docker compose config --quiet` / `ps` | Config valida; PostgreSQL healthy | Ninguno |
| `alembic current` | `20260731_0001 (head)` | Sandbox bloqueo conexion inicial; repeticion autorizada OK |
| API `18002` live/ready | HTTP 200 / 200 | Puerto alterno temporal; cierre limpio |
| API/worker `18002/19002` metricas | Series tecnicas visibles | Procesos temporales cerrados limpiamente |
| metricas de extraccion | Lifecycle/duracion/tokens presentes | Registry verificado sin endpoint nuevo |

## Archivos

Nuevos:

- `src/saas_platform/modules/telecom_extraction/`: contratos, contexto, schema,
  prompt, processing/mapper, aplicacion, composicion, OpenAI/scripted, errores y
  telemetria;
- siete archivos unitarios de pruebas y `tests/extraction_fixtures.py`;
- analisis de impacto, documento especifico, ADR-019 y este informe.

Modificados:

- `ProfileField` en `lead_qualification/domain.py`, sin cambiar scoring/politica;
- `config.py`, `observability.py`, `.env.example`, `pyproject.toml`, `uv.lock`;
- README, indices y documentos de arquitectura/dependencias/deuda;
- tests existentes de configuracion y observabilidad.

Eliminados: ninguno.

## Migraciones

No se creo migracion. La capacidad se demuestra correctamente en memoria. Una
tabla en Entrega 2 no habilitaria aplicacion productiva y anticiparia
incorrectamente propietario Lead/Conversation, version/concurrencia, retencion,
RLS y replay. Entrega 3 debe disenar esos elementos juntos y aplicar extraccion,
perfil, dedupe y eventos en una frontera transaccional.

## Deuda tecnica

| ID | Deuda | Impacto | Prioridad/objetivo |
|---|---|---|---|
| TD-008 | Auditoria/idempotencia de extraccion solo en memoria | No resiste reinicio ni aplica trafico real | Bloqueante, Entrega 3 |
| TD-009 | Sin calibracion/canary con proveedor real | Precision, latencia, coste y drift desconocidos | Alta antes de produccion |
| TD-006 | Perfil/evaluacion sin persistencia | Sin historial comercial durable | Bloqueante, Entregas 3/4 |
| TD-004 | Warning TestClient/httpx2 | Riesgo de actualizacion futura | Entrega 2 general/mantenimiento |

## Proxima vertical recomendada

**Entrega 3: Integracion conversacional, persistencia incremental y aplicacion
idempotente de extracciones al LeadProfile.**

Debe definir Contact/Conversation/Lead, versionado y concurrencia, registros de
ejecucion/dedupe tenant-scoped, aplicacion atomica, RLS/retencion, recepcion y
continuacion conversacional, y pausa/transferencia por DNC/handoff. No se
implemento ninguna parte de esa vertical aqui.

## Veredicto

**ENTREGA 2 COMPLETADA CON DEUDA**

| Area | Estado | Evidencia | Pendiente |
|---|---|---|---|
| Puerto de extraccion | Completo | `CommercialExtractionProvider` sin SDK | Persistir orquestacion en E3 |
| Adaptador OpenAI | Completo contractual | Responses API y errores SDK offline | Canary real aprobado |
| Salidas estructuradas | Completo | Schema strict/additionalProperties false | Calibracion productiva |
| Validacion | Completo | Sintaxis, semantica, autoridad y referencias | Ninguno en E2 |
| Normalizacion | Completo | Moneda, fecha, cantidad, bool y texto | Catalogos externos fuera de alcance |
| Procedencia | Completo | Mensaje, evidencia, metodo, fecha, estado | Persistencia E3 |
| Confianza | Completo | Politica central y tratamiento por banda | Ajuste con dataset real |
| Contradicciones | Completo | Conservacion y conflicto; confirmado protegido | Resolucion humana operativa E3 |
| Informacion faltante | Completo | Matriz para 11 oportunidades | Politica tenant futura |
| Siguiente pregunta | Completo en propuesta | Validacion local y supresiones | Envio/historial E3 |
| No contacto | Completo | Prioridad incluso en degraded mode | Consentimiento durable E3 |
| Handoff sugerido | Completo | General/especializado con motivos | Asignacion/pausa E3 |
| Integracion con scoring | Completo | `QualificationRequest`; engine sin cambios | Composicion transaccional E3 |
| Seguridad | Completo en alcance | Tenant, redaccion, injection, secretos, limites | Revision regional/proveedor |
| Observabilidad | Completo tecnico | Logs, audit in-memory, metricas/tokens/coste | Storage/SLO productivo |
| Pruebas | Completo | 265 passed, global 95.90% | Canary no ejecutado |
| Documentacion | Completo | Impacto, ADR, arquitectura, reporte, indices | Actualizar al ejecutar E3 |
