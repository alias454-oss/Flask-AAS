ARG PYTHON_VERSION=3.13.13
FROM python:${PYTHON_VERSION}-slim-trixie

WORKDIR /base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_APP=app \
    PYTHONPATH=/base \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Install the fully pinned, hash-verified runtime dependencies.
# The lock is expected to provide wheels for the deployment platform.
COPY requirements.txt ./
RUN python -m pip install \
    --require-hashes \
    --only-binary=:all: \
    -r requirements.txt

RUN groupadd --system flaskaas \
    && useradd \
        --system \
        --gid flaskaas \
        --home-dir /base \
        --no-create-home \
        --shell /bin/bash \
        flaskaas \
    && chown flaskaas:flaskaas /base

COPY --chown=flaskaas:flaskaas . /base
RUN chmod +x /base/entrypoint.sh

USER flaskaas:flaskaas

EXPOSE 5000

ENTRYPOINT ["/base/entrypoint.sh"]
