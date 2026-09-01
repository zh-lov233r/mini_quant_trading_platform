FROM python:3.12-slim AS native-builder

RUN apt-get update \
    && apt-get install --no-install-recommends -y g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY backend/native /build/native

ARG QUANT_KERNEL_BUILD_ID=docker
ENV QUANT_KERNEL_BUILD_ID=${QUANT_KERNEL_BUILD_ID}

RUN pip wheel --no-cache-dir --wheel-dir /wheels /build/native


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend

WORKDIR /app/backend

COPY backend/requirements.txt /tmp/requirements.txt
COPY --from=native-builder /wheels /tmp/wheels

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip install --no-cache-dir /tmp/wheels/quant_kernel-*.whl \
    && rm -rf /tmp/wheels

COPY backend /app/backend

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
