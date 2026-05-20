"""
环境配置 — 改编自旧项目的 config.py。

支持 LEA_ENV=test/production 环境切换。
"""

from __future__ import annotations

import os
import urllib.parse
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings


class Environment(str, Enum):
    TEST = "test"
    PRODUCTION = "production"


def _load_env_files() -> Environment:
    """按优先级加载环境配置。"""
    backend_dir = Path(__file__).parent.parent.parent.parent.parent  # lea-platform/

    base_env = backend_dir / ".env"
    if base_env.exists():
        load_dotenv(base_env)

    env_name = os.getenv("LEA_ENV", "test").lower()
    try:
        current_env = Environment(env_name)
    except ValueError:
        current_env = Environment.TEST

    env_file = backend_dir / f".env.{current_env.value}"
    if env_file.exists():
        load_dotenv(env_file, override=True)

    return current_env


LEA_ENV = _load_env_files()


class Settings(BaseSettings):
    """应用配置。"""

    db_host: str = Field(default="localhost", alias="DB_HOST")
    db_port: int = Field(default=3306, alias="DB_PORT")
    db_user: str = Field(default="root", alias="DB_USER")
    db_password: str = Field(default="", alias="DB_PASSWORD")
    db_name: str = Field(default="lea-platform-test", alias="DB_NAME")

    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    secret_key: str = Field(default="dev-secret-key-change-me", alias="SECRET_KEY")
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440

    upload_dir: str = Field(default="./data/uploads", alias="UPLOAD_DIR")
    generated_files_dir: str = Field(default="./data/generated_files", alias="GENERATED_FILES_DIR")

    @property
    def database_url(self) -> str:
        if self.db_host in ("sqlite", ""):
            db_path = Path(self.db_name).with_suffix(".db")
            return f"sqlite:///{db_path}"
        password = urllib.parse.quote_plus(self.db_password)
        return f"mysql+pymysql://{self.db_user}:{password}@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
