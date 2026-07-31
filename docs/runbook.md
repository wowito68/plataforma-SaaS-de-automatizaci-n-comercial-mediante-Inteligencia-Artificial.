# Runbook local

## Preparacion

```bash
cp .env.example .env
make install
make db-up
make migrate
make bootstrap
```

Los scripts de `infra/postgres/init` solo se ejecutan al crear el volumen. Si un
volumen antiguo no contiene los roles, recrealo conscientemente con
`docker compose down -v` y repite la preparacion; esto elimina todos los datos
locales.

Si `5432` ya esta ocupado, define otro `POSTGRES_HOST_PORT` en `.env` y usa ese
mismo puerto en las cuatro URLs PostgreSQL antes de ejecutar Compose.

## Procesos

```bash
make api
make worker
```

Deten ambos con `Ctrl-C`. `make db-down` detiene PostgreSQL sin borrar su volumen.

## Diagnostico

1. `curl -fsS http://127.0.0.1:8000/health/live` comprueba el proceso API.
2. `curl -fsS http://127.0.0.1:8000/health/ready` comprueba PostgreSQL runtime.
3. `curl -fsS http://127.0.0.1:8000/metrics` muestra metricas API.
4. `curl -fsS http://127.0.0.1:9001/metrics` muestra metricas del worker.
5. `docker compose logs postgres` permite revisar arranque y migraciones fallidas.

Un `401` en el adaptador indica token ausente/incorrecto. Un `404` no revela si
el evento existe en otro tenant. Un `409` indica reutilizacion de idempotency key
con payload distinto. Un `503` de readiness indica que la API no puede ejecutar
`SELECT 1` con `app_runtime`.

## Recuperacion de cola

Los mensajes `leased` vuelven a ser reclamables al vencer `lease_until`. Los
errores temporales aplican backoff y los permanentes o intentos agotados terminan
en `dead`. Solo se guarda el tipo de error, nunca payload o mensaje sensible.

No modifiques estados manualmente como operacion habitual. En Iteracion 1 no hay
comando de replay de dead letters: conserva filas y correlation IDs para
diagnostico y registra una correccion/migracion controlada si una prueba local lo
requiere.

## Pruebas y problemas frecuentes

`make test` requiere acceso al socket Docker y puede descargar `postgres:16-alpine`
la primera vez. Si el puerto 5432 esta ocupado, las pruebas siguen funcionando
porque Testcontainers asigna uno dinamico; Compose local requiere liberar o
cambiar el puerto publicado.

No uses las credenciales de `.env.example` fuera de una maquina local. Produccion
debe mantener el adaptador sintetico deshabilitado y no esta soportada por esta
iteracion.
