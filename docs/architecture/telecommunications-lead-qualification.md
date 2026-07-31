# Telecommunications Lead Qualification

## Estado y proposito

Estado: **Entregas 1 y 2 implementadas; capacidad integral parcial**.

Este bounded context califica oportunidades comerciales de telecomunicaciones de
forma determinista, explicable, reproducible y aislada por empresa. La capacidad
adyacente `telecom_extraction` interpreta fragmentos de texto mediante una salida
estructurada no confiable, la valida y propone un perfil/senales normalizados. El
motor no permite que una IA asigne el puntaje final: resuelve una politica
publicada y calcula el resultado mediante reglas auditables.

La Entrega 2 puede procesar mensajes suministrados en memoria y ofrece un
adaptador OpenAI opt-in, pero no recibe WhatsApp, no persiste conversaciones,
leads, extracciones o politicas, no ejecuta handoffs y no expone API o panel.
Esos limites evitan inventar propietarios de datos que el repositorio aun no
tiene.

## Alcance de dominio

| Concepto | Forma | Responsabilidad |
|---|---|---|
| `Opportunity` | Value object | Oportunidad primaria y secundarias sin duplicados |
| `LeadProfile` | Snapshot inmutable | Hechos estructurados, faltantes y contradicciones |
| `ProfileFact` | Value object | Valor con fuente, mensaje, fecha, confianza y validacion |
| `CommercialSignal` | Value object | Evidencia comercial normalizada y allowlisted |
| `ScoringPolicy` | Politica inmutable | Reglas, pesos, umbrales, decisiones y version |
| `LeadScoringEngine` | Servicio de dominio | Calculo puro y determinista |
| `QualificationResult` | Registro historico conceptual | Resultado completo con explicacion y referencia de politica |
| `EvaluateLeadQualification` | Caso de uso | Resuelve politica tenant/oportunidad y suministra ID/fecha |
| `ScoringPolicyProvider` | Puerto | Obtiene la version publicada sin acoplar almacenamiento |
| `CommercialExtractionProvider` | Puerto | Solicita una extraccion comercial estructurada sin tipos del SDK |
| `TelecomExtractionResult` | Resultado por etapas | Procedencia, confianza, controles, conflictos, pregunta y auditoria |
| `ExtractionDomainMapper` | Servicio de aplicacion | Propone perfil inmutable y construye `QualificationRequest` |

No se introdujo un agregado `Lead`: su propiedad, concurrencia y limite
transaccional deben definirse junto con contacto y conversacion en la Entrega 3.

## Tipos de oportunidad

La politica se selecciona por `tenant_id` y oportunidad primaria. Una oportunidad
puede conservar tipos secundarios para representar combinaciones sin duplicar el
lead.

| Tipo | Uso principal | Compatibilidad base |
|---|---|---|
| `device_purchase` | Compra de equipo | Equipo |
| `device_with_plan` | Equipo con plan | Equipo y plan |
| `plan_subscription` | Contratacion de plan | Plan |
| `new_line` | Linea nueva | Plan |
| `portability` | Cambio conservando numero | Plan |
| `renewal` | Renovacion | Equipo y plan |
| `prepaid_to_postpaid` | Migracion de modalidad | Plan |
| `accessory` | Accesorio | Requisitos generales |
| `multiple_lines` | Varias lineas | Equipo y plan |
| `business_account` | Cuenta empresarial | Equipo y plan |
| `unidentified` | Intencion aun no resuelta | Requisitos generales |

Las reglas de plan/equipo se omiten cuando no aplican. Lo desconocido no se
interpreta como incompatible y la ausencia de informacion no equivale a una
respuesta negativa.

## Perfil comercial

`LeadProfile` conserva campos de producto, marca, modelo, capacidad, color,
condicion, cantidad, precios/presupuestos, financiamiento declarado, pago, plazo,
plan, consumo, llamadas/mensajes/redes, roaming/internacionales, modalidad,
portabilidad, operador, fechas, lineas/equipos, tipo/tamano/rol de cliente,
facturacion/administracion, ubicacion, cobertura, inventario, accion, canal,
sucursal e intencion. Cobertura, inventario y elegibilidad solo pueden hacerse
autoritativos mediante fuentes externas autorizadas.

Cada `ProfileFact` registra:

- valor;
- fuente (`customer`, `system_lookup`, `human_advisor`, `import`);
- mensaje de origen;
- fecha con zona horaria;
- confianza entre 0 y 1;
- metodo (`explicit`, `ai`, `human`, `system`, `import`);
- estado (`hypothesis`, `accepted`, `confirmed`, `rejected`);
- actor que confirma, obligatorio para hechos confirmados.

El mapa se copia y expone de forma inmutable. `FactConflict` conserva valor
anterior, valor actual, motivo y fecha; ningun dato contradictorio debe
sobrescribirse silenciosamente.

## Politica inicial

### Dimensiones y pesos

| Dimension | Maximo |
|---|---:|
| Necesidad y compatibilidad | 25 |
| Intencion de compra | 25 |
| Presupuesto y pago | 20 |
| Urgencia | 15 |
| Participacion | 10 |
| Potencial comercial | 5 |

Los maximos suman exactamente 100. Cada dimension aplica su propio tope antes de
penalizaciones y el resultado final se limita a 0..100. Los puntos son enteros,
por lo que esta version no requiere redondeo.

### Necesidad y compatibilidad

| Regla | Puntos | Aplicacion |
|---|---:|---|
| Necesidad definida | 5 | Todos los tipos |
| Plan compatible | 5 | Oportunidades con plan |
| Equipo compatible | 5 | Oportunidades con equipo |
| Cobertura confirmada | 5 | Todos los tipos cuando corresponde |
| Requisitos preliminares compatibles | 5 | Todos los tipos |

### Intencion de compra

Estas reglas pertenecen al grupo exclusivo `purchase_intent_level`: si coinciden
varias, solo aporta la de mayor valor.

| Senal | Puntos |
|---|---:|
| Informacion general | 3 |
| Pregunta precio | 7 |
| Pregunta mensualidad | 10 |
| Pregunta disponibilidad | 12 |
| Compara productos | 15 |
| Pregunta requisitos | 18 |
| Solicita cotizacion | 20 |
| Solicita portabilidad | 22 |
| Quiere iniciar contratacion | 25 |

### Presupuesto y pago

Grupo exclusivo `budget_level`:

| Situacion explicita | Puntos |
|---|---:|
| Aun no define presupuesto | 2 |
| Presupuesto incompatible | 5 |
| Acepta alternativas | 10 |
| Presupuesto compatible | 15 |
| Presupuesto y pago definidos | 20 |

No se asignan los 2 puntos por mera ausencia del dato; debe existir una senal
explicita y confiable de presupuesto aun no definido.

### Urgencia

Grupo exclusivo `urgency_level`:

| Plazo | Puntos |
|---|---:|
| Solo investiga | 2 |
| Mas de tres meses | 5 |
| Proximo mes | 9 |
| Esta semana | 12 |
| Inmediato | 15 |

### Participacion

| Evidencia de avance | Puntos |
|---|---:|
| Proporciona informacion relevante | 3 |
| Responde preguntas de calificacion | 3 |
| Mantiene progreso comercial | 2 |
| Acepta llamada o visita | 2 |

Se mide calidad de avance, no cantidad bruta de mensajes.

### Potencial comercial

| Evidencia | Puntos |
|---|---:|
| Equipo y plan | 2 |
| Accesorio | 1 |
| Varias lineas | 3 |
| Cuenta empresarial | 5 |

Las evidencias pueden coexistir, pero la dimension se limita a 5.

## Clasificaciones

| Puntaje | Clasificacion | Accion base |
|---:|---|---|
| 0-29 | Exploracion | Resolver dudas y presentar opciones |
| 30-49 | Frio | Seguimiento automatizado |
| 50-69 | Tibio | Recopilar informacion faltante |
| 70-84 | Caliente | Notificar asesor |
| 85-100 | Prioritario | Transferencia inmediata |

Las bandas cubren 0..100 sin huecos ni solapamientos. Las decisiones bloqueantes
y de prioridad pueden sustituir la accion base sin falsificar el score.

## Penalizaciones y efectos

| Regla | Ajuste | Efecto | Accion/bloqueo |
|---|---:|---|---|
| Sin cobertura | -20 | Bloqueo de oferta | Buscar alternativa; bloquea oferta solicitada |
| Equipo sin inventario | -10 | Bloqueo de oferta | Buscar alternativa; bloquea equipo solicitado |
| Presupuesto incompatible, sin alternativas | -15 | Penalizacion | Conserva oportunidad segun politica |
| Compra en mas de seis meses | -8 | Penalizacion | Conserva trazabilidad |
| Informacion contradictoria | -5 | Penalizacion | Revision humana |
| Inactividad prolongada | -5 | Exclusion temporal | No equivale a perdida definitiva |
| Solicitud de no contacto | 0 | No contactar | Detener automatizacion |

`penalty`, `offer_block`, `disqualification`, `temporary_exclusion` y
`do_not_contact` son efectos diferentes. No contacto no es un numero negativo:
siempre produce `STOP_AUTOMATION`, recomienda estado `DO_NOT_CONTACT` y suprime
cualquier handoff paralelo.

## Handoff

Las decisiones se evaluan por prioridad descendente:

| Prioridad | Condicion | Resultado |
|---:|---|---|
| 1000 | No contacto | Detener; sin handoff |
| 950 | Solicita humano | Transferencia inmediata |
| 900 | Empresa y multiples lineas | Handoff especializado |
| 850 | Quiere contratar ahora | Transferencia inmediata |
| 800 | Portabilidad | Transferencia inmediata |
| 700 | Solicita cotizacion | Notificar asesor |
| 690 | Equipo agotado y acepta alternativa | Transferencia inmediata |
| 650 | Requiere revision humana | Notificar asesor |
| 600 | Informacion contradictoria | Notificar asesor |

Un score caliente/prioritario tambien activa handoff por su accion de banda. El
resultado incluye `required`, `specialized` y codigos de motivo. La ejecucion
operativa del handoff, pausa de IA, asignacion y reanudacion controlada pertenecen
a la Entrega 3.

## Elegibilidad

La elegibilidad es un eje externo: `pending_validation`, `eligible`,
`eligible_with_conditions`, `not_eligible`, `manual_review_required` o
`not_applicable`. El motor copia este valor al resultado y nunca lo deriva de la
calificacion. Un lead 100/100 puede seguir pendiente de validacion financiera.

## Estados

El dominio reconoce: nuevo, en conversacion, calificando, informacion
incompleta, calificado, cotizacion solicitada/enviada, pendiente de documentos,
validacion, inventario, cobertura o financiamiento, transferido/contactado por
asesor, portabilidad/contratacion iniciada, venta completada, perdido, no
elegible, duplicado y no contactar.

Entrega 1 solo emite una recomendacion de estado para decisiones inequivocas. La
maquina transaccional pertenece al agregado futuro de Lead; no esta implementada.
Su contrato propuesto para Entrega 3 es:

| Origen | Destinos permitidos principales | Actor/causa |
|---|---|---|
| Nuevo | En conversacion, duplicado, no contactar | Mensaje/sistema/consentimiento |
| En conversacion | Calificando, transferido, perdido, no contactar | Sistema, asesor o cliente |
| Calificando | Informacion incompleta, calificado, pendientes, transferido | Motor o asesor |
| Informacion incompleta | Calificando, transferido, perdido, no contactar | Nueva evidencia o actor humano |
| Calificado | Cotizacion solicitada, transferido, portabilidad/contratacion | Cliente, regla o asesor |
| Cotizacion solicitada | Cotizacion enviada, transferido, perdido | Asesor/integracion |
| Cotizacion enviada | Pendientes, contratacion iniciada, perdido | Asesor/integracion |
| Estados pendientes | Calificando, calificado, transferido, no elegible, perdido | Resultado autorizado |
| Transferido | Contactado por asesor, calificado, perdido | Asignacion/asesor |
| Contactado | Cotizacion, portabilidad, contratacion, perdido | Asesor |
| Portabilidad/contratacion | Venta completada, pendiente, perdido | Sistema autorizado/asesor |

Estados terminales: venta completada, perdido, no elegible, duplicado y no
contactar. Reapertura solo manual, autorizada y auditada; `do_not_contact`
requiere un nuevo consentimiento valido y `duplicate` debe enlazar al registro
canonico. Ninguna transicion debe aceptarse por asignacion arbitraria.

## Pipeline

| Pasos solicitados | Estado en esta entrega |
|---|---|
| 1-4 recibir/persistir/resolver tenant-contacto-conversacion | Existente solo para evento sintetico; no integrado a leads |
| 5 identificar oportunidad | Extraccion estructurada reconoce los 11 tipos y evidencia |
| 6-8 IA, esquema y normalizacion | Puerto, OpenAI/scripted, esquema estricto y normalizacion implementados |
| 9-12 comparar, conflictos, perfil y faltantes | Propuesta inmutable en memoria; aplicacion durable pendiente |
| 13 siguiente pregunta | Selector local implementado; envio conversacional pendiente |
| 14-20 scoring, penalizaciones, bloqueos, clasificacion, accion, handoff | Implementado |
| 21 explicacion | Implementado |
| 22 versionar resultado | Referencia/fingerprint implementados; persistencia no |
| 23-25 eventos, metricas y continuacion/transferencia | Metricas tecnicas IA implementadas; eventos/ejecucion pendientes |

El `trigger_id` y `correlation_id` quedan en cada resultado para conectarlo luego
con inbox/outbox. Entrega 2 crea una clave SHA-256 estable por tenant,
conversacion, mensajes, prompt, esquema y operacion. La idempotencia real debe
vivir en la transaccion que actualice el Lead; no se afirma que la deduplicacion
durable exista mientras ese agregado no este implementado.

## Siguiente mejor pregunta

Entrega 2 calcula faltantes distintos por oportunidad y selecciona una sola
pregunta. La propuesta del proveedor se acepta solo si apunta al primer dato
faltante/conflictivo, no fue preguntado recientemente, es breve y no solicita
telefono, correo, credenciales, tarjeta o identificadores oficiales. Si falla,
se usa una plantilla local segura. DNC, solicitud humana y handoff confirmado
suprimen la pregunta. El envio, historial durable de preguntas y control de
friccion entre sesiones pertenecen a Entrega 3.

El pipeline completo, esquema, prompts, confianza, errores y ejemplos estan en
[Telecommunications AI Structured Extraction](telecommunications-ai-structured-extraction.md).

## Explicabilidad y auditoria

Cada resultado contiene:

- score, subtotal y clasificacion;
- puntos crudos/aplicados/maximos por dimension;
- contribuciones con regla, senales, puntos y explicacion;
- penalizaciones, efectos y ofertas bloqueadas;
- senales efectivamente usadas;
- informacion faltante;
- accion, handoff y recomendacion de estado;
- elegibilidad externa;
- politica: ID, tenant, oportunidad, version y SHA-256 canonico;
- version del motor;
- fecha, causa, trigger y correlacion;
- proveedor/modelo/prompt/esquema/confianza de extraccion, cuando existan.

El fingerprint detecta cambios y el snapshot de referencia impide que una nueva
politica altere el resultado en memoria. Para reconstruccion durable aun se debe
persistir el snapshot completo de entrada, reglas aplicadas y resultado.

## Configuracion multiempresa

`ScoringPolicy` valida al construirse:

- tenant y oportunidad obligatorios;
- version positiva y estado publicado para evaluar;
- seis dimensiones unicas que suman 100;
- codigos de regla unicos;
- criterio menor o igual al tope de su dimension;
- bandas completas sin huecos;
- confianza minima 0..1;
- fecha con zona horaria.

El puerto consulta por tenant y oportunidad y el motor vuelve a verificar ambos,
evitando confiar solo en el adaptador. `build_default_telecom_policy` materializa
una copia base tenant-scoped. Persistir borradores, publicar atomica y
exclusivamente una version, comparar/simular y autorizar administradores es
Entrega 4.

## Modelo de datos futuro

No hubo migracion. Cuando Lead/Conversation tengan propietario, el diseno minimo
debera separar:

| Estructura conceptual | Contenido |
|---|---|
| `lead_profiles` + `lead_profile_facts` | Snapshot, fuente, confianza y confirmacion |
| `lead_fact_conflicts` | Valores anterior/nuevo y resolucion |
| `lead_opportunities` | Tipo primario/secundarios y ciclo de vida |
| `commercial_signals` | Senal normalizada, mensaje, extractor y validacion |
| `scoring_policies` + `scoring_policy_versions` | Borrador/publicada/retirada y reglas inmutables |
| `qualification_evaluations` | Una fila append-only por recalculo |
| `qualification_contributions` | Dimensiones, reglas y penalizaciones reconstruibles |
| `lead_state_transitions` | Origen/destino, actor, causa y efectos |
| `handoff_requests` | Motivo, resumen, asignacion y pausado de automatizacion |
| `eligibility_assessments` | Fuente autorizada y resultado independiente |

Todas las filas empresariales necesitaran `tenant_id`, PK/FK compuestas, RLS
forzado y politicas de retencion. No se guardara solo el ultimo score.

## Eventos e integraciones

No se crearon eventos sin consumidor. Los candidatos para Entrega 3/4 son:

| Evento | Productor | Consumidor/efecto esperado | Idempotencia |
|---|---|---|---|
| `LeadProfileUpdated` | Agregado Lead | Recalificar y proyectar faltantes | lead/version/message |
| `LeadScoreCalculated` | Calificacion | Historial y metricas | evaluation ID |
| `LeadClassificationChanged` | Lead | Priorizacion/panel | transition ID |
| `HandoffRequested` | Orquestador | Asignar asesor y pausar IA | handoff ID |
| `EligibilityUpdated` | Adaptador autorizado | Actualizar estado sin inferir score | provider assessment ID |
| `DoNotContactRequested` | Conversacion | Consentimiento y bloqueo de automatizacion | contact/request ID |

Catalogo, planes, promociones, inventario, cobertura, requisitos, financiamiento
y sucursales deben usar puertos separados y devolver estados normalizados. El
motor nunca importara SDKs de operador o proveedor.

## Metricas y calibracion

Entrega 2 agrega metricas tecnicas de extracciones iniciadas/completadas/fallidas,
errores, reparacion, duracion, confianza, campos, contradicciones, DNC, handoff,
tokens y coste opcional por proveedor/modelo/version. IDs, telefono y contenido
no son etiquetas. No hay metricas comerciales porque no existe almacenamiento ni
outcome real. La futura proyeccion medira distribucion/promedio por clasificacion
y oportunidad, cambios, faltantes, tiempo/preguntas, handoff, cotizacion,
portabilidad, contratacion, venta, conversion por rango/dimension/producto/plan/
campana/tenant, falsos positivos/negativos y coste de IA.

La calibracion comparara la version historica con cotizacion, venta, perdida,
tiempo y valor. Evaluara precision de prioritarios, recall de conversiones,
distribucion, sesgo y reglas sin valor. No se entrenara un modelo predictivo sin
volumen, calidad y aprobacion suficientes; nunca se mezclaran tenants sin
consentimiento explicito.

## Seguridad y privacidad

- La entrada de reglas es una lista cerrada de senales comerciales; no incluye
  raza, religion, orientacion sexual, discapacidad, salud, opiniones politicas u
  otros atributos protegidos.
- El motor exige coincidencia de tenant y oportunidad incluso si el provider se
  equivoca.
- Conversaciones, telefonos, documentos, presupuestos y decisiones financieras
  no deben aparecer completos en logs.
- El contexto se limita por mensajes, caracteres, resumen y antiguedad; telefono,
  correo y RFC/CURP-like se redactan antes del proveedor.
- Mensajes y resumen son datos no confiables. Prompt injection se detecta y no
  puede agregar score, elegibilidad, politica, permisos o datos cross-tenant.
- El esquema prohibe propiedades desconocidas; cobertura/inventario declarados
  por el modelo se bloquean antes del perfil.
- La futura administracion necesita OIDC/RBAC, auditoria de lectura/publicacion y
  RLS; el bearer sintetico actual no es autenticacion de producto.
- Senales rechazadas o debajo de `0.70` no puntuan; una confirmacion humana puede
  hacer autoritativa una senal y queda identificada en el perfil.

## Ejemplos de referencia

| Caso | Extraccion Entrega 2 | Resultado determinista de referencia |
|---|---|---|
| A. Portabilidad con equipo, presupuesto y esta semana | Portabilidad + equipo/plan, 600 MXN, Samsung, financiamiento declarado, esta semana, handoff | 82, caliente, elegibilidad pendiente |
| B. "Que celulares manejan" | Exploracion, faltantes relevantes y una pregunta | 3, exploracion, sin handoff |
| C. Galaxy S25 Ultra hoy | Modelo e inmediato; inventario desconocido | Un lookup autorizado posterior puede aportar stock; la IA no aplica -10 |
| D. Empresa, 40 lineas, proximo mes | Empresa, volumen, equipos, plazo y handoff especializado | 35, frio por datos aun limitados |
| E. "No me escriban" | DNC incluso con proveedor invalido; sin pregunta/handoff | 0, detener y estado no contactar |

Los casos muestran por que score, estado, bloqueo, elegibilidad y accion son ejes
separados. Un caso empresarial puede requerir especialista con score modesto y
un equipo agotado bloquea una oferta, no al prospecto.

## Proxima entrega

Recomendacion: **Entrega 3: Integracion conversacional, persistencia incremental
y aplicacion idempotente de extracciones al LeadProfile**. Debe definir propiedad
de Contact/Conversation/Lead, versionado incremental, ejecucion/deduplicacion
durable, transaccion de aplicacion, RLS, retencion, pausa por DNC/handoff y eventos.
No esta implementada en Entrega 2.
