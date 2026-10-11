from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    reports_bucket: str = ""
    anthropic_param_name: str = ""


settings = Settings()
