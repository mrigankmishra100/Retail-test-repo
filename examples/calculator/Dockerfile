FROM public.ecr.aws/docker/library/python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY server.py index.html style.css app.js favicon.svg ./
RUN useradd --uid 10001 --create-home appuser
USER appuser
EXPOSE 8080
CMD ["python", "server.py"]
