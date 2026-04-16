"""Add profile fields to users table

Revision ID: a3b4c5d6e7f8
Revises: f7a8b9c0d1e2
Create Date: 2026-03-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "preferred_currency",
            sa.String(10),
            nullable=False,
            server_default="USD",
        ),
    )
    op.add_column(
        "users",
        sa.Column("monthly_income_goal", sa.Numeric(15, 2), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("display_title", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "display_title")
    op.drop_column("users", "monthly_income_goal")
    op.drop_column("users", "preferred_currency")
