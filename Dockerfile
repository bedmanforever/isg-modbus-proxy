FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY proxy.py .
ENV ISG_URL=http://isg POLL_INTERVAL=30
EXPOSE 502/tcp
CMD ["python", "-u", "proxy.py"]
