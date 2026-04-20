from pydantic import BaseModel, Field


class DeviceRegisterRequest(BaseModel):
    fcm_token: str = Field(min_length=1, max_length=512)
    device_type: str = Field(min_length=1, max_length=32)


class DeviceUnregisterRequest(BaseModel):
    fcm_token: str = Field(min_length=1, max_length=512)


class DeviceResponse(BaseModel):
    fcm_token: str
    device_type: str
