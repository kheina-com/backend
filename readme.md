## Development
1. install https://exiftool.org
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
2. startup docker environment
	```shell
	% docker compose up -d
	```
3. create credential files for above
	- `cp sample-creds.json credentials/`
4. setup python venv
	```shell
	% ./venv.sh
		...

	Done. run 'source ./.venv/bin/activate' to enter python virtual environment
	```
5. init db and create defaults
	```shell
	% source .venv/bin/activate
	(.venv) % python3
	Python 3.12.4 (main, Jun  7 2024, 06:33:07) [GCC 14.1.1 20240522] on linux
	Type "help", "copyright", "credits" or "license" for more information.
	>>> import init
	>>> init.startup()
	[Fri Jun 21 12:10:28 2024] INFO > connected to database.
	==> exec: db/0/00-init.sql
	==> exec: db/0/01-enums.sql
	>>> init.uploadDefaultIcon()
	>>> init.createAdmin()
	[Fri Jun 21 12:10:45 2024] INFO > connected to database.
	LoginRequest(email='localhost@kheina.com', password='very-secure-password-123')
	```
6. run server within venv
	```shell
	% source .venv/bin/activate
	(.venv) % python3 server.py
	```
7. run `deactivate` to exit venv
