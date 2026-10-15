"""Celery app. In development and tests CELERY_EAGER=true runs tasks inline, so Redis and a worker are optional."""

from celery import Celery

from app.core.config import settings

celery_app = Celery("pinesphere", broker=settings.REDIS_URL, backend=settings.REDIS_URL, include=["app.worker.tasks"])
celery_app.conf.update(
    timezone="Asia/Kolkata",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_always_eager=settings.CELERY_EAGER,
    task_eager_propagates=False,
    task_ignore_result=True,
    beat_schedule={
        "expire-quotes": {"task": "orders.expire_quotes", "schedule": 600.0},
    },
)


@celery_app.task(name="system.ping")
def ping() -> str:
    return "pong"
