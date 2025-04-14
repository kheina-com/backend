from asyncio import Task, ensure_future

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from shared.auth import Scope
from shared.models.server import Request

from .configs import Configs
from .models import ConfigsResponse, UpdateConfigRequest, UserConfigRequest, UserConfigResponse


app = APIRouter(
	prefix='/v1/config',
	tags=['config'],
)
configs: Configs = Configs()


@app.on_event('shutdown')
async def shutdown() :
	await configs.close()


@app.get('s', response_model=ConfigsResponse)
async def v1Configs() -> ConfigsResponse :
	return await configs.allConfigs()


@app.patch('/user', status_code=204)
async def v1UpdateUserConfig(req: Request, body: UserConfigRequest) -> None :
	await req.user.verify_scope(Scope.user)
	await configs.setUserConfig(
		req.user,
		**body.values(),
	)


@app.get('/user', response_model=UserConfigResponse)
async def v1UserConfig(req: Request) -> UserConfigResponse :
	await req.user.authenticated()
	return await configs.getUserConfig(req.user)


@app.get('/theme.css', response_model=str)
async def v1UserTheme(req: Request) -> PlainTextResponse :
	await req.user.authenticated()
	return PlainTextResponse(
		content=await configs.getUserTheme(req.user),
		media_type='text/css',
		headers={
			'cache-control': 'no-cache',
		},
	)


# @app.patch('', status_code=204)
# async def v1UpdateConfig(req: Request, body: UpdateConfigRequest) -> None :
# 	await req.user.verify_scope(Scope.mod)
# 	await configs.updateConfig(
# 		req.user,
# 		body.config,
# 		body.value,
# 	)
