# ADR-016: Stack Python para Iteracion 1

- **Estado:** Provisional, aceptado para Iteracion 1
- **Contexto:** La baseline dejo B-05 sin resolver y el repositorio estaba vacio.
- **Opciones:** TypeScript/Node.js, Python, aplazar la implementacion.
- **Decision:** Python 3.12, uv, FastAPI, SQLAlchemy Core, Alembic y Psycopg.
- **Razon:** El toolchain esta disponible, el stack es pequeno, tipable y
  adecuado para API, worker y PostgreSQL sin condicionar proveedores futuros.
- **Consecuencias:** Un runtime y un gestor de paquetes; se debe proteger el
  dominio de FastAPI y SQLAlchemy.
- **Riesgo:** La experiencia real del equipo no esta documentada.
- **Revision:** Antes de Iteracion 2 o si el equipo demuestra mayor experiencia
  operativa en otro stack.
