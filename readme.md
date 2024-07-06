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
3. setup python venv
	```shell
	% ./venv.sh
		...

	Done. run 'source ./.venv/bin/activate' to enter python virtual environment
	```
4. create credential files for above
	```shell
	% source .venv/bin/activate
	(.venv) % python3 init.py gen-credentials
	```
5. init db and create defaults
	```shell
	% source .venv/bin/activate
	(.venv) % python3 init.py db -u
	[Tue Jul  2 21:10:40 2024] INFO > connected to database.
	==> exec: db/0/00-init.sql
	==> exec: db/0/01-enums.sql
	(.venv) % python3 init.py icon
	(.venv) % python3 init.py admin
	[Tue Jul  2 21:11:17 2024] INFO > connected to database.
	[Tue Jul  2 21:11:17 2024] INFO > connected to database.
	==> account: email='localhost@kheina.com' password='very-secure-password-123'
	```
6. run server within venv
	```shell
	% source .venv/bin/activate
	(.venv) % fastapi dev server.py
	```
7. run `deactivate` to exit venv
