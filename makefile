.PHONY: venv
venv:
	python3 -m venv ./.venv
	.venv/bin/python3 -m pip install -r requirements.lock --no-deps --ignore-requires-python \
		&& go install github.com/kheina-com/go-thumbhash/cmd/thumbhash@9146e72 \
		&& echo && echo "Done. run 'source .venv/bin/activate' to enter python virtual environment"

.PHONY: lock
lock:
	python3 -m venv ./.venv
	pip-compile --no-annotate --no-header --strip-extras --no-upgrade --output-file=requirements.lock requirements.txt

.PHONY: dev
dev:
	docker compose up -d --wait
	ENVIRONMENT=LOCAL; ./.venv/bin/fastapi dev server.py

.PHONY: build
build:
	DOCKER_DEFAULT_PLATFORM="linux/amd64" docker build -t us-central1-docker.pkg.dev/kheinacom/fuzzly-repo/fuzzly-backend:$(shell git rev-parse --short HEAD) .

.PHONY: push
push:
	docker push us-central1-docker.pkg.dev/kheinacom/fuzzly-repo/fuzzly-backend:$(shell git rev-parse --short HEAD)

.PHONY: apply
apply:
	git add k8s.yml
	git commit -m apply
	kubectl apply -f k8s.yml
