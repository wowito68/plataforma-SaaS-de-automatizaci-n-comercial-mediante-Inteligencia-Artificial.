import logging
import signal
from threading import Event

from prometheus_client import start_http_server

from saas_platform.bootstrap import Container
from saas_platform.config import get_settings
from saas_platform.observability import configure_logging

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging(
        environment=settings.app_env,
        service=f"{settings.service_name}-worker",
        level=settings.log_level,
    )
    container = Container.build(settings)
    stopped = Event()

    def stop(_: int, __: object) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if settings.metrics_enabled:
        start_http_server(settings.worker_metrics_port)

    logger.info("worker started", extra={"operation": "worker.start", "result": "ready"})
    try:
        while not stopped.is_set():
            try:
                container.worker.run_cycle()
            except Exception as error:
                logger.exception(
                    "worker cycle failed",
                    extra={
                        "operation": "worker.cycle",
                        "error_type": type(error).__name__,
                        "result": "failed",
                    },
                )
            stopped.wait(settings.queue_poll_interval_ms / 1000)
    finally:
        container.close()
        logger.info("worker stopped", extra={"operation": "worker.stop", "result": "ok"})


if __name__ == "__main__":
    main()
