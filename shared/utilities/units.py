from enum import Enum, unique


class Time(Enum) :
	planck        = 5.39e-44
	yoctosecond   = 1e-24
	jiffy         = 3e-24
	zeptosecond   = 1e-21
	attosecond    = 1e-18
	femtosecond   = 1e-15
	svedberg      = 1e-13
	picosecond    = 1e-12
	nanosecond    = 1e-9
	shake         = 1e-8
	microsecond   = 1e-6
	millisecond   = 1e-3
	second        = 1
	decasecond    = 10
	minute        = 60
	moment        = 90
	hectosecond   = 100
	decaminute    = 600
	ke            = 864
	kilosecond    = 1000
	hour          = 3600
	hectominute   = 6000
	kilominute    = 60000
	day           = 86400
	week          = 604800
	megasecond    = 1000000
	fortnight     = 1209600
	month         = 2592000
	quarter       = 7776000
	season        = 7776000
	quadrimester  = 10368000
	semester      = 1555200
	year          = 31536000
	common_year   = 31536000
	tropical_year = 31556925.216
	gregorian     = 31556952
	sidereal_year = 31558149.7635456
	leap_year     = 31622400
	biennium      = 63072000
	triennium     = 94608000
	quadrennium   = 126144000
	olympiad      = 126144000
	lustrum       = 157680000
	decade        = 315360000
	indiction     = 473040000
	gigasecond    = 1000000000
	jubilee       = 1576800000
	century       = 3153600000
	millennium    = 31536000000
	terasecond    = 1000000000000
	megannum      = 31536000000000
	petasecond    = 1000000000000000
	galactic_year = 7253279999999999
	aeon          = 31536000000000000
	exasecond     = 1000000000000000000
	zettasecond   = 1000000000000000000000
	yottasecond   = 1000000000000000000000000

class Byte(Enum) :
	kilobyte   = 1000
	kibibyte   = 1024
	megabyte   = 1000**2
	mebibyte   = 1024**2
	gigabyte   = 1000**3
	gibibyte   = 1024**3
	terabyte   = 1000**4
	tebibyte   = 1024**4
	petabyte   = 1000**5
	pebibyte   = 1024**5
	exabyte    = 1000**6
	exbibyte   = 1024**6
	zettabyte  = 1000**7
	zebibyte   = 1024**7
	yottabyte  = 1000**8
	yobibyte   = 1024**8
	ronnabyte  = 1000**9
	quettabyte = 1000**10