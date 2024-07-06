from enum import Enum, unique
from os import environ


@unique
class Environment(Enum) :
	local: str = 'local'
	dev: str = 'dev'
	prod: str = 'prod'
	test: str = 'test'

	def is_local(self) :
		return self == Environment.local

	def is_dev(self) :
		return self == Environment.dev

	def is_prod(self) :
		return self == Environment.prod

	def is_test(self) :
		return self == Environment.test


environment: Environment = Environment[environ.get('ENVIRONMENT', 'LOCAL').lower()]

# put other variables/constants here (these will overwrite the env-specific configs above!)
epoch = 1576242000
