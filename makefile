.PHONY: venv
venv:
	python3 -m venv ./.venv
	.venv/bin/python3 -m pip install -r requirements.lock --no-deps --ignore-requires-python \
		&& echo && echo "Done. run 'source .venv/bin/activate' to enter python virtual environment"

.PHONY: lock
lock:
	python3 -m venv ./.venv
	.venv/bin/python3 -c 'from subprocess import PIPE, Popen; open("requirements.lock", "w").write("\n".join(sorted(filter(None, set(b"".join(Popen([".venv/bin/python3", "-m", "pip", "freeze", "--local"], stdout=PIPE, stderr=PIPE).communicate()).decode().split("\n")) - set(map(str.strip, open("requirements-build.lock").readlines()))), key=str.casefold)))'

.PHONY: build
build:
	DOCKER_DEFAULT_PLATFORM="linux/amd64" docker build -t us-central1-docker.pkg.dev/kheinacom/fuzzly-repo/fuzzly-backend:$(shell git rev-parse --short HEAD) . --progress=plain

.PHONY: push
push:
	docker push us-central1-docker.pkg.dev/kheinacom/fuzzly-repo/fuzzly-backend:$(shell git rev-parse --short HEAD)

.PHONY: apply
apply:
	kubectl apply -f k8s.yml
