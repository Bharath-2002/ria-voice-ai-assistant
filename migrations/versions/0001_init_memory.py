"""init memory: customers + conversations

Revision ID: 0001_init_memory
Revises:
Create Date: 2026-05-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0001_init_memory"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("phone", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("phone", name="uq_customers_phone"),
    )
    op.create_index("ix_customers_phone", "customers", ["phone"], unique=True)

    op.create_table(
        "conversations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=True),
        sa.Column("agent_id", sa.String(), nullable=True),
        sa.Column("direction", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("duration_secs", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column("follow_up", sa.Text(), nullable=True),
        sa.Column("captured_preferences", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("recommended_products", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("cards_sent", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("raw_summary_elevenlabs", sa.Text(), nullable=True),
        sa.Column("raw_transcript_turns", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("conversation_id", name="uq_conversations_conversation_id"),
    )
    op.create_index("ix_conversations_customer_id", "conversations", ["customer_id"])
    op.create_index(
        "idx_conversations_customer_recent",
        "conversations",
        ["customer_id", sa.text("ended_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_conversations_customer_recent", table_name="conversations")
    op.drop_index("ix_conversations_customer_id", table_name="conversations")
    op.drop_table("conversations")
    op.drop_index("ix_customers_phone", table_name="customers")
    op.drop_table("customers")
