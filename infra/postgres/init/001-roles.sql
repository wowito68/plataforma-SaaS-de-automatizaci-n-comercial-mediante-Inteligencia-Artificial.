CREATE ROLE app_owner LOGIN PASSWORD 'development-only-owner' NOSUPERUSER NOBYPASSRLS;
CREATE ROLE app_runtime LOGIN PASSWORD 'development-only-runtime' NOSUPERUSER NOBYPASSRLS;
CREATE ROLE app_worker LOGIN PASSWORD 'development-only-worker' NOSUPERUSER BYPASSRLS;

ALTER DATABASE saas_platform OWNER TO app_owner;
ALTER SCHEMA public OWNER TO app_owner;

GRANT CONNECT ON DATABASE saas_platform TO app_runtime, app_worker;
GRANT USAGE ON SCHEMA public TO app_runtime, app_worker;
