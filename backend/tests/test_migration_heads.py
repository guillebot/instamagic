from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_single_alembic_head() -> None:
    """Concurrent PRs must chain migrations; two heads break `alembic upgrade head` on deploy."""
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1, heads
