# Telecommunications Lead Qualification

## Estado y proposito

Estado: **Entrega 1 implementada; capacidad integral parcial**.

Este bounded context califica oportunidades comerciales de telecomunicaciones de
forma determinista, explicable, reproducible y aislada por empresa. El motor no
interpreta texto libre ni permite que una IA asigne el puntaje final: recibe un
perfil y senales normalizadas, resuelve una politica publicada y calcula el
resultado mediante reglas auditables.

Esta entrega no recibe conversaciones, no llama modelos de IA, no persiste leads
ni politicas, no ejecuta handoffs y no expone API o panel. Esos limites son
deliberados y evitan inventar propietarios de datos que el repositorio aun no
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

`LeadProfile` conserva los campos de producto, marca, modelo, capacidad, color,
presupuestos, pago, plazo, plan, consumo, llamadas, roaming, portabilidad,
operador, fecha de compra, lineas, tipo/tamano de cliente, ubicacion, cobertura,
inventario, accion, canal, sucursal e intencion.

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
| 5 identificar oportunidad | Tipo modelado; clasificador no implementado |
| 6-8 IA, esquema y normalizacion | No implementado; entrada actual ya normalizada |
| 9-12 comparar, conflictos, perfil y faltantes | Estructuras modeladas; actualizacion incremental no implementada |
| 13 siguiente pregunta | No implementado |
| 14-20 scoring, penalizaciones, bloqueos, clasificacion, accion, handoff | Implementado |
| 21 explicacion | Implementado |
| 22 versionar resultado | Referencia/fingerprint implementados; persistencia no |
| 23-25 eventos, metricas y continuacion/transferencia | No implementado |

El `trigger_id` y `correlation_id` quedan en cada resultado para conectarlo luego
con inbox/outbox. La idempotencia real debe vivir en la transaccion que actualice
el Lead; no se afirma que exista mientras ese agregado no este implementado.

## Siguiente mejor pregunta

No esta implementada en Entrega 1. El selector futuro recibira oportunidad,
hechos confiables, faltantes, impacto potencial, etapa, friccion y politica. No
preguntara un hecho ya confirmado, limitara preguntas consecutivas y cedera ante
una solicitud humana. Las preguntas no deben codificarse dentro del motor de
score porque conversacion y calificacion tienen ciclos de cambio distintos.

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

No hay metricas comerciales en esta entrega porque no existe almacenamiento ni
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
- La futura administracion necesita OIDC/RBAC, auditoria de lectura/publicacion y
  RLS; el bearer sintetico actual no es autenticacion de producto.
- Senales rechazadas o debajo de `0.70` no puntuan; una confirmacion humana puede
  hacer autoritativa una senal y queda identificada en el perfil.

## Ejemplos de referencia

| Caso | Resultado determinista de prueba |
|---|---|
| A. Portabilidad con equipo, presupuesto y esta semana | 82, caliente, elegibilidad pendiente, handoff inmediato |
| B. "Que celulares manejan" | 3, exploracion, cuatro faltantes, sin handoff |
| C. Modelo hoy sin inventario | 30, frio, -10 y buscar alternativas; al aceptarla, handoff |
| D. Empresa, 40 lineas, proximo mes | 35, frio por datos aun limitados, handoff especializado |
| E. "No me escriban" | 0, detener automatizacion, estado no contactar, sin handoff |

Los casos muestran por que score, estado, bloqueo, elegibilidad y accion son ejes
separados. Un caso empresarial puede requerir especialista con score modesto y
un equipo agotado bloquea una oferta, no al prospecto.

## Proxima entrega

Recomendacion: **Entrega 2, extraccion estructurada mediante IA**, sin integrarla
aun a WhatsApp ni crear CRUDs. Debe definir esquemas validados para oportunidad,
hechos, senales y contradicciones; prompts versionados; umbrales/fallbacks;
adaptador sustituible; pruebas de salida invalida, timeout y proveedor caido. Su
salida seguira siendo evidencia no confiable para este motor determinista.
