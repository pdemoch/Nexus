"""Remove a tabela legada de estoque consolidado."""

from alembic import op

revision = "8f1d2c3a4b5c"
down_revision = "6adb9c0e79d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("fato_estoque_d0")


def downgrade() -> None:
    raise NotImplementedError("A tabela fato_estoque_d0 foi substituída pelo histórico diário no S3.")
