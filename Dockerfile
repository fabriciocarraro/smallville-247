# The whole app is stdlib Python plus two SDKs, so the image is tiny.
FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir typesafe-sdk openai

COPY smallville/ ./smallville/
COPY web/ ./web/
COPY run.py .

# The town is saved here. Mount a volume on this path or every redeploy
# resets the simulation to day 1 - which defeats the point of running it 24/7.
VOLUME ["/data"]
ENV HOST=0.0.0.0 PORT=8080 PYTHONUNBUFFERED=1

EXPOSE 8080
CMD ["python", "run.py", "--no-open", "--state", "/data/state.json"]
