from asyncio import Task, ensure_future

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from shared.auth import Scope
from shared.server import Request

from .configs import Configs
from .models import BannerResponse, ConfigType, CostsStore, FundingResponse, UpdateConfigRequest, UserConfigRequest, UserConfigResponse


app = APIRouter(
	prefix='/v1/config',
	tags=['config'],
)
configs: Configs = Configs()


@app.on_event('startup')
async def startup() :
	await configs.startup()


@app.on_event('shutdown')
async def shutdown() :
	configs.close()


################################################## INTERNAL ##################################################
# @app.get('/i1/user/{user_id}', response_model=UserConfig)
# async def i1UserConfig(req: Request, user_id: int) -> UserConfig :
# 	await req.user.verify_scope(Scope.internal)
# 	return await configs._getUserConfig(user_id)


##################################################  PUBLIC  ##################################################
@app.get('/banner', response_model=BannerResponse)
async def v1Banner() -> BannerResponse :
	return await configs.getConfig(ConfigType.banner, BannerResponse)


@app.get('/funding', response_model=FundingResponse)
async def v1Funding() -> FundingResponse :
	costs: Task[CostsStore] = ensure_future(configs.getConfig(ConfigType.costs, CostsStore))
	return FundingResponse(
		funds=configs.getFunding(),
		costs=(await costs).costs,
	)


@app.patch('/user', status_code=204)
async def v1UpdateUserConfig(req: Request, body: UserConfigRequest) -> None :
	await req.user.authenticated(Scope.user)
	await configs.setUserConfig(
		req.user,
		body,
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


@app.patch('', status_code=204)
async def v1UpdateConfig(req: Request, body: UpdateConfigRequest) -> None :
	await req.user.verify_scope(Scope.mod)
	await configs.updateConfig(
		req.user,
		body.config,
		body.value,
	)
