"""split models

Revision ID: 9b0578c8951f
Revises: e2f1d25825c6
Create Date: 2026-09-26 01:08:08.618107

"""

import json
from json import JSONDecodeError
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "9b0578c8951f"
down_revision: Union[str, Sequence[str], None] = "e2f1d25825c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _decode_enriched_prompt(value: str) -> dict:
    """Преобразует сохранённый текст обогащённого промпта в JSON-объект.

    Args:
        value: Текст обогащённого промпта из старой таблицы.

    Returns:
        JSON-совместимый словарь для колонки generations.enriched_prompt.
    """
    try:
        decoded_value = json.loads(value)
    except JSONDecodeError:
        return {"raw": value}

    if isinstance(decoded_value, dict):
        return decoded_value

    return {"value": decoded_value}


def upgrade() -> None:
    """Переносит данные генераций из generation_feedbacks в generations."""
    op.create_table(
        "generations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column(
            "enriched_prompt",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()),
                "postgresql",
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "success",
                "failed",
                "cancelled",
                name="generation_status",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.tg_id"],
            name=op.f("fk_generations_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generations")),
    )
    op.create_index(
        op.f("ix_generations_user_id"),
        "generations",
        ["user_id"],
        unique=False,
    )

    op.add_column(
        "generation_feedbacks",
        sa.Column("generation_id", sa.Integer(), nullable=True),
    )

    connection = op.get_bind()
    feedbacks = sa.table(
        "generation_feedbacks",
        sa.column("id", sa.Integer()),
        sa.column("user_id", sa.BigInteger()),
        sa.column("initial_prompt", sa.Text()),
        sa.column("enriched_prompt", sa.Text()),
        sa.column("title", sa.Text()),
        sa.column("is_liked", sa.Boolean()),
        sa.column("feedback", sa.Text()),
        sa.column("generation_id", sa.Integer()),
    )
    generations = sa.table(
        "generations",
        sa.column("id", sa.Integer()),
        sa.column("prompt", sa.Text()),
        sa.column("enriched_prompt", sa.JSON()),
        sa.column("title", sa.String()),
        sa.column("status", sa.String()),
        sa.column("user_id", sa.BigInteger()),
    )

    legacy_feedbacks = connection.execute(
        sa.select(
            feedbacks.c.id,
            feedbacks.c.user_id,
            feedbacks.c.initial_prompt,
            feedbacks.c.enriched_prompt,
            feedbacks.c.title,
            feedbacks.c.is_liked,
            feedbacks.c.feedback,
        )
    ).mappings()

    for legacy_feedback in legacy_feedbacks:
        enriched_prompt = _decode_enriched_prompt(legacy_feedback["enriched_prompt"])
        has_completed_generation = (
            legacy_feedback["title"] is not None
            or legacy_feedback["is_liked"] is not None
            or legacy_feedback["feedback"] is not None
        )
        status = "success" if has_completed_generation else "pending"

        generation_id = connection.execute(
            sa.insert(generations)
            .values(
                prompt=legacy_feedback["initial_prompt"],
                enriched_prompt=enriched_prompt,
                title=legacy_feedback["title"],
                status=status,
                user_id=legacy_feedback["user_id"],
            )
            .returning(generations.c.id)
        ).scalar_one()

        connection.execute(
            sa.update(feedbacks).where(feedbacks.c.id == legacy_feedback["id"]).values(generation_id=generation_id)
        )

    op.alter_column(
        "generation_feedbacks",
        "generation_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.drop_index(
        op.f("ix_generation_feedbacks_user_id"),
        table_name="generation_feedbacks",
    )
    op.create_index(
        op.f("ix_generation_feedbacks_generation_id"),
        "generation_feedbacks",
        ["generation_id"],
        unique=False,
    )
    op.drop_constraint(
        op.f("generation_feedbacks_user_id_fkey"),
        "generation_feedbacks",
        type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk_generation_feedbacks_generation_id_generations"),
        "generation_feedbacks",
        "generations",
        ["generation_id"],
        ["id"],
    )
    op.drop_column("generation_feedbacks", "initial_prompt")
    op.drop_column("generation_feedbacks", "enriched_prompt")
    op.drop_column("generation_feedbacks", "user_id")
    op.drop_column("generation_feedbacks", "title")


def downgrade() -> None:
    """Возвращает данные генераций в generation_feedbacks перед удалением таблицы."""
    op.add_column(
        "generation_feedbacks",
        sa.Column("title", sa.Text(), nullable=True),
    )
    op.add_column(
        "generation_feedbacks",
        sa.Column("user_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "generation_feedbacks",
        sa.Column("enriched_prompt", sa.Text(), nullable=True),
    )
    op.add_column(
        "generation_feedbacks",
        sa.Column("initial_prompt", sa.Text(), nullable=True),
    )

    connection = op.get_bind()
    feedbacks = sa.table(
        "generation_feedbacks",
        sa.column("id", sa.Integer()),
        sa.column("generation_id", sa.Integer()),
        sa.column("title", sa.Text()),
        sa.column("user_id", sa.BigInteger()),
        sa.column("enriched_prompt", sa.Text()),
        sa.column("initial_prompt", sa.Text()),
    )
    generations = sa.table(
        "generations",
        sa.column("id", sa.Integer()),
        sa.column("prompt", sa.Text()),
        sa.column("enriched_prompt", postgresql.JSONB()),
        sa.column("title", sa.String()),
        sa.column("user_id", sa.BigInteger()),
    )

    generation_rows = connection.execute(
        sa.select(
            feedbacks.c.id.label("feedback_id"),
            generations.c.prompt,
            generations.c.enriched_prompt,
            generations.c.title,
            generations.c.user_id,
        ).select_from(
            feedbacks.join(
                generations,
                feedbacks.c.generation_id == generations.c.id,
            )
        )
    ).mappings()

    for generation_row in generation_rows:
        connection.execute(
            sa.update(feedbacks)
            .where(feedbacks.c.id == generation_row["feedback_id"])
            .values(
                initial_prompt=generation_row["prompt"],
                enriched_prompt=json.dumps(
                    generation_row["enriched_prompt"],
                    ensure_ascii=False,
                ),
                title=generation_row["title"],
                user_id=generation_row["user_id"],
            )
        )

    op.alter_column(
        "generation_feedbacks",
        "initial_prompt",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.alter_column(
        "generation_feedbacks",
        "enriched_prompt",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.alter_column(
        "generation_feedbacks",
        "user_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )

    op.drop_constraint(
        op.f("fk_generation_feedbacks_generation_id_generations"),
        "generation_feedbacks",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "generation_feedbacks_user_id_fkey",
        "generation_feedbacks",
        "users",
        ["user_id"],
        ["tg_id"],
    )
    op.drop_index(
        op.f("ix_generation_feedbacks_generation_id"),
        table_name="generation_feedbacks",
    )
    op.create_index(
        op.f("ix_generation_feedbacks_user_id"),
        "generation_feedbacks",
        ["user_id"],
        unique=False,
    )
    op.drop_column("generation_feedbacks", "generation_id")
    op.drop_index(
        op.f("ix_generations_user_id"),
        table_name="generations",
    )
    op.drop_table("generations")
