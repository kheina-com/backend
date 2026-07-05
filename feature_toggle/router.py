from fastapi import APIRouter

from shared.models.auth import Scope
from shared.models.server import Request

from .models import FeatureToggle
from .repository import feature_toggles


featureRouter = APIRouter(
	prefix='/feature_toggle',
)


@featureRouter.get('/enable/{feature}', response_model=FeatureToggle)
async def v1Enable(req: Request, feature: str) -> FeatureToggle :
	await req.user.verify_scope(Scope.admin)
	status = await feature_toggles.enable_feat(req.user, feature)
	return FeatureToggle(
		feature = feature,
		status  = status,
	)


@featureRouter.get('/disable/{feature}', response_model=FeatureToggle)
async def v1Disable(req: Request, feature: str) -> FeatureToggle :
	await req.user.verify_scope(Scope.admin)
	status = await feature_toggles.disable_feat(req.user, feature)
	return FeatureToggle(
		feature = feature,
		status  = status,
	)


@featureRouter.get('/{feature}', response_model=FeatureToggle)
async def v1Get(feature: str) -> FeatureToggle :
	status = await feature_toggles.enabled(feature)
	return FeatureToggle(
		feature = feature,
		status  = status,
	)


app = APIRouter(
	prefix='/v1',
	tags=['features'],
)

app.include_router(featureRouter)
