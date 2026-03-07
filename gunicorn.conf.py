import os
import logging

bind = f"0.0.0.0:{os.environ.get('DATABRICKS_APP_PORT', '8000')}"
workers = 1  # PTY fds + sessions dict are process-local
threads = 32  # Support 20+ concurrent terminals polling + input + resize
worker_class = "gthread"
timeout = 30
graceful_timeout = 10  # Databricks gives 15s after SIGTERM
accesslog = "-"
errorlog = "-"
loglevel = "info"
# Structured access log: method path status response_time
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(L)ss'


def post_worker_init(worker):
    from app import initialize_app

    initialize_app()


def on_exit(server):
    logger = logging.getLogger("gunicorn.error")
    logger.info("Gunicorn shutting down — triggering state save")
    try:
        from state_sync import save_state

        save_state()
        logger.info("State saved on shutdown")
    except Exception as e:
        logger.error(f"Failed to save state on shutdown: {e}")
