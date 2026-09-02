from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.agent.messages import create_custom_message
from app.models import models
from app.operator.capability_map import describe_capability_contract
from app.operator.errors import OperatorError
from app.operator.registry import RegistryContractError


CAPABILITY_LOADING_STATE_KEY = "capability_loading_enforced"

_MODEL_TOOL_OPERATIONS: Mapping[str, str] = {
    "query_records": "query",
    "get_record": "read",
    "create_record": "create",
    "patch_record": "patch",
    "delete_or_archive_record": "delete_or_archive",
}


def capability_references_for_tool(
    tool_name: str,
    args: Mapping[str, Any],
) -> list[tuple[str, str, str]]:
    """All capability references a tool call must have loaded.

    ``manage_session(activate_skill)`` must bind both the session-command
    contract and the Skill/SOP contract (SPEC §5.4): a Skill may only be
    activated after the model loaded its exact SOP schema and persisted a
    digest-bound receipt.
    """
    operation = _MODEL_TOOL_OPERATIONS.get(str(tool_name or ""))
    if operation is not None:
        return [("model", str(args.get("model") or "").strip(), operation)]
    if tool_name == "invoke_action":
        return [("action", str(args.get("action") or "").strip(), "invoke")]
    if tool_name == "manage_session":
        session_operation = str(args.get("operation") or "").strip().lower()
        references: list[tuple[str, str, str]] = [
            ("session-command", "manage_session", session_operation)
        ]
        if session_operation == "activate_skill":
            updates = args.get("updates")
            if not isinstance(updates, Mapping):
                updates = {}
            skill_name = str(
                updates.get("active_skill") or updates.get("skill") or updates.get("name") or ""
            ).strip()
            if skill_name:
                references.append(("skill", skill_name, "activate"))
        return references
    return []


def capability_reference_for_tool(
    tool_name: str,
    args: Mapping[str, Any],
) -> tuple[str, str, str] | None:
    references = capability_references_for_tool(tool_name, args)
    return references[0] if references else None


def _current_schema(kind: str, name: str, operation: str) -> dict[str, Any]:
    try:
        return describe_capability_contract(kind, name, operation)
    except RegistryContractError as exc:
        raise OperatorError(
            "validation_error",
            str(exc),
            {"capability_kind": kind, "capability_name": name, "operation": operation},
        ) from exc


async def persist_capability_load_receipt(
    db: Any,
    actor: Any,
    schema: Mapping[str, Any],
) -> models.AgentCapabilityLoadReceipt:
    kind = str(schema.get("kind") or "")
    name = str(schema.get("name") or "")
    operation = str(schema.get("operation") or "")
    digest = str(schema.get("schema_digest") or "")
    identity = (str(actor.actor_id), str(actor.session_id), kind, name, operation)
    receipt = await db.get(models.AgentCapabilityLoadReceipt, identity)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if receipt is None:
        receipt = models.AgentCapabilityLoadReceipt(
            actor_id=identity[0],
            session_id=identity[1],
            capability_kind=kind,
            capability_name=name,
            operation=operation,
            schema_digest=digest,
            loaded_at=now,
            updated_at=now,
        )
        db.add(receipt)
    else:
        receipt.schema_digest = digest
        receipt.loaded_at = now
        receipt.updated_at = now
    await db.commit()
    return receipt


async def require_loaded_capability(
    db: Any,
    actor: Any,
    *,
    tool_name: str,
    args: Mapping[str, Any],
) -> None:
    references = capability_references_for_tool(tool_name, args)
    if not references:
        return
    for kind, name, operation in references:
        schema = _current_schema(kind, name, operation)
        identity = (str(actor.actor_id), str(actor.session_id), kind, name, operation)
        receipt = await db.get(models.AgentCapabilityLoadReceipt, identity)
        details = {
            "capability_kind": kind,
            "capability_name": name,
            "operation": operation,
            "current_digest": schema["schema_digest"],
        }
        if receipt is None:
            if kind == "skill" and operation == "activate":
                raise OperatorError(
                    "skill_sop_contract_required",
                    "Load the Skill/SOP contract (describe_capability kind=skill) before activating it.",
                    _skill_receipt_details(name, schema["schema_digest"]),
                )
            raise OperatorError(
                "capability_schema_required",
                "Load the exact capability operation schema before using this tool.",
                details,
            )
        if str(receipt.schema_digest or "") != str(schema["schema_digest"]):
            if kind == "skill" and operation == "activate":
                raise OperatorError(
                    "skill_sop_contract_stale",
                    "The loaded Skill contract is stale; reload it before activating the Skill.",
                    {
                        **_skill_receipt_details(name, schema["schema_digest"]),
                        "loaded_digest": str(receipt.schema_digest or ""),
                    },
                )
            raise OperatorError(
                "capability_schema_stale",
                "The loaded capability schema is stale; reload it before using this tool.",
                {**details, "loaded_digest": str(receipt.schema_digest or "")},
            )


def _skill_receipt_details(skill_name: str, current_digest: str) -> dict[str, Any]:
    """Structured fail-closed recovery for a missing/stale Skill/SOP receipt.

    The model can recover by describing the Skill contract and reactivating
    with the same user intent; the action itself must never be retried with
    different parameters before the receipt binds the current digest.
    """
    return {
        "capability_kind": "skill",
        "capability_name": skill_name,
        "operation": "activate",
        "current_digest": current_digest,
        "recovery": {
            "code": "skill_sop_contract_required",
            "missing_requirements": [
                {
                    "name": "skill_sop_receipt",
                    "status": "missing",
                    "satisfy_with": [
                        {
                            "tool": "describe_capability",
                            "kind": "skill",
                            "name": skill_name,
                            "operation": "activate",
                        }
                    ],
                }
            ],
            "next_allowed_operations": [
                {
                    "tool": "describe_capability",
                    "kind": "skill",
                    "name": skill_name,
                    "operation": "activate",
                }
            ],
            "retry_same_action_after": ["after_skill_contract_loaded"],
        },
    }


def _retained_schema_keys(messages: Sequence[Any]) -> set[tuple[str, str, str, str]]:
    retained: set[tuple[str, str, str, str]] = set()

    def visit(value: Any) -> None:
        if not isinstance(value, Mapping):
            return
        kind = str(value.get("capability_kind") or value.get("kind") or "")
        name = str(value.get("capability_name") or value.get("name") or "")
        operation = str(value.get("operation") or "")
        digest = str(value.get("schema_digest") or "")
        if kind and name and operation and digest:
            retained.add((kind, name, operation, digest))
        for key in ("details", "raw_result", "capability", "schema"):
            child = value.get(key)
            if isinstance(child, Mapping) and child is not value:
                visit(child)

    for message in messages:
        details = message.get("details") if isinstance(message, Mapping) else getattr(message, "details", None)
        custom_type = message.get("custom_type") if isinstance(message, Mapping) else getattr(message, "custom_type", None)
        if custom_type == "capability_schema" or isinstance(details, Mapping):
            visit(details or {})
    return retained


async def rehydrate_loaded_capabilities(
    db: Any,
    actor: Any,
    messages: Sequence[Any],
    *,
    session_id: str = "",
) -> list[Any]:
    rows = (
        await db.execute(
            select(models.AgentCapabilityLoadReceipt)
            .where(
                models.AgentCapabilityLoadReceipt.actor_id == str(actor.actor_id),
                models.AgentCapabilityLoadReceipt.session_id == str(session_id or getattr(actor, "session_id", "")),
            )
            .order_by(
                models.AgentCapabilityLoadReceipt.capability_kind,
                models.AgentCapabilityLoadReceipt.capability_name,
                models.AgentCapabilityLoadReceipt.operation,
            )
        )
    ).scalars().all()
    retained = _retained_schema_keys(messages)
    restored = []
    for row in rows:
        try:
            schema = describe_capability_contract(
                str(row.capability_kind),
                str(row.capability_name),
                str(row.operation),
            )
        except RegistryContractError:
            continue
        digest = str(schema["schema_digest"])
        key = (str(row.capability_kind), str(row.capability_name), str(row.operation), digest)
        if str(row.schema_digest or "") != digest or key in retained:
            continue
        details = {
            "capability_kind": key[0],
            "capability_name": key[1],
            "operation": key[2],
            "schema_digest": digest,
            "schema": schema,
        }
        restored.append(
            create_custom_message(
                "capability_schema",
                "Authoritative loaded capability schema:\n" + json.dumps(schema, ensure_ascii=False, sort_keys=True),
                display=False,
                details=details,
            )
        )
    return restored
