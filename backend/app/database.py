# =============================================
# OfferU - 閺佺増宓佹惔鎾崇穿閹?
# =============================================
# 瀵倹? SQLAlchemy 瀵洘鎼搁柊宥囩枂
# 閺€?瀵?SQLite閿涘牆绱戦崣鎴礆閸?PostgreSQL閿涘牏鏁撴禍褝绱?
# =============================================

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

# 閸掓稑缂撳鍌?閺佺増宓佹惔鎾崇穿閹?
# echo=False 閸忔娊妫?SQL 閺冦儱绻旈敍娑氭晸娴溠呭箚婢?database_url 鎼存柧璐?postgresql+asyncpg://...
# 瀵偓閸欐垹骞嗘晶鍐帛鐠併倓濞囬悽?sqlite+aiosqlite:///./djm.db
engine = create_async_engine(settings.database_url, echo=False)


@event.listens_for(engine.sync_engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    """Enable SQLite FK enforcement for every production connection."""
    if str(getattr(engine.dialect, "name", "")) != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()

# expire_on_commit=False閿涙瓭ommit 閸?ORM 鐎电钖勭仦鐐粹偓褌绗夋径杈ㄦ櫏閿?
# 闁灝鍘ゅ鍌?娑撳﹣绗呴弬鍥﹁厬閹板繐?鐟欙箑褰傚鎯扮箿閸旂姾娴囬敍鍧卻ync session 娑撳秴鍘戠拋鎼佹瀵?IO閿?
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


class RequiredIndexMigrationError(RuntimeError):
    """Fail-closed error for required-index states needing manual review."""


class RequiredForeignKeyMigrationError(RuntimeError):
    """Fail-closed error for required-FK states needing manual review."""


class RequiredColumnMigrationError(RuntimeError):
    """Fail-closed error for missing-column states without a safe deterministic default."""


def _quote_migration_string(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _migration_column_default_clause(column, *, col_type_upper: str, dialect_name: str = "sqlite") -> str:
    """Build a portable DEFAULT clause for ``ALTER TABLE ... ADD COLUMN``.

    Audit B P1-3: PostgreSQL rejects literal ``DEFAULT 0`` for BOOLEAN and
    DATETIME/TIMESTAMP columns, which SQLite silently accepts.  Boolean client
    defaults therefore render as TRUE/FALSE on PostgreSQL, and non-null
    DateTime columns with no deterministic client default render as
    ``DEFAULT CURRENT_TIMESTAMP`` on PostgreSQL while SQLite keeps its legacy
    (SQLite-legal) ``DEFAULT 0`` behavior — SQLite forbids non-constant
    defaults in ADD COLUMN.  Numeric columns keep ``DEFAULT 0`` on both.
    """
    import json

    default = column.default
    if default is not None:
        value = default.arg
        if callable(value):
            try:
                value = value()
            except TypeError:
                try:
                    value = value(None)
                except Exception as exc:
                    raise RequiredColumnMigrationError(
                        f"Required column migration needs manual review: column={column.table.name}.{column.name}, "
                        "callable default could not be evaluated deterministically."
                    ) from exc
            except Exception as exc:
                raise RequiredColumnMigrationError(
                    f"Required column migration needs manual review: column={column.table.name}.{column.name}, "
                    "callable default could not be evaluated deterministically."
                ) from exc
        if isinstance(value, str):
            return f" DEFAULT {_quote_migration_string(value)}"
        if isinstance(value, bool):
            if dialect_name == "postgresql":
                return " DEFAULT TRUE" if value else " DEFAULT FALSE"
            return f" DEFAULT {1 if value else 0}"
        if isinstance(value, (int, float)):
            return f" DEFAULT {value}"
        if isinstance(value, (list, dict)):
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            return f" DEFAULT {_quote_migration_string(encoded)}"
        if value is not None:
            raise RequiredColumnMigrationError(
                f"Required column migration needs manual review: column={column.table.name}.{column.name}, "
                f"unsupported Python default type={type(value).__name__}."
            )
    if column.nullable:
        return ""
    if "JSON" in col_type_upper:
        raise RequiredColumnMigrationError(
            f"Required column migration needs manual review: NOT NULL JSON column={column.table.name}.{column.name} "
            "has no declared object/array shape default."
        )
    if "CHAR" in col_type_upper or "TEXT" in col_type_upper:
        return " DEFAULT ''"
    if "BOOL" in col_type_upper:
        return " DEFAULT FALSE" if dialect_name == "postgresql" else " DEFAULT 0"
    if "DATETIME" in col_type_upper or "TIMESTAMP" in col_type_upper:
        if dialect_name == "postgresql":
            return " DEFAULT CURRENT_TIMESTAMP"
        # SQLite rejects non-constant defaults in ADD COLUMN; keep the legacy
        # SQLite-legal literal so upgraded SQLite databases still migrate.
        return " DEFAULT 0"
    return " DEFAULT 0"


_REQUIRED_UNIQUE_INDEXES = (
    ("agent_tree_entries", "uq_agent_tree_entries_invocation_sequence", ("invocation_key", "invocation_sequence")),
    ("agent_tool_invocation_receipts", "uq_agent_tool_invocation_receipt_key", ("invocation_key", "tool_call_id")),
    ("proposal_continuations", "uq_proposal_continuations_confirmed_event_id", ("confirmed_event_id",)),
    ("proposal_continuations", "uq_proposal_continuations_invocation_key", ("invocation_key",)),
    ("agent_plan_drafts", "uq_agent_plan_draft_turn", ("actor_id", "session_id", "turn_key")),
    ("agent_plan_intents", "uq_agent_plan_intent_effect", ("draft_id", "canonical_effect_key")),
    ("agent_plan_intents", "uq_agent_plan_intent_sequence", ("draft_id", "sequence")),
    ("confirmation_groups", "uq_confirmation_group_sequence", ("plan_id", "sequence")),
    ("operation_nodes", "uq_operation_node_sequence", ("plan_id", "sequence")),
    ("confirmation_decisions", "uq_confirmation_decision_sequence", ("group_id", "sequence")),
    ("plan_rebase_receipts", "uq_plan_rebase_event", ("node_id", "event_key")),
    ("confirmation_groups", "uq_confirmation_group_plan_identity", ("plan_id", "group_id")),
    ("operation_nodes", "uq_operation_node_plan_identity", ("plan_id", "node_id")),
    ("proposal_plans", "uq_proposal_plan_lineage_revision", ("lineage_id", "revision")),
    ("proposal_plans", "uq_proposal_plan_current_lineage", ("current_lineage_key",)),
    ("node_execution_receipts", "uq_node_execution_idempotency_key", ("idempotency_key",)),
    ("atomic_group_execution_claims", "uq_atomic_group_execution_identity", ("plan_id", "atomic_group_id")),
    ("atomic_group_execution_claims", "uq_atomic_group_execution_idempotency_key", ("idempotency_key",)),
    ("saga_compensation_receipts", "uq_saga_compensation_idempotency_key", ("idempotency_key",)),
    ("plan_group_execution_jobs", "uq_plan_group_execution_idempotency_key", ("idempotency_key",)),
    ("node_execution_outcomes", "uq_node_execution_outcome_node", ("node_id",)),
    ("plan_group_result_receipts", "uq_plan_group_result_identity", ("plan_id", "group_id")),
    ("manual_review_cases", "uq_manual_review_case_dedupe", ("dedupe_key",)),
    ("manual_review_resolutions", "uq_manual_review_resolution_sequence", ("case_id", "sequence")),
    ("manual_review_resolutions", "uq_manual_review_resolution_idempotency", ("case_id", "idempotency_key")),
    ("applications", "uq_applications_owner_job", ("owner_actor_id", "job_id")),
)


_PART6_POSTGRES_FOREIGN_KEYS = (
    ("agent_plan_intents", "fk_plan_intent_draft", ("draft_id",), "agent_plan_drafts", ("draft_id",)),
    ("proposal_plans", "fk_proposal_plan_draft", ("draft_id",), "agent_plan_drafts", ("draft_id",)),
    ("proposal_plans", "fk_proposal_plan_parent", ("parent_plan_id",), "proposal_plans", ("plan_id",)),
    ("proposal_plans", "fk_proposal_plan_replaced_by", ("replaced_by_plan_id",), "proposal_plans", ("plan_id",)),
    ("confirmation_groups", "fk_confirmation_group_plan", ("plan_id",), "proposal_plans", ("plan_id",)),
    ("confirmation_decisions", "fk_confirmation_decision_group", ("plan_id", "group_id"), "confirmation_groups", ("plan_id", "group_id")),
    ("operation_nodes", "fk_operation_node_group", ("plan_id", "confirmation_group_id"), "confirmation_groups", ("plan_id", "group_id")),
    ("node_dependencies", "fk_node_dependency_child", ("plan_id", "node_id"), "operation_nodes", ("plan_id", "node_id")),
    ("node_dependencies", "fk_node_dependency_parent", ("plan_id", "depends_on_node_id"), "operation_nodes", ("plan_id", "node_id")),
    ("node_execution_receipts", "fk_node_execution_receipt_node", ("plan_id", "node_id"), "operation_nodes", ("plan_id", "node_id")),
    ("plan_node_execution_snapshots", "fk_plan_node_snapshot_node", ("plan_id", "node_id"), "operation_nodes", ("plan_id", "node_id")),
    ("plan_node_execution_snapshots", "fk_plan_node_snapshot_group", ("plan_id", "confirmation_group_id"), "confirmation_groups", ("plan_id", "group_id")),
    ("plan_rebase_receipts", "fk_plan_rebase_receipt_node", ("plan_id", "node_id"), "operation_nodes", ("plan_id", "node_id")),
    ("node_execution_revisions", "fk_node_execution_revision_node", ("plan_id", "node_id"), "operation_nodes", ("plan_id", "node_id")),
    ("saga_groups", "fk_saga_group_plan", ("plan_id",), "proposal_plans", ("plan_id",)),
    ("saga_compensation_receipts", "fk_saga_compensation_receipt_node", ("plan_id", "node_id"), "operation_nodes", ("plan_id", "node_id")),
    ("saga_compensation_receipts", "fk_saga_compensation_receipt_group", ("plan_id",), "saga_groups", ("plan_id",)),
    ("atomic_group_execution_claims", "fk_atomic_group_execution_group", ("plan_id", "confirmation_group_id"), "confirmation_groups", ("plan_id", "group_id")),
    ("plan_group_execution_jobs", "fk_plan_group_execution_proposal", ("proposal_id",), "proposal_cache", ("proposal_id",)),
    ("plan_group_execution_jobs", "fk_plan_group_execution_group", ("plan_id", "group_id"), "confirmation_groups", ("plan_id", "group_id")),
    ("node_execution_outcomes", "fk_node_execution_outcome_node", ("plan_id", "node_id"), "operation_nodes", ("plan_id", "node_id")),
    ("node_execution_outcomes", "fk_node_execution_outcome_group", ("plan_id", "group_id"), "confirmation_groups", ("plan_id", "group_id")),
    ("plan_group_result_receipts", "fk_plan_group_result_group", ("plan_id", "group_id"), "confirmation_groups", ("plan_id", "group_id")),
    ("manual_review_resolutions", "fk_manual_review_resolution_case", ("case_id",), "manual_review_cases", ("case_id",)),
    ("plan_group_execution_jobs", "fk_plan_group_execution_result_receipt", ("result_receipt_id",), "plan_group_result_receipts", ("result_receipt_id",)),
    ("proposal_continuations", "fk_proposal_continuation_result_receipt", ("result_receipt_id",), "plan_group_result_receipts", ("result_receipt_id",)),
    ("agent_audit_logs", "fk_agent_audit_result_receipt", ("result_receipt_id",), "plan_group_result_receipts", ("result_receipt_id",)),
    ("application_records", "fk_application_record_application", ("application_id",), "applications", ("id",)),
)


def _index_definition(index: dict) -> dict:
    dialect_options = index.get("dialect_options") or {}
    predicates = {key: str(value) for key, value in dialect_options.items() if key.endswith("_where") and value is not None}
    expressions = index.get("expressions")
    return {
        "name": index.get("name"),
        "unique": bool(index.get("unique")),
        "columns": tuple(index.get("column_names") or ()),
        "predicates": predicates,
        "expressions": tuple(expressions or ()),
    }


def _verify_required_index(connection, table_name: str, index_name: str, columns: tuple[str, ...]) -> bool:
    from sqlalchemy import inspect as sa_inspect
    matches = [item for item in sa_inspect(connection).get_indexes(table_name) if item.get("name") == index_name]
    if not matches:
        return False
    observed = _index_definition(matches[0])
    expected = {"name": index_name, "unique": True, "columns": columns, "predicates": {}, "expressions": ()}
    if observed != expected:
        raise RequiredIndexMigrationError(
            f"Required index migration needs manual review: table={table_name!r}, index={index_name!r}, "
            f"expected={expected!r}, observed={observed!r}. Do not drop or rewrite data automatically."
        )
    return True


def _duplicate_samples(connection, table_name: str, columns: tuple[str, ...], limit: int = 5) -> list[dict]:
    from sqlalchemy import text
    quote = connection.dialect.identifier_preparer.quote_identifier
    selected = ", ".join(quote(column) for column in columns)
    non_null = " AND ".join(f"{quote(column)} IS NOT NULL" for column in columns)
    sql = (
        f"SELECT {selected}, COUNT(*) AS duplicate_count FROM {quote(table_name)} "
        f"WHERE {non_null} GROUP BY {selected} HAVING COUNT(*) > 1 "
        f"ORDER BY COUNT(*) DESC LIMIT {int(limit)}"
    )
    return [dict(row._mapping) for row in connection.execute(text(sql))]

def _unique_index_ddl(dialect, *, table_name: str, index_name: str, columns: tuple[str, ...]) -> str:
    """Build portable SQLite/PostgreSQL DDL for a fixed, required unique index."""

    quote = dialect.identifier_preparer.quote_identifier
    column_sql = ", ".join(quote(column) for column in columns)
    return (
        f"CREATE UNIQUE INDEX IF NOT EXISTS {quote(index_name)} "
        f"ON {quote(table_name)} ({column_sql})"
    )


def _foreign_key_definition(item: dict) -> dict:
    options = item.get("options") or {}
    ondelete = str(options.get("ondelete") or item.get("ondelete") or "NO ACTION").upper().replace("_", " ")
    return {
        "name": str(item.get("name") or ""),
        "local_columns": tuple(item.get("constrained_columns") or ()),
        "remote_table": str(item.get("referred_table") or ""),
        "remote_columns": tuple(item.get("referred_columns") or ()),
        "ondelete": ondelete,
    }


def _verify_required_foreign_key(
    inspector, table_name: str, constraint_name: str, local_columns: tuple[str, ...],
    remote_table: str, remote_columns: tuple[str, ...], *, ondelete: str = "RESTRICT",
) -> bool:
    matches = [item for item in inspector.get_foreign_keys(table_name) if str(item.get("name") or "") == constraint_name]
    if not matches:
        return False
    observed = _foreign_key_definition(matches[0])
    expected = {
        "name": constraint_name, "local_columns": tuple(local_columns), "remote_table": remote_table,
        "remote_columns": tuple(remote_columns), "ondelete": str(ondelete).upper().replace("_", " "),
    }
    if observed != expected:
        raise RequiredForeignKeyMigrationError(
            f"Required foreign key migration needs manual review: table={table_name!r}, constraint={constraint_name!r}, "
            f"expected={expected!r}, observed={observed!r}. Do not drop or rewrite constraints automatically."
        )
    return True


def _foreign_key_orphan_samples(
    connection, table_name: str, local_columns: tuple[str, ...], remote_table: str,
    remote_columns: tuple[str, ...], limit: int = 5,
) -> list[dict]:
    from sqlalchemy import text
    quote = connection.dialect.identifier_preparer.quote_identifier
    local_alias, remote_alias = "child", "parent"
    join = " AND ".join(
        f"{local_alias}.{quote(local)} = {remote_alias}.{quote(remote)}"
        for local, remote in zip(local_columns, remote_columns, strict=True)
    )
    present = " AND ".join(f"{local_alias}.{quote(column)} IS NOT NULL" for column in local_columns)
    missing = f"{remote_alias}.{quote(remote_columns[0])} IS NULL"
    selected = ", ".join(f"{local_alias}.{quote(column)}" for column in local_columns)
    sql = (
        f"SELECT {selected} FROM {quote(table_name)} AS {local_alias} "
        f"LEFT JOIN {quote(remote_table)} AS {remote_alias} ON {join} "
        f"WHERE {present} AND {missing} LIMIT {int(limit)}"
    )
    return [dict(row._mapping) for row in connection.execute(text(sql))]


_PART6_RESULT_RECEIPT_REFERENCES = (
    ("plan_group_execution_jobs", "result_receipt_id"),
    ("proposal_continuations", "result_receipt_id"),
    ("agent_audit_logs", "result_receipt_id"),
)


def _sqlite_make_column_nullable(connection, table_name: str, column_name: str) -> None:
    """Rebuild one SQLite table while preserving data and explicit schema objects."""
    import re
    from sqlalchemy import text

    row = connection.execute(
        text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"),
        {"name": table_name},
    ).scalar_one_or_none()
    if not row:
        return
    pattern = rf'((?:"{re.escape(column_name)}"|{re.escape(column_name)})\s+[^,\n]*?)\s+NOT\s+NULL'
    rebuilt_sql, count = re.subn(pattern, r'\1', str(row), count=1, flags=re.IGNORECASE)
    if count != 1:
        raise RequiredForeignKeyMigrationError(
            f"SQLite nullable migration needs manual review: table={table_name!r}, column={column_name!r}"
        )
    schema_objects = list(connection.execute(
        text(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE tbl_name=:name AND type IN ('index','trigger') AND sql IS NOT NULL"
        ),
        {"name": table_name},
    ).mappings())
    columns = [str(item[1]) for item in connection.exec_driver_sql(f'PRAGMA table_info("{table_name}")')]
    quoted_columns = ", ".join(f'"{column}"' for column in columns)
    temporary_name = f"__part6_nullable_{table_name}"
    connection.execute(text(f'ALTER TABLE "{table_name}" RENAME TO "{temporary_name}"'))
    connection.execute(text(rebuilt_sql))
    connection.execute(text(
        f'INSERT INTO "{table_name}" ({quoted_columns}) SELECT {quoted_columns} FROM "{temporary_name}"'
    ))
    connection.execute(text(f'DROP TABLE "{temporary_name}"'))
    for item in schema_objects:
        connection.execute(text(str(item["sql"])))


def _prepare_part6_result_receipt_references(connection) -> None:
    """Make optional durable-result references FK-safe without inventing receipts."""
    from sqlalchemy import inspect as sa_inspect, text

    inspector = sa_inspect(connection)
    quote = connection.dialect.identifier_preparer.quote_identifier
    for table_name, column_name in _PART6_RESULT_RECEIPT_REFERENCES:
        if not inspector.has_table(table_name):
            continue
        columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        if column_name not in columns:
            continue
        if connection.dialect.name == "postgresql":
            connection.execute(
                text(
                    f"ALTER TABLE {quote(table_name)} ALTER COLUMN {quote(column_name)} DROP NOT NULL"
                )
            )
        elif connection.dialect.name == "sqlite" and not bool(columns[column_name].get("nullable")):
            _sqlite_make_column_nullable(connection, table_name, column_name)
        connection.execute(
            text(
                f"UPDATE {quote(table_name)} SET {quote(column_name)} = NULL "
                f"WHERE TRIM({quote(column_name)}) = ''"
            )
        )


def _classify_part6_legacy_receipts(connection, *, schema_column_was_missing: bool) -> None:
    """Fail closed for receipts created before the canonical evidence contract.

    Legacy rows are made explicitly manual-reviewable.  The migration deliberately
    leaves their manifest empty/version zero instead of manufacturing a complete
    no-effect manifest or immutable outcome from evidence the database never saw.
    """
    from sqlalchemy import inspect as sa_inspect, select, update

    inspector = sa_inspect(connection)
    required_tables = {
        "node_execution_receipts",
        "operation_nodes",
        "confirmation_groups",
        "proposal_plans",
    }
    if not required_tables.issubset(
        {table_name for table_name in required_tables if inspector.has_table(table_name)}
    ):
        return

    receipt_table = Base.metadata.tables["node_execution_receipts"]
    node_table = Base.metadata.tables["operation_nodes"]
    group_table = Base.metadata.tables["confirmation_groups"]
    plan_table = Base.metadata.tables["proposal_plans"]
    predicate = (
        receipt_table.c.receipt_schema_version >= 0
        if schema_column_was_missing
        else receipt_table.c.receipt_schema_version == 0
    )
    legacy_rows = connection.execute(
        select(receipt_table.c.node_id, receipt_table.c.plan_id).where(predicate)
    ).mappings().all()
    for row in legacy_rows:
        node_id = str(row["node_id"])
        plan_id = str(row["plan_id"])
        connection.execute(
            update(receipt_table)
            .where(receipt_table.c.node_id == node_id)
            .values(
                receipt_schema_version=0,
                status="manual_review",
                effect_manifest_schema_version=0,
                effect_manifest_json={},
                effect_manifest_digest="",
                completion_reason="legacy_receipt_requires_manual_review",
                error_classification="legacy_unproven",
            )
        )
        connection.execute(
            update(node_table)
            .where(node_table.c.node_id == node_id, node_table.c.plan_id == plan_id)
            .values(status="manual_review")
        )
        connection.execute(
            update(group_table)
            .where(group_table.c.plan_id == plan_id)
            .values(status="manual_review")
        )
        connection.execute(
            update(plan_table)
            .where(plan_table.c.plan_id == plan_id)
            .values(status="manual_review")
        )

_PART6_SQLITE_FOREIGN_KEYS = _PART6_POSTGRES_FOREIGN_KEYS


# The canonical Application projection binding FK
# (``application_records.application_id -> applications.id``) cannot be
# auto-installed on a *legacy* SQLite database whose ``application_records``
# table already has inbound foreign keys (``application_table_records`` is the
# production case).  Rebuilding such a table with ``PRAGMA foreign_keys=ON``
# would fail closed on the inbound-FK preflight (see
# ``_sqlite_rebuild_table_from_metadata``) and block a legal upgrade, while a
# silent offline rebuild risks data damage from the inbound ``ON DELETE
# CASCADE`` edge.  The migration therefore:
#
# - PostgreSQL (and any fresh SQLite database via ``create_all``) installs the
#   FK normally;
# - a legacy SQLite database with inbound FKs keeps the column bound through
#   the fail-closed ``_migrate_application_record_application_binding``
#   preflights (orphan / ownership-job mismatch / ambiguity), runs a scoped
#   ``PRAGMA foreign_key_check`` and fails closed on any violation, and logs
#   the skipped installation explicitly instead of rebuilding.
#
# The FK is *not* silently weakened: unknown application_id values are still
# rejected by the existing preflights, and every other Part-6 FK keeps the
# strict fail-closed rebuild path.
_APPLICATION_BINDING_SQLITE_FK = ("application_records", "fk_application_record_application")


def _sqlite_incoming_foreign_keys(inspector, table_name: str) -> list[dict]:
    """Foreign keys owned by other tables that reference ``table_name``."""
    incoming = []
    for child_table in inspector.get_table_names():
        if child_table == table_name:
            continue
        for foreign_key in inspector.get_foreign_keys(child_table):
            if str(foreign_key.get("referred_table") or "") == table_name:
                incoming.append({
                    "table": child_table,
                    "columns": tuple(foreign_key.get("constrained_columns") or ()),
                })
    return incoming


def _sqlite_has_foreign_key(
    inspector, table_name: str, local_columns: tuple[str, ...], remote_table: str,
    remote_columns: tuple[str, ...], *, ondelete: str,
) -> bool:
    expected_delete = str(ondelete or "").upper()
    for item in inspector.get_foreign_keys(table_name):
        observed_delete = str((item.get("options") or {}).get("ondelete") or "").upper()
        if (
            tuple(item.get("constrained_columns") or ()) == tuple(local_columns)
            and str(item.get("referred_table") or "") == str(remote_table)
            and tuple(item.get("referred_columns") or ()) == tuple(remote_columns)
            and observed_delete == expected_delete
        ):
            return True
    return False


def _sqlite_rebuild_table_from_metadata(connection, table_name: str) -> None:
    """Rebuild one legacy SQLite table from authoritative model metadata."""
    import re
    from sqlalchemy import inspect as sa_inspect, text
    from sqlalchemy.schema import CreateTable

    table = Base.metadata.tables.get(table_name)
    if table is None:
        raise RequiredForeignKeyMigrationError(
            f"SQLite foreign key migration needs manual review: no model metadata for table={table_name!r}"
        )
    inspector = sa_inspect(connection)
    foreign_keys_enabled = bool(connection.exec_driver_sql("PRAGMA foreign_keys").scalar())
    incoming = []
    if foreign_keys_enabled:
        for child_table in inspector.get_table_names():
            if child_table == table_name:
                continue
            for foreign_key in inspector.get_foreign_keys(child_table):
                if str(foreign_key.get("referred_table") or "") == table_name:
                    incoming.append({
                        "table": child_table,
                        "columns": tuple(foreign_key.get("constrained_columns") or ()),
                    })
        if incoming:
            raise RequiredForeignKeyMigrationError(
                f"SQLite foreign key migration needs manual review: table={table_name!r} has incoming "
                f"foreign keys while enforcement is enabled: {incoming!r}. Rebuild offline with foreign_keys disabled "
                "after the existing orphan preflight, then run PRAGMA foreign_key_check before re-enabling enforcement."
            )
    existing_columns = [str(item["name"]) for item in inspector.get_columns(table_name)]
    model_columns = [str(column.name) for column in table.columns]
    missing = [column for column in model_columns if column not in existing_columns]
    if missing:
        raise RequiredForeignKeyMigrationError(
            f"SQLite foreign key migration needs manual review: table={table_name!r}, missing_columns={missing!r}"
        )
    schema_objects = list(connection.execute(
        text(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE tbl_name=:name AND type IN ('index','trigger') AND sql IS NOT NULL"
        ),
        {"name": table_name},
    ).mappings())
    temporary_name = f"__part6_fk_{table_name}"
    if inspector.has_table(temporary_name):
        raise RequiredForeignKeyMigrationError(
            f"SQLite foreign key migration needs manual review: stale temporary table={temporary_name!r}"
        )
    create_sql = str(CreateTable(table).compile(dialect=connection.dialect))
    pattern = rf"(CREATE\s+TABLE\s+)(?:\"{re.escape(table_name)}\"|{re.escape(table_name)})(\s*\()"
    create_temp_sql, count = re.subn(
        pattern, rf'\1"{temporary_name}"\2', create_sql, count=1, flags=re.IGNORECASE,
    )
    if count != 1:
        raise RequiredForeignKeyMigrationError(
            f"SQLite foreign key migration needs manual review: could not compile rebuild DDL for table={table_name!r}"
        )
    quoted_columns = ", ".join(f'"{column}"' for column in model_columns)
    try:
        connection.exec_driver_sql("PRAGMA defer_foreign_keys = ON")
        connection.execute(text(create_temp_sql))
        connection.execute(text(
            f'INSERT INTO "{temporary_name}" ({quoted_columns}) '
            f'SELECT {quoted_columns} FROM "{table_name}"'
        ))
        connection.execute(text(f'DROP TABLE "{table_name}"'))
        connection.execute(text(f'ALTER TABLE "{temporary_name}" RENAME TO "{table_name}"'))
        for item in schema_objects:
            connection.execute(text(str(item["sql"])))
    except Exception as exc:
        raise RequiredForeignKeyMigrationError(
            f"SQLite foreign key migration needs manual review: table={table_name!r}, error={exc}"
        ) from exc


def _sqlite_should_skip_application_binding_rebuild(connection, table_name: str, missing) -> bool:
    """Whether the application-binding FK rebuild must be skipped on this
    legacy SQLite connection (inbound FKs + enforcement enabled)."""
    if table_name != _APPLICATION_BINDING_SQLITE_FK[0]:
        return False
    if not any(item[0] == _APPLICATION_BINDING_SQLITE_FK[1] for item in missing):
        return False
    if len(missing) != 1:
        # Additional missing constraints on the same table still need a rebuild;
        # the rebuild path keeps failing closed on inbound FKs.
        return False
    from sqlalchemy import inspect as sa_inspect
    foreign_keys_enabled = bool(connection.exec_driver_sql("PRAGMA foreign_keys").scalar())
    if not foreign_keys_enabled:
        return False
    return bool(_sqlite_incoming_foreign_keys(sa_inspect(connection), table_name))


def _verify_skipped_sqlite_application_binding(connection, table_name: str) -> None:
    """Fail-closed integrity verification for the documented legacy-SQLite
    skip of the application-binding FK installation."""
    import logging
    from sqlalchemy import inspect as sa_inspect

    logger = logging.getLogger(__name__)
    inspector = sa_inspect(connection)
    incoming = _sqlite_incoming_foreign_keys(inspector, table_name)
    quote = connection.dialect.identifier_preparer.quote_identifier
    # Scope the check to the projection table and every inbound child so the
    # migration never proceeds over a violation the skipped FK would catch.
    for target in [table_name, *[item["table"] for item in incoming]]:
        violations = connection.exec_driver_sql(
            f"PRAGMA foreign_key_check({quote(target)})"
        ).fetchall()
        if violations:
            raise RequiredForeignKeyMigrationError(
                f"Required SQLite foreign key migration needs manual review: table={target!r}, "
                f"foreign_key_check violations after application-binding FK skip: {violations[:5]!r}. "
                "Repair data explicitly; the constraint is not auto-installed on legacy SQLite "
                "databases with inbound foreign keys."
            )
    logger.warning(
        "SQLite foreign key migration: skipping auto-install of %r on legacy table %r "
        "because it has inbound foreign keys while PRAGMA foreign_keys=enabled "
        "(see _APPLICATION_BINDING_SQLITE_FK). New SQLite databases and PostgreSQL "
        "install this FK; integrity was verified via PRAGMA foreign_key_check.",
        _APPLICATION_BINDING_SQLITE_FK[1], table_name,
    )


def _ensure_part6_sqlite_foreign_keys(connection) -> None:
    """Install required Part 6 graph FKs on upgraded SQLite databases."""
    if connection.dialect.name != "sqlite":
        return
    from sqlalchemy import inspect as sa_inspect

    requirements_by_table: dict[str, list[tuple[str, tuple[str, ...], str, tuple[str, ...]]]] = {}
    for table_name, constraint_name, local_columns, remote_table, remote_columns in _PART6_SQLITE_FOREIGN_KEYS:
        requirements_by_table.setdefault(table_name, []).append(
            (constraint_name, local_columns, remote_table, remote_columns)
        )

    for table_name, requirements in requirements_by_table.items():
        inspector = sa_inspect(connection)
        if not inspector.has_table(table_name):
            continue
        applicable = [item for item in requirements if inspector.has_table(item[2])]
        missing = [
            item for item in applicable
            if not _sqlite_has_foreign_key(
                inspector, table_name, item[1], item[2], item[3], ondelete="RESTRICT"
            )
        ]
        if not missing:
            continue
        for constraint_name, local_columns, remote_table, remote_columns in missing:
            orphans = _foreign_key_orphan_samples(
                connection, table_name, local_columns, remote_table, remote_columns
            )
            if orphans:
                raise RequiredForeignKeyMigrationError(
                    f"Required SQLite foreign key migration needs manual review: table={table_name!r}, "
                    f"constraint={constraint_name!r}, orphan_samples={orphans!r}. "
                    "Repair data explicitly before rebuilding the table."
                )
        if _sqlite_should_skip_application_binding_rebuild(connection, table_name, missing):
            _verify_skipped_sqlite_application_binding(connection, table_name)
            continue
        _sqlite_rebuild_table_from_metadata(connection, table_name)
        inspector = sa_inspect(connection)
        for constraint_name, local_columns, remote_table, remote_columns in applicable:
            if not _sqlite_has_foreign_key(
                inspector, table_name, local_columns, remote_table, remote_columns, ondelete="RESTRICT"
            ):
                raise RequiredForeignKeyMigrationError(
                    f"Required SQLite foreign key migration needs manual review: table={table_name!r}, "
                    f"constraint={constraint_name!r}, observed=absent after rebuild."
                )


def _ensure_part6_postgresql_foreign_keys(connection) -> None:
    """Idempotently add Part 6 graph FKs to an existing PostgreSQL database."""
    if connection.dialect.name != "postgresql":
        return
    from sqlalchemy import inspect as sa_inspect, text
    quote = connection.dialect.identifier_preparer.quote_identifier
    # Serialize concurrent application-startup migrations in the current transaction.
    connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('offeru_part6_fk_migration_v1'))"))
    inspector = sa_inspect(connection)
    for table_name, constraint_name, local_columns, remote_table, remote_columns in _PART6_POSTGRES_FOREIGN_KEYS:
        if not inspector.has_table(table_name) or not inspector.has_table(remote_table):
            continue
        if _verify_required_foreign_key(
            inspector, table_name, constraint_name, local_columns, remote_table, remote_columns, ondelete="RESTRICT"
        ):
            continue
        orphans = _foreign_key_orphan_samples(
            connection, table_name, local_columns, remote_table, remote_columns
        )
        if orphans:
            raise RequiredForeignKeyMigrationError(
                f"Required foreign key migration needs manual review: table={table_name!r}, constraint={constraint_name!r}, "
                f"orphan_samples={orphans!r}. Repair data explicitly before installing the constraint."
            )
        local_sql = ", ".join(quote(column) for column in local_columns)
        remote_sql = ", ".join(quote(column) for column in remote_columns)
        ddl = (
            f"ALTER TABLE {quote(table_name)} ADD CONSTRAINT {quote(constraint_name)} "
            f"FOREIGN KEY ({local_sql}) REFERENCES {quote(remote_table)} ({remote_sql}) ON DELETE RESTRICT"
        )
        connection.execute(text(ddl))
        inspector = sa_inspect(connection)


def _migrate_application_record_application_binding(connection) -> None:
    """Fail-closed startup guard for the canonical Application projection binding.

    Adds the ``application_records.application_id`` index and binds unbound
    projection rows to their canonical Application using an owner/job-aware,
    ambiguity-quarantined backfill:

    - orphan preflight: any ``application_id`` without a parent Application
      fails closed (the FK would reject it later anyway);
    - ownership/job mismatch preflight: an existing ``application_id`` whose
      parent belongs to a different actor or job fails closed;
    - ambiguous duplicates: records whose ``(owner_actor_id, job_ref_id)``
      matches more than one Application are quarantined into the migration
      error report (bounded samples) instead of guessing a binding;
    - only rows with exactly one candidate are backfilled, so restarts are
      idempotent and no destructive overwrite ever happens.

    Callers must run this after the ADD COLUMN loop and before FK installation.
    """
    from sqlalchemy import inspect as sa_inspect, text

    inspector = sa_inspect(connection)
    if not inspector.has_table("application_records") or not inspector.has_table("applications"):
        return
    columns = {str(item["name"]) for item in inspector.get_columns("application_records")}
    if "application_id" not in columns or "job_ref_id" not in columns:
        return
    quote = connection.dialect.identifier_preparer.quote_identifier
    records_table = quote("application_records")
    applications_table = quote("applications")

    # 1) Named, deterministic index for projection lookups.
    connection.execute(text(
        f"CREATE INDEX IF NOT EXISTS {quote('ix_application_records_application_id')} "
        f"ON {records_table} ({quote('application_id')})"
    ))

    # 2) Orphan preflight: a bound application_id must have a parent row.
    orphans = _foreign_key_orphan_samples(
        connection, "application_records", ("application_id",), "applications", ("id",)
    )
    if orphans:
        raise RequiredForeignKeyMigrationError(
            "Required foreign key migration needs manual review: table='application_records', "
            f"constraint='fk_application_record_application', orphan_samples={orphans!r}. "
            "Repair application_id values explicitly before installing the constraint."
        )

    # 3) Ownership/job mismatch preflight: a bound application_id must point to
    #    an Application of the same owner and job.
    mismatch_samples = connection.execute(text(
        f"SELECT {records_table}.{quote('id')} AS record_id, "
        f"{records_table}.{quote('application_id')} AS application_id, "
        f"{records_table}.{quote('job_ref_id')} AS job_ref_id, "
        f"{records_table}.{quote('owner_actor_id')} AS record_owner, "
        f"{applications_table}.{quote('owner_actor_id')} AS application_owner, "
        f"{applications_table}.{quote('job_id')} AS application_job_id "
        f"FROM {records_table} "
        f"JOIN {applications_table} ON {applications_table}.{quote('id')} = {records_table}.{quote('application_id')} "
        f"WHERE {records_table}.{quote('application_id')} IS NOT NULL "
        f"AND ({applications_table}.{quote('owner_actor_id')} <> {records_table}.{quote('owner_actor_id')} "
        f"OR {applications_table}.{quote('job_id')} <> {records_table}.{quote('job_ref_id')}) "
        f"LIMIT 5"
    )).mappings().all()
    if mismatch_samples:
        raise RequiredForeignKeyMigrationError(
            "Required foreign key migration needs manual review: table='application_records', "
            f"binding mismatch samples={[dict(row) for row in mismatch_samples]!r}. "
            "Repair application_id values explicitly before installing the constraint."
        )

    # 4) Ambiguity preflight: unbound rows must not have more than one
    #    (owner, job) candidate Application. Ambiguous rows are quarantined
    #    into this fail-closed error instead of guessed.
    ambiguous_samples = connection.execute(text(
        f"SELECT {records_table}.{quote('id')} AS record_id, "
        f"{records_table}.{quote('owner_actor_id')} AS owner_actor_id, "
        f"{records_table}.{quote('job_ref_id')} AS job_ref_id, "
        f"COUNT(*) AS candidate_count "
        f"FROM {records_table} "
        f"JOIN {applications_table} ON {applications_table}.{quote('job_id')} = {records_table}.{quote('job_ref_id')} "
        f"AND {applications_table}.{quote('owner_actor_id')} = {records_table}.{quote('owner_actor_id')} "
        f"WHERE {records_table}.{quote('application_id')} IS NULL "
        f"AND {records_table}.{quote('job_ref_id')} IS NOT NULL "
        f"GROUP BY {records_table}.{quote('id')}, {records_table}.{quote('owner_actor_id')}, {records_table}.{quote('job_ref_id')} "
        f"HAVING COUNT(*) > 1 "
        f"LIMIT 5"
    )).mappings().all()
    if ambiguous_samples:
        raise RequiredForeignKeyMigrationError(
            "Required foreign key migration needs manual review: table='application_records', "
            f"ambiguous application binding samples={[dict(row) for row in ambiguous_samples]!r}. "
            "Each record must resolve to exactly one canonical Application; "
            "repair duplicate Application rows explicitly before the constraint is installed."
        )

    # 5) Bounded unambiguous backfill: bind only rows with exactly one
    #    (owner, job) candidate. Idempotent: unbound rows without a candidate
    #    are left untouched for future backfill after canonical Applications
    #    are created.
    connection.execute(text(
        f"UPDATE {records_table} "
        f"SET {quote('application_id')} = ("
        f"SELECT {applications_table}.{quote('id')} FROM {applications_table} "
        f"WHERE {applications_table}.{quote('job_id')} = {records_table}.{quote('job_ref_id')} "
        f"AND {applications_table}.{quote('owner_actor_id')} = {records_table}.{quote('owner_actor_id')} "
        f"ORDER BY {applications_table}.{quote('id')} ASC LIMIT 1"
        f") "
        f"WHERE {records_table}.{quote('application_id')} IS NULL "
        f"AND {records_table}.{quote('job_ref_id')} IS NOT NULL"
    ))


async def get_db():
    """FastAPI 渚濊禆娉ㄥ叆锛氭彁渚涙暟鎹簱浼氳瘽"""
    async with async_session() as session:
        yield session


async def init_db():
    """Create tables, add missing columns, seed data, and validate the registry."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_auto_migrate)
    await seed_templates()
    await seed_system_batches()
    await migrate_triage_status()
    from app.operator.registry import validate_registry_contracts
    validate_registry_contracts()


def _auto_migrate(connection):
    """Add missing model columns and required Part 6 constraints fail-closed."""
    from sqlalchemy import inspect as sa_inspect, text
    inspector = sa_inspect(connection)
    receipt_columns_before = (
        {column["name"] for column in inspector.get_columns("node_execution_receipts")}
        if inspector.has_table("node_execution_receipts")
        else set()
    )
    schema_column_was_missing = bool(receipt_columns_before) and "receipt_schema_version" not in receipt_columns_before
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing_cols = {c["name"] for c in inspector.get_columns(table.name)}
        for col in table.columns:
            if col.name not in existing_cols:
                col_type = col.type.compile(connection.dialect)
                col_type_upper = col_type.upper()
                default_clause = _migration_column_default_clause(
                    col,
                    col_type_upper=col_type_upper,
                    dialect_name=str(getattr(connection.dialect, "name", "") or ""),
                )
                nullable = "" if col.nullable else " NOT NULL"
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}{nullable}{default_clause}'
                connection.execute(text(ddl))

    _migrate_application_record_application_binding(connection)
    _prepare_part6_result_receipt_references(connection)
    _classify_part6_legacy_receipts(
        connection, schema_column_was_missing=schema_column_was_missing
    )
    for table_name, index_name, columns in _REQUIRED_UNIQUE_INDEXES:
        if not sa_inspect(connection).has_table(table_name):
            continue
        if _verify_required_index(connection, table_name, index_name, columns):
            continue
        duplicates = _duplicate_samples(connection, table_name, columns)
        if duplicates:
            raise RequiredIndexMigrationError(
                f"Required index migration needs manual review: table={table_name!r}, index={index_name!r}, "
                f"expected unique ordered columns={columns!r}; duplicate samples (bounded)={duplicates!r}. "
                "Do not delete or merge rows automatically."
            )
        connection.execute(text(_unique_index_ddl(connection.dialect, table_name=table_name, index_name=index_name, columns=columns)))
        if not _verify_required_index(connection, table_name, index_name, columns):
            raise RequiredIndexMigrationError(
                f"Required index migration needs manual review: table={table_name!r}, index={index_name!r}, "
                f"expected unique ordered columns={columns!r}, observed=absent after create (possible race)."
            )
    _ensure_part6_sqlite_foreign_keys(connection)
    _ensure_part6_postgresql_foreign_keys(connection)

# =============================================
# 鍐呯疆妯℃澘绉嶅瓙鏁版嵁
# =============================================
# 棣栨鍚姩鏃惰嚜鍔ㄦ彃鍏?4 濂楀唴缃ā鏉匡紝
# 姣忓妯℃澘鍖呭惈涓婚閰嶈壊銆佸瓧鍙?闂磋窛 CSS 鍙橀噺銆?
# 閫氳繃妫€鏌?is_builtin + name 鍘婚噸锛岄伩鍏嶉噸澶嶆彃鍏ャ€?
# =============================================

BUILTIN_TEMPLATES = [
    {
        "name": "经典蓝",
        "thumbnail_url": "",
        "css_variables": {
            "primaryColor": "#2563eb",
            "accentColor": "#1e40af",
            "bodySize": "13",
            "headingSize": "16",
            "lineHeight": "1.5",
            "pageMargin": "2.2",
            "sectionGap": "14",
            "fontFamily": "Inter, 'Noto Sans SC', sans-serif",
        },
        "is_builtin": True,
    },
    {
        "name": "现代灰",
        "thumbnail_url": "",
        "css_variables": {
            "primaryColor": "#374151",
            "accentColor": "#6b7280",
            "bodySize": "12.5",
            "headingSize": "15",
            "lineHeight": "1.45",
            "pageMargin": "2.0",
            "sectionGap": "12",
            "fontFamily": "'Source Sans Pro', 'Noto Sans SC', sans-serif",
        },
        "is_builtin": True,
    },
    {
        "name": "优雅紫",
        "thumbnail_url": "",
        "css_variables": {
            "primaryColor": "#7c3aed",
            "accentColor": "#5b21b6",
            "bodySize": "13",
            "headingSize": "16",
            "lineHeight": "1.55",
            "pageMargin": "2.4",
            "sectionGap": "16",
            "fontFamily": "'Playfair Display', 'Noto Serif SC', serif",
        },
        "is_builtin": True,
    },
    {
        "name": "清新绿",
        "thumbnail_url": "",
        "css_variables": {
            "primaryColor": "#059669",
            "accentColor": "#047857",
            "bodySize": "13",
            "headingSize": "15.5",
            "lineHeight": "1.5",
            "pageMargin": "2.0",
            "sectionGap": "14",
            "fontFamily": "'Nunito', 'Noto Sans SC', sans-serif",
        },
        "is_builtin": True,
    },
]


async def seed_templates():
    """濡傛灉鍐呯疆妯℃澘涓嶅瓨鍦ㄥ垯鎻掑叆"""
    from app.models.models import ResumeTemplate
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(
            select(ResumeTemplate).where(ResumeTemplate.is_builtin == True)
        )
        existing = {t.name for t in result.scalars().all()}

        for tpl in BUILTIN_TEMPLATES:
            if tpl["name"] not in existing:
                session.add(ResumeTemplate(**tpl))

        await session.commit()


async def seed_system_batches():
    """Ensure the legacy import batch exists for historical records."""
    from app.models.models import Batch
    from sqlalchemy import select

    async with async_session() as session:
        existing = (
            await session.execute(select(Batch).where(Batch.id == "legacy-import"))
        ).scalar_one_or_none()
        if not existing:
            session.add(
                Batch(
                    id="legacy-import",
                    source="legacy",
                    keywords=["historical"],
                    location="",
                    total_fetched=0,
                )
            )
            await session.commit()


async def migrate_triage_status():
    """灏嗘棫鐨?triage_status 鍊硷紙screened/unscreened锛夊綊涓€鍖栦负 picked/inbox"""
    import logging
    from sqlalchemy import text

    logger = logging.getLogger(__name__)

    try:
        async with async_session() as session:
            need_migrate = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM jobs WHERE triage_status IN ('screened','unscreened')"
                        " UNION ALL "
                        "SELECT COUNT(*) FROM pools WHERE scope IN ('screened','unscreened')"
                    )
                )
            ).all()
            if all(row[0] == 0 for row in need_migrate):
                return

            await session.execute(
                text("UPDATE jobs SET triage_status = 'picked' WHERE triage_status = 'screened'")
            )
            await session.execute(
                text("UPDATE jobs SET triage_status = 'inbox' WHERE triage_status = 'unscreened'")
            )
            await session.execute(
                text("UPDATE pools SET scope = 'picked' WHERE scope = 'screened'")
            )
            await session.execute(
                text("UPDATE pools SET scope = 'inbox' WHERE scope = 'unscreened'")
            )
            await session.commit()
            logger.info("migrate_triage_status: normalized legacy screened/unscreened values")
    except Exception as exc:
        logger.error("migrate_triage_status failed: %s", exc)
