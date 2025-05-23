FROM python:3.13-alpine

RUN apk update && \
	apk upgrade && \
	apk add --no-cache git && \
	apk add --no-cache --virtual \
	.build-deps \
	gcc \
	g++ \
	musl-dev \
	libffi-dev \
	postgresql-dev \
	build-base \
	bash \
	linux-headers \
	libuv \
	libuv-dev \
	openssl \
	openssl-dev \
	lua5.1 \
	lua5.1-dev \
	zlib \
	zlib-dev \
	python3-dev \
	libpng-dev \
	libjpeg-turbo-dev \
	tiff-dev \
	libwebp-dev \
	imagemagick \
	exiftool \
	jq

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
