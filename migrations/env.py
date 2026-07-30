"""Alembic ortam betiği.

Veritabanı URL'sini alembic.ini'den DEĞİL, uygulamanın kendi config
sisteminden (JETSON_CAMERA_CONFIG ortam değişkeni veya config/config.yaml)
okur. Böylece geliştirme ve üretim için ayrı bir alembic.ini tutmaya gerek
kalmaz - config.yaml neredeyse migration'lar da oraya yazar.
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# backend/ dizinini sys.path'e ekle ki "app.*" import edilebilsin.
_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.config import load_config  # noqa: E402
from app.models.recording import Recording  # noqa: E402,F401 - metadata'ya kaydolması için import edilmesi yeterli
from app.models.user import User  # noqa: E402,F401 - metadata'ya kaydolması için import edilmesi yeterli
from sqlmodel import SQLModel  # noqa: E402

config = context.config

# main.py servis her başladığında migration'ları IN-PROCESS çalıştırır
# (bkz. app/main.py _run_migrations). fileConfig() kök logger'ı sıfırlayıp
# handler'ları değiştiriyor (disable_existing_loggers=True) - bu, uygulamanın
# kendi JSON loglama kurulumunu (app.core.logging.configure_logging) ezer.
# main.py çağrısı alembic_cfg.attributes["skip_logging_config"] = True
# bayrağını set ediyor; `alembic` CLI'den manuel çalıştırıldığında (bu bayrak
# yokken) normal davranış (konsola güzel alembic logları) korunur.
if config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata

app_config = load_config()
config.set_main_option("sqlalchemy.url", f"sqlite:///{app_config.storage.database_path}")


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
