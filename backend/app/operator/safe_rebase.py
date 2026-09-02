from __future__ import annotations
import hashlib,json
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any
from sqlalchemy import func,select
from app.models import models

class SafeRebaseConflict(RuntimeError): pass
class SafeRebaseLimitError(RuntimeError): pass
@dataclass(frozen=True)
class RebaseAssessment:
    status:str; rebased_updates:dict[str,Any]; competing_fields:tuple[str,...]

def evaluate_field_rebase(*,sealed_before:Mapping[str,Any],desired_updates:Mapping[str,Any],current_record:Mapping[str,Any])->RebaseAssessment:
    rebased={}; conflicts=[]
    for field,desired in desired_updates.items():
        current=current_record.get(field); before=sealed_before.get(field)
        if current==desired: continue
        if current!=before: conflicts.append(str(field))
        else: rebased[str(field)]=desired
    status="conflict" if conflicts else "already_satisfied" if not rebased else "safe_rebase"
    return RebaseAssessment(status,rebased,tuple(sorted(conflicts)))

def _digest(v): return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(",",":"),default=str).encode()).hexdigest()

async def record_safe_rebase(db,actor,*,node_id,sealed_before,current_record,current_version,desired_updates=None,event_key="",max_attempts=2,_defer_commit=False):
    node=await db.get(models.OperationNode,str(node_id))
    if not node: raise SafeRebaseConflict("node not found")
    plan=await db.get(models.ProposalPlan,node.plan_id)
    if not plan or plan.actor_id!=str(actor.actor_id) or plan.session_id!=str(actor.session_id): raise SafeRebaseConflict("node outside actor/session scope")
    event_key=str(event_key or f"{current_version}:{_digest(current_record)}")
    existing=(await db.execute(select(models.PlanRebaseReceipt).where(models.PlanRebaseReceipt.node_id==node.node_id,models.PlanRebaseReceipt.event_key==event_key))).scalar_one_or_none()
    if existing: return existing
    attempt=int(await db.scalar(select(func.coalesce(func.max(models.PlanRebaseReceipt.attempt),0)).where(models.PlanRebaseReceipt.node_id==node.node_id)) or 0)+1
    if attempt>max(1,int(max_attempts)): raise SafeRebaseLimitError("safe rebase attempt limit exceeded")
    desired=dict(desired_updates) if isinstance(desired_updates,Mapping) else dict((node.payload_json or {}).get("updates") or {})
    assessment=evaluate_field_rebase(sealed_before=sealed_before,desired_updates=desired,current_record=current_record)
    current_digest=_digest(current_record)
    receipt=models.PlanRebaseReceipt(node_id=node.node_id,attempt=attempt,plan_id=plan.plan_id,actor_id=plan.actor_id,session_id=plan.session_id,event_key=event_key,status=assessment.status,current_version=str(current_version),current_digest=current_digest,rebased_updates=assessment.rebased_updates,competing_fields=list(assessment.competing_fields))
    revision=models.NodeExecutionRevision(node_id=node.node_id,attempt=attempt,plan_id=plan.plan_id,status=assessment.status,current_version=str(current_version),resolved_payload={**dict(node.payload_json or {}),"updates":assessment.rebased_updates},receipt_digest=_digest({"node":node.node_id,"attempt":attempt,"status":assessment.status,"current":current_digest,"updates":assessment.rebased_updates}))
    db.add_all([receipt,revision]); await (db.flush() if _defer_commit else db.commit())
    if assessment.status=="conflict": raise SafeRebaseConflict("competing touched fields: "+", ".join(assessment.competing_fields))
    return receipt
