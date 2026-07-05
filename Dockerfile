FROM python:3.14-alpine

RUN apk update && apk upgrade
RUN apk add --no-cache git
RUN apk add --no-cache gcc
RUN apk add --no-cache g++
RUN apk add --no-cache musl-dev
RUN apk add --no-cache libffi-dev
# RUN apk add --no-cache postgresql-dev
RUN apk add --no-cache build-base
RUN apk add --no-cache bash
RUN apk add --no-cache linux-headers
RUN apk add --no-cache libuv
RUN apk add --no-cache libuv-dev
RUN apk add --no-cache openssl
RUN apk add --no-cache openssl-dev
RUN apk add --no-cache lua5.1
RUN apk add --no-cache lua5.1-dev
RUN apk add --no-cache zlib
RUN apk add --no-cache zlib-dev
RUN apk add --no-cache python3-dev
RUN apk add --no-cache libpng-dev
RUN apk add --no-cache libjpeg-turbo-dev
RUN apk add --no-cache tiff-dev
RUN apk add --no-cache libwebp-dev
RUN apk add --no-cache libev
RUN apk add --no-cache libev-dev
RUN apk add --no-cache imagemagick
RUN apk add --no-cache exiftool
RUN apk add --no-cache jq

RUN rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app
RUN chmod +x docker-exec.sh

ENV MAGICK_HOME=/usr
RUN find /usr/lib -name "libMagickCore*" -maxdepth 1 -type f -exec ln -s {} /usr/lib/libMagickCore.so \;
RUN find /usr/lib -name "libMagickWand*" -maxdepth 1 -type f -exec ln -s {} /usr/lib/libMagickWand.so \;
RUN mkdir "images"

RUN wget https://go.dev/dl/go1.22.5.linux-amd64.tar.gz && \
	tar -xvf go1.22.5.linux-amd64.tar.gz -C /usr/local && \
	rm go1.22.5.linux-amd64.tar.gz

ENV GOROOT=/usr/local/go
# redefine $HOME to the default so that it stops complaining
ENV HOME=/root
ENV GOPATH=$HOME/go
ENV PATH=$GOPATH/bin:$GOROOT/bin:$PATH

RUN python3 -m venv /opt/.venv

RUN /opt/.venv/bin/python3 -m pip install -r requirements.lock --no-deps --ignore-requires-python
RUN go install github.com/kheina-com/go-thumbhash/cmd/thumbhash@9146e72

# install things before setting path
ENV PATH="/opt/.venv/bin:$PATH"

ENV PORT=80
ENV ENVIRONMENT=DEV
CMD ["./docker-exec.sh"]
