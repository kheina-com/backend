## Development
1. install https://exiftool.org
2. startup docker environment
	```bash
	docker compose up -d db
	docker compose up -d aerospike
	docker compose up -d minio
	```
3. create credential files for above
	- details coming soon
4. setup python venv
	```bash
	$ ./venv.sh
		...

	Done. run 'source ./.venv/bin/activate' to enter python virtual environment
	$ source ./.venv/bin/activate
	```
5. init db and create defaults
	```
	>>> import init
	>>> init.startup()
	>>> init.uploadDefaultIcon()
	>>> init.createAdmin()
	```
6. run server within venv
	```bash
	(.venv) $ python3 server.py
	```
7. run `deactivate` to exit venv
