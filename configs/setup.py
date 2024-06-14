try :
	from re import _pattern_type as Pattern
	from re import compile as re_compile

except ImportError :
	from re import Pattern, compile as re_compile

from os import listdir

from fuzzly_configs import __version__
from setuptools import find_packages, setup


req_regex: Pattern = re_compile(r'^requirements-(\w+).txt$')


setup(
	name='fuzzly-configs',
	version=__version__,
	description='configs library for fuzz.ly',
	long_description=open('readme.md').read(),
	long_description_content_type='text/markdown',
	author='kheina',
	url='https://github.com/kheina-com/configs',
	packages=find_packages(exclude=['tests']),
	install_requires=list(filter(None, map(str.strip, open('requirements.txt').read().split()))),
	python_requires='>=3.9.*',
	license='Mozilla Public License 2.0',
	extras_require=dict(map(lambda x : (x[1], open(x[0]).read().split()), filter(None, map(req_regex.match, listdir())))),
)