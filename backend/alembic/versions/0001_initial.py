"""Initial schema.

Tables are created from the SQLAlchemy metadata so the migration stays aligned
with the models. PostgreSQL gains the pgvector extension before create_all.
"""

from alembic import op

from instamagic.models import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
    op.execute("DROP EXTENSION IF EXISTS vector")
