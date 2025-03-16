## Development
1. install python3.12+
	- https://www.python.org/downloads
2. install https://exiftool.org
	- Arch
		```
		pacman -S perl-image-exiftool
		```
	- Ubuntu
		```
		apt install libimage-exiftool-perl
		```
	- CentOS/RHEL
		```
		yum install perl-Image-ExifTool
		```
	- Others
		* idk figure it out
3. install ffmpeg
	- https://ffmpeg.org/download.html
4. install docker
5. setup python venv
	```shell
	% make venv
		...

	Done. run 'source ./.venv/bin/activate' to enter python virtual environment
	```
6. create credential files for above
	```shell
	% source .venv/bin/activate
	(.venv) % python3 init.py gen-credentials
	```
7. init db and create defaults
	```shell
	% source .venv/bin/activate
	(.venv) % python3 init.py db -u
	[Tue Jul  2 21:10:40 2024] INFO > connected to database.
	==> exec: db/0/00-init.sql
	==> exec: db/0/01-enums.sql
	...
	(.venv) % python3 init.py icon
	(.venv) % python3 init.py admin
	[Tue Jul  2 21:11:17 2024] INFO > connected to database.
	[Tue Jul  2 21:11:17 2024] INFO > connected to database.
	==> account: email='localhost@kheina.com' password='very-secure-password-123'
	```
8. setup minio server
	1. navigate to http://localhost:9090 and login using [default credentials](./docker-compose.yml#l55-l56)
	2. create a bucket named `kheina-content` with default settings
		- you must add an anonymous access rule to allow read access to bucket contents
	3. create an access key using the [sample credentials](./sample-creds.json#l26-l27)
9. run dev server
	```shell
	% make dev 
	python3 -m venv ./.venv
	docker compose up -d --wait
	[+] Running 3/3
	✔ Container backend-db-1         Healthy          3.8s 
	✔ Container backend-aerospike-1  Healthy          0.8s 
	✔ Container backend-minio-1      Healthy          0.8s 
	ENVIRONMENT=LOCAL; fastapi dev server.py
	INFO     Using path server.py
	INFO     Resolved absolute path /path/to/backend/server.py
	INFO     Searching for package file structure from directories with __init__.py files
	INFO     Importing from /path/to/backend

	╭─ Python module file ─╮
	│                      │
	│  🐍 server.py        │
	│                      │
	╰──────────────────────╯

	INFO     Importing module server
	INFO     Found importable FastAPI app

	╭─ Importable FastAPI app ─╮
	│                          │
	│  from server import app  │
	│                          │
	╰──────────────────────────╯

	INFO     Using import string server:app

	╭────────── FastAPI CLI - Development mode ───────────╮
	│                                                     │
	│  Serving at: http://127.0.0.1:8000                  │
	│                                                     │
	│  API docs: http://127.0.0.1:8000/docs               │
	│                                                     │
	│  Running in development mode, for production use:   │
	│                                                     │
	│  fastapi run                                        │
	│                                                     │
	╰─────────────────────────────────────────────────────╯

	INFO:     Will watch for changes in these directories: ['/path/to/backend']
	INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
	INFO:     Started reloader process [21838] using WatchFiles
	INFO:     Started server process [21955]
	INFO:     Waiting for application startup.
	INFO:     Application startup complete.
	```
10. the frontend must be run via the [frontend repository](https://github.com/kheina-com/frontend)
