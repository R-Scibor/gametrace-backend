FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
# Upgrade the installer, install the app, then remove the installer in this
# same layer. A later layer would hide pip from the running container but
# leave it in the image Trivy scans. ensurepip can recreate pip.
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt && \
    pip uninstall -y setuptools wheel pip && \
    rm -rf /usr/local/lib/python3.11/ensurepip \
           /usr/local/bin/pip* \
           /root/.cache/pip

COPY . .

ARG GIT_SHA=dev
ARG BUILD_TIME=unknown
ARG APP_VERSION=dev
ENV GIT_SHA=$GIT_SHA \
    BUILD_TIME=$BUILD_TIME \
    APP_VERSION=$APP_VERSION


CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010"]
