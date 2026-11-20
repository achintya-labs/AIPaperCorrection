FROM docker.io/library/python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Same UID as the worker image: both containers write to the shared storage volume, and a
# named volume takes its ownership from this directory when first created.
RUN useradd -m -u 1000 app && mkdir -p /data/storage && chown -R app /data /srv
USER app
