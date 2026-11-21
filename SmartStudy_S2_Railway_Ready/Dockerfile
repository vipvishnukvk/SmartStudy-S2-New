FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py planner_data.json ./
COPY web ./web
RUN mkdir -p /data/uploads && useradd -r -u 10001 smartstudy && chown -R smartstudy:smartstudy /data /app
USER smartstudy
ENV FILES_DIR=/data/uploads
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
