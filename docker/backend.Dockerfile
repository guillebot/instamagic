# Pin to multi-arch manifest-list digest (index), not a per-arch manifest.
FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2

RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY backend /app
RUN pip install --no-cache-dir .
COPY docker/entrypoint.py /entrypoint.py
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "/entrypoint.py"]
CMD ["uvicorn", "instamagic.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
