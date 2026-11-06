FROM python:3.12.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN sed -i 's/^Suites: bookworm bookworm-updates$/Suites: bookworm/' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install --no-install-recommends -y tesseract-ocr poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --requirement requirements.txt

COPY pipeline/ ./pipeline/

RUN groupadd --system --gid 10001 rahbar \
    && useradd --system --uid 10001 --gid rahbar --create-home --home-dir /home/rahbar rahbar

USER 10001:10001

CMD ["python", "-m", "pipeline.run"]
