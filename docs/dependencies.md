# Dependencias

| Dependencia | Proposito | Alternativas | Razon de seleccion | Riesgo |
|---|---|---|---|---|
| FastAPI | Adaptador HTTP y validacion | Starlette, Flask | Tipado y manejo de limites sin framework empresarial | Acoplar modelos HTTP a aplicacion |
| Pydantic Settings | Configuracion validada | Validacion manual | Una unica carga tipada y fallo temprano | Cambios entre versiones mayores |
| SQLAlchemy | Transacciones y SQL parametrizado | Psycopg directo | Core SQL expresivo sin exponer ORM al dominio | Abstraccion adicional |
| Psycopg | Driver PostgreSQL | asyncpg | Driver oficial moderno y modo sincronico simple | Binarios por plataforma |
| Alembic | Migraciones | Scripts SQL manuales | Historial y ejecucion desde base vacia | Migraciones autogeneradas sin revision |
| uuid6 | UUIDv7 | UUIDv4 o implementacion propia | Cumple la estrategia aprobada sin criptografia propia | Dependencia pequena adicional |
| prometheus-client | Metricas | Contadores propios | Formato interoperable y estable | Cardinalidad si se etiqueta tenant |
| Uvicorn | Servidor ASGI | Hypercorn | Integracion directa y madura | Debe configurarse por entorno |
| OpenAI SDK | Adaptador de salida estructurada Responses API | HTTP directo, otro SDK | Cliente oficial con `responses.parse`; aislado detras del puerto | Cambio de API/modelo y dependencia de proveedor |
| Pydantic | Esquema estricto de salida y tipos acotados | Validacion manual, jsonschema | Ya es base del stack y genera JSON Schema compatible | Semantica de schema al cambiar version mayor |
| Ruff | Formato y lint | Black + Flake8 | Una herramienta rapida y mantenida | Reglas nuevas al actualizar |
| Mypy | Type checking | Pyright | Compatible con stack Python y CI | Tipos de librerias incompletos |
| Pytest | Pruebas | unittest | Fixtures legibles y amplio soporte | Fixtures excesivas |
| Testcontainers | PostgreSQL real en pruebas | DB compartida | Aislamiento reproducible y valida RLS | Requiere Docker |
| detect-secrets | Deteccion basica | Gitleaks | Ejecutable con el mismo entorno Python | Falsos positivos/baseline |
| pip-audit | Vulnerabilidades | Scanner externo | Audita el lock de Python | Depende de base de vulnerabilidades |

No se agregan frameworks de agentes, RAG, mensajeria, cache, brokers, motores de
workflow ni SDKs de IA adicionales. El SDK oficial solo aparece en el adaptador;
dominio, aplicacion y pruebas de flujo dependen del puerto.
