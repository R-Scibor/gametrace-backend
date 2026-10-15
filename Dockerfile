FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ARG GIT_SHA=dev
ARG BUILD_TIME=unknown
ARG APP_VERSION=dev
ENV GIT_SHA=$GIT_SHA \
    BUILD_TIME=$BUILD_TIME \
    APP_VERSION=$APP_VERSION


CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010"]
