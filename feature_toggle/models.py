from pydantic import BaseModel


class FeatureToggle(BaseModel) :
	feature: str
	status:  bool
