from pathlib import Path
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict, TomlConfigSettingsSource


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JACQUES_", env_file=".env")

    db_path: Path = Path("jacques.db")
    output_path: Path = Path("/media/library")
    temp_path: Path = Path("/tmp/jacques")

    makemkvcon_path: str = "makemkvcon"
    handbrake_path: str = "HandBrakeCLI"
    handbrake_quality: int = 18
    handbrake_preset: str = "medium"

    tmdb_api_key: str = ""

    host: str = "0.0.0.0"
    port: int = 8080

    min_title_duration_seconds: int = 1200  # 20 minutes

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        toml_source = TomlConfigSettingsSource(
            settings_cls,
            toml_file=Path.home() / ".config/jacques/config.toml",
        )
        return (init_settings, env_settings, dotenv_settings, file_secret_settings, toml_source)


settings = Settings()
