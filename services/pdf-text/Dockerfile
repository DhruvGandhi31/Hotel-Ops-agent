FROM python:3.12.15-slim-trixie

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# Exactly the pypdf version in requirements-dev.txt, so the eval harness and this service extract
# identical text (a test compares the two pins).
RUN pip install pypdf==6.19.0

WORKDIR /app
COPY pdf_text.py app.py ./

USER nobody
EXPOSE 8000
CMD ["python", "app.py"]
