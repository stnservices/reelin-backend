release: alembic upgrade head
web: uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
worker: celery -A app.celery_app:celery_app worker --loglevel=info
