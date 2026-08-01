"""Add users, authenticated sessions and S3-backed upload metadata."""

from collections.abc import Sequence
from uuid import UUID as PythonUUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql.elements import BindParameter

revision: str = "20260725_0002"
down_revision: str | None = "20260722_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = sa.Uuid()
LEGACY_OWNER_ID = PythonUUID("00000000-0000-0000-0000-000000000001")


def legacy_owner_id_parameter() -> BindParameter[PythonUUID]:
    return sa.bindparam("id", value=LEGACY_OWNER_ID, type_=UUID)


def timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID, nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("role", sa.String(16), server_default="user", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_username", "users", ["username"])
    op.create_index("ix_users_email", "users", ["email"])
    op.execute(
        sa.text(
            """
            INSERT INTO users (
                id, username, email, password_hash, role, is_active,
                must_change_password, created_at, updated_at
            )
            VALUES (
                :id, 'legacy-owner', 'legacy-owner@invalid.local', 'disabled',
                'admin', false, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """
        ).bindparams(legacy_owner_id_parameter())
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("user_agent", sa.String(512)),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_auth_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_token_hash", "auth_sessions", ["token_hash"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])

    op.create_table(
        "uploaded_files",
        sa.Column("id", UUID, nullable=False),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("original_file_name", sa.String(512), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("file_format", sa.String(16), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), server_default="uploaded", nullable=False),
        sa.Column("parse_error", sa.Text()),
        *timestamps(),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_uploaded_files"),
        sa.UniqueConstraint("object_key", name="uq_uploaded_files_object_key"),
    )
    op.create_index("ix_uploaded_files_owner_id", "uploaded_files", ["owner_id"])
    op.create_index("ix_uploaded_files_checksum", "uploaded_files", ["checksum"])
    op.create_index("ix_uploaded_files_status", "uploaded_files", ["status"])

    op.add_column("import_batches", sa.Column("owner_id", UUID))
    op.add_column("import_batches", sa.Column("uploaded_file_id", UUID))
    op.create_foreign_key(
        "fk_import_batches_owner_id_users",
        "import_batches",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_import_batches_uploaded_file_id_uploaded_files",
        "import_batches",
        "uploaded_files",
        ["uploaded_file_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_import_batches_owner_id", "import_batches", ["owner_id"])
    op.create_unique_constraint(
        "uq_import_batches_uploaded_file_id", "import_batches", ["uploaded_file_id"]
    )
    op.execute(
        sa.text("UPDATE import_batches SET owner_id = :id WHERE owner_id IS NULL").bindparams(
            legacy_owner_id_parameter()
        )
    )
    op.alter_column("import_batches", "owner_id", existing_type=UUID, nullable=False)
    op.drop_constraint("uq_import_batches_checksum", "import_batches", type_="unique")
    op.create_unique_constraint(
        "uq_import_batches_owner_checksum_sheet",
        "import_batches",
        ["owner_id", "checksum", "sheet_name"],
    )

    op.add_column("bond_lots", sa.Column("owner_id", UUID))
    op.create_foreign_key(
        "fk_bond_lots_owner_id_users",
        "bond_lots",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_bond_lots_owner_id", "bond_lots", ["owner_id"])
    op.execute(
        sa.text("UPDATE bond_lots SET owner_id = :id WHERE owner_id IS NULL").bindparams(
            legacy_owner_id_parameter()
        )
    )
    op.alter_column("bond_lots", "owner_id", existing_type=UUID, nullable=False)

    op.add_column("app_settings", sa.Column("owner_id", UUID))
    op.create_foreign_key(
        "fk_app_settings_owner_id_users",
        "app_settings",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_app_settings_owner_id", "app_settings", ["owner_id"])
    op.execute(
        sa.text("UPDATE app_settings SET owner_id = :id WHERE owner_id IS NULL").bindparams(
            legacy_owner_id_parameter()
        )
    )
    op.alter_column("app_settings", "owner_id", existing_type=UUID, nullable=False)
    op.drop_constraint("uq_app_settings_singleton_key", "app_settings", type_="unique")
    op.create_unique_constraint(
        "uq_app_settings_owner_singleton", "app_settings", ["owner_id", "singleton_key"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_app_settings_owner_singleton", "app_settings", type_="unique")
    op.create_unique_constraint("uq_app_settings_singleton_key", "app_settings", ["singleton_key"])
    op.drop_index("ix_app_settings_owner_id", table_name="app_settings")
    op.drop_constraint("fk_app_settings_owner_id_users", "app_settings", type_="foreignkey")
    op.drop_column("app_settings", "owner_id")

    op.drop_index("ix_bond_lots_owner_id", table_name="bond_lots")
    op.drop_constraint("fk_bond_lots_owner_id_users", "bond_lots", type_="foreignkey")
    op.drop_column("bond_lots", "owner_id")

    op.drop_constraint("uq_import_batches_owner_checksum_sheet", "import_batches", type_="unique")
    op.create_unique_constraint(
        "uq_import_batches_checksum", "import_batches", ["checksum", "sheet_name"]
    )
    op.drop_constraint("uq_import_batches_uploaded_file_id", "import_batches", type_="unique")
    op.drop_index("ix_import_batches_owner_id", table_name="import_batches")
    op.drop_constraint(
        "fk_import_batches_uploaded_file_id_uploaded_files",
        "import_batches",
        type_="foreignkey",
    )
    op.drop_constraint("fk_import_batches_owner_id_users", "import_batches", type_="foreignkey")
    op.drop_column("import_batches", "uploaded_file_id")
    op.drop_column("import_batches", "owner_id")

    op.drop_index("ix_uploaded_files_status", table_name="uploaded_files")
    op.drop_index("ix_uploaded_files_checksum", table_name="uploaded_files")
    op.drop_index("ix_uploaded_files_owner_id", table_name="uploaded_files")
    op.drop_table("uploaded_files")
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_token_hash", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
