from asyncio import Task, ensure_future, run, sleep
from importlib import reload

from uvicorn import Config, Server
from watchdog.observers import Observer
from watchdog.events import FileSystemEvent, FileSystemEventHandler

import server
import time


srv: Task


async def app() :
	global srv
	app_config = {
		'host': '0.0.0.0',
		'port': 5000,
	}
	app = Server(Config(app=server.app, **app_config))
	srv = ensure_future(app.serve())
	timeout: float = 5

	class Reloader(FileSystemEventHandler) :
		def dispatch(self, event: FileSystemEvent) -> None:
			print('==> FileSystemEvent:', event)
			global srv
			if not srv.cancel() :
				raise ChildProcessError('shid')

			start = time.time()
			while not srv.done() :
				if time.time() - start > timeout :
					raise ChildProcessError('shid2')

			reload(server)
			app = Server(Config(app=server.app, **app_config))
			srv = ensure_future(app.serve())

	observer = Observer()
	observer.schedule(Reloader(), '.', recursive=True)
	observer.start()

	try:
		while True:
			await sleep(1)
	except KeyboardInterrupt:
		observer.stop()
	observer.join()


if __name__ == '__main__' :
	run(app())
