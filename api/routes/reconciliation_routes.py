from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from api.security import require_api_key
from application.celery_app import amazon_reconciliation_task

router=APIRouter(prefix="/api/reconciliation",tags=["reconciliation"],dependencies=[Depends(require_api_key)])
class RunRequest(BaseModel): connection_id:str=Field(min_length=1,max_length=100)
@router.post("/runs")
async def create_run(payload:RunRequest,request:Request,response:Response)->dict[str,Any]:
    p=request.state.principal
    if p.role not in {"owner","admin","operator"}: raise HTTPException(status_code=403,detail={"error":"forbidden","message":"Operator role is required."})
    connection=request.app.state.tenant_repository.get_connection(tenant_id=p.tenant_id,connection_id=payload.connection_id)
    if connection is None: raise HTTPException(status_code=404,detail={"error":"not_found","message":"Amazon connection not found."})
    idempotency_key=request.headers.get("Idempotency-Key")
    if not idempotency_key: raise HTTPException(status_code=400,detail={"error":"idempotency_key_required","message":"Idempotency-Key header is required."})
    task,created=request.app.state.task_repository.create_once(
        tenant_id=p.tenant_id,actor_id=p.user_id,queue_name="sync",action="amazon.reconciliation",
        payload={"connection_id":connection.id},idempotency_key=f"reconciliation:{connection.id}:{idempotency_key}",
        correlation_id=getattr(request.state,"request_id",idempotency_key),max_attempts=5,
    )
    if created: amazon_reconciliation_task.delay(task.id,p.tenant_id,connection.id)
    request.app.state.audit_logger.log_operation(
        action="amazon_reconciliation_requested",status="success",
        details={"tenant_id":p.tenant_id,"connection_id":connection.id,"task_id":task.id},
        actor=p.user_id,risk_level="low",
    )
    response.status_code=202
    return {"status":task.status,"connection_id":connection.id,"task_id":task.id,"idempotent_replay":not created}
@router.get("/runs")
async def list_runs(request:Request,limit:int=30)->dict[str,Any]:
    rows=request.app.state.reconciliation_repository.list_runs(tenant_id=request.state.principal.tenant_id,limit=limit)
    items=[]
    for row in rows:
        differences=request.app.state.reconciliation_repository.differences(tenant_id=request.state.principal.tenant_id,run_id=row.id)
        summary=row.summary or {}
        items.append({"id":row.id,"connection_id":row.channel_connection_id,"status":row.status,"summary":summary,"started_at":row.started_at.isoformat(),"completed_at":row.completed_at.isoformat() if row.completed_at else None,"data_source":summary.get("data_source","unknown"),"verified_amazon_data":bool(summary.get("verified_amazon_data",False)),"amazon_observed_at":summary.get("amazon_observed_at"),"amazon_collected_at":summary.get("amazon_collected_at"),"freshness_status":summary.get("freshness_status","unknown"),"differences":[{"id":item.id,"resource_type":item.resource_type,"resource_id":item.resource_id,"difference_type":item.difference_type,"amazon_value":item.amazon_value,"internal_value":item.internal_value,"created_at":item.created_at.isoformat()} for item in differences]})
    return {"total":len(items),"items":items}
