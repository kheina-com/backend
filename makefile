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

.PHONY: upgrade
upgrade:
	python3 -m venv ./.venv
	pip-compile --no-annotate --no-header --upgrade --output-file=/tmp/_fuzzly_requirements.lock requirements.txt
	python3 -m pip install -r /tmp/_fuzzly_requirements.lock
	python3 -c 'upgraded = dict(map(lambda x : x.split("==", 1), filter(None, open("/tmp/_fuzzly_requirements.lock").read().lower().split("\n")))); std = list(map(lambda x : x.split("=")[0], filter(None, open("requirements.txt", "r").read().split("\n")))); open("requirements.txt", "w").write("\n".join([f"{i[:-1]}{i[-1]}={upgraded[i[:-1].lower()]}" for i in std]))' 

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
