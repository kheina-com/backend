from fastapi import APIRouter

from shared.timing import timed

from .repository import Probe


probes = APIRouter(
	prefix='/health',
	tags=['health'],
	include_in_schema=False,
)
probe = Probe()


@probes.get('/liveness', status_code=204)
@timed.root
async def healthz() -> None :
	return


@probes.get('/readiness', status_code=204)
@timed.root
async def readyz() -> None :
	return await probe.readyz()
