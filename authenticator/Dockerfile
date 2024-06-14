FROM python:3.7-alpine3.7

RUN apk update && apk upgrade && apk add --no-cache git && \
	apk add --no-cache --virtual .build-deps gcc g++ musl-dev libffi-dev postgresql-dev build-base

WORKDIR /app

COPY requirements.txt /

RUN pip install -r /requirements.txt

COPY . /app

ENV PORT 80
CMD ["gunicorn", "-w 2", "-k uvicorn.workers.UvicornWorker", "-b 0.0.0.0:80", "server:app"]