"""Add prediction subrounds and matches.subround_id.

Revision ID: 20261106_0001
Revises:
Create Date: 2026-11-06

Creates ``subrounds`` (per parent pot/round slate) and nullable
``matches.subround_id``. Backfill: one open subround for the current
(or latest) round, attaching all currently selected fixtures.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261106_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subrounds",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("round_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["round_id"], ["rounds.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("round_id", "sequence", name="uq_subrounds_round_sequence"),
    )
    op.create_index("ix_subrounds_round_id", "subrounds", ["round_id"])

    with op.batch_alter_table("matches") as batch_op:
        batch_op.add_column(sa.Column("subround_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_matches_subround_id", ["subround_id"])
        batch_op.create_foreign_key(
            "fk_matches_subround_id_subrounds",
            "subrounds",
            ["subround_id"],
            ["id"],
        )

    conn = op.get_bind()
    rounds = conn.execute(
        sa.text(
            "SELECT id FROM rounds WHERE end_date IS NULL "
            "ORDER BY start_date DESC LIMIT 1"
        )
    ).fetchone()
    if rounds is None:
        rounds = conn.execute(
            sa.text("SELECT id FROM rounds ORDER BY start_date DESC LIMIT 1")
        ).fetchone()
    if rounds is None:
        return

    round_id = rounds[0]
    selected = conn.execute(
        sa.text("SELECT COUNT(*) FROM matches WHERE use_match = 1")
    ).scalar()
    if not selected:
        return

    conn.execute(
        sa.text(
            "INSERT INTO subrounds (round_id, sequence, created_at, closed_at) "
            "VALUES (:round_id, 1, CURRENT_TIMESTAMP, NULL)"
        ),
        {"round_id": round_id},
    )
    subround = conn.execute(
        sa.text(
            "SELECT id FROM subrounds WHERE round_id = :round_id AND sequence = 1"
        ),
        {"round_id": round_id},
    ).fetchone()
    if subround is None:
        return
    conn.execute(
        sa.text(
            "UPDATE matches SET subround_id = :subround_id "
            "WHERE use_match = 1"
        ),
        {"subround_id": subround[0]},
    )


def downgrade() -> None:
    with op.batch_alter_table("matches") as batch_op:
        batch_op.drop_constraint("fk_matches_subround_id_subrounds", type_="foreignkey")
        batch_op.drop_index("ix_matches_subround_id")
        batch_op.drop_column("subround_id")
    op.drop_index("ix_subrounds_round_id", table_name="subrounds")
    op.drop_table("subrounds")
