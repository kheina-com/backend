.PHONY: venv
venv:
	python3 -m venv ./.venv
	.venv/bin/python3 -m pip install -r requirements.lock --no-deps \
		&& echo && echo "Done. run 'source .venv/bin/activate' to enter python virtual environment"

.PHONY: lock
lock:
	python3 -m pip freeze > requirements.lock

.PHONY: build
build:
	docker build -t us-central1-docker.pkg.dev/kheinacom/fuzzly-repo/fuzzly-backend:$(shell git rev-parse --short HEAD) . --progress=plain

.PHONY: push-docker
push-docker:
	docker push us-central1-docker.pkg.dev/kheinacom/fuzzly-repo/fuzzly-backend:$(shell git rev-parse --short HEAD)

.PHONY: apply
apply:
	kubectl apply -f k8s.yml
