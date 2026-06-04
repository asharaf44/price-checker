from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    reports_bucket: str = ""
    anthropic_secret_arn: str = ""


settings = Settings()
