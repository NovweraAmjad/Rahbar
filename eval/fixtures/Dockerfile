FROM python:3.12.12-alpine

WORKDIR /app
COPY embedding_server.py /app/embedding_server.py
USER 10001:10001
EXPOSE 8081
CMD ["python", "-u", "/app/embedding_server.py"]
