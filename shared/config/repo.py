from subprocess import PIPE, Popen

from ..utilities import stringSlice


name: str = ''

output: bytes = b''.join(Popen(['git', 'config', '--get', 'remote.origin.url'], stdout=PIPE, stderr=PIPE).communicate())
if output and not output.startswith(b'fatal'):
	name = stringSlice(output.decode(), '/', '.git')

else :
	output = b''.join(Popen(['git', 'rev-parse', '--show-toplevel'], stdout=PIPE, stderr=PIPE).communicate())
	if output and not output.startswith(b'fatal'):
		name = stringSlice(output.decode(), '/').strip()


short_hash: str = ''

output = b''.join(Popen(['git', 'rev-parse', '--short', 'HEAD'], stdout=PIPE, stderr=PIPE).communicate())
if output and not output.startswith(b'fatal'):
	short_hash = output.decode().strip()


full_hash: str = ''

output = b''.join(Popen(['git', 'rev-parse', 'HEAD'], stdout=PIPE, stderr=PIPE).communicate())
if output and not output.startswith(b'fatal'):
	full_hash = output.decode().strip()


if not all([name, short_hash, full_hash]) :
	raise ValueError('failed to parse git environment')


del output, stringSlice, PIPE, Popen
