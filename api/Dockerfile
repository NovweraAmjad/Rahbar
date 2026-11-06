FROM python:3.12.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --requirement requirements.txt

COPY app/ ./app/

RUN groupadd --system --gid 10001 rahbar \
    && useradd --system --uid 10001 --gid rahbar --create-home --home-dir /home/rahbar rahbar

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
