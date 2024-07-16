from fastapi import APIRouter

from shared.timing import timed

from .repository import Probe


probes = APIRouter()
probe = Probe()


@probes.get('/healthz', status_code=204)
@timed.root
async def healthz() -> None :
	return


@probes.get('/readyz', status_code=204)
@timed.root
async def readyz() -> None :
	return await probe.readyz()
