import os
from typing import List

import ujson as json

from .caching import SimpleCache
from .cwd import setCwd
from .exceptions.http_error import NotFound


cwd = setCwd()


@SimpleCache(900)  # 15 minute cache
def secureFolders() -> List[str] :
	try :
		with open('securefolders.json') as folders :
			return json.load(folders)
	except :
		return ['credentials']


def safeJoin(*args) -> str :
	path = os.path.realpath(os.path.join(*args))
	if path.startswith(cwd) and all(folder not in path for folder in secureFolders()) and os.path.exists(path) :
		return path
	raise NotFound('The requested resource is not available.')
