# Nạp Celery app khi Django khởi động để @shared_task hoạt động.
from .celery import app as celery_app

__all__ = ("celery_app",)
