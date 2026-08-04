from __future__ import annotations
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from infrastructure.database.models import ReconciliationDifferenceRow, ReconciliationRunRow

class ReconciliationRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None: self.session_factory=session_factory
    def save(self, *, tenant_id:str, connection_id:str, status:str, summary:dict[str,Any], differences:list[dict[str,Any]], started_at:datetime, completed_at:datetime) -> ReconciliationRunRow:
        run_id=str(uuid4())
        with self.session_factory.begin() as session:
            row=ReconciliationRunRow(id=run_id,tenant_id=tenant_id,channel_connection_id=connection_id,status=status,summary=summary,started_at=started_at,completed_at=completed_at)
            session.add(row)
            session.flush()
            for item in differences:
                session.add(ReconciliationDifferenceRow(id=str(uuid4()),tenant_id=tenant_id,run_id=run_id,**item))
            session.flush(); session.expunge(row)
        return row
    def list_runs(self, *, tenant_id:str, limit:int=30) -> list[ReconciliationRunRow]:
        with self.session_factory() as session: return list(session.scalars(select(ReconciliationRunRow).where(ReconciliationRunRow.tenant_id==tenant_id).order_by(ReconciliationRunRow.created_at.desc()).limit(limit)))
    def differences(self, *, tenant_id:str, run_id:str) -> list[ReconciliationDifferenceRow]:
        with self.session_factory() as session: return list(session.scalars(select(ReconciliationDifferenceRow).where(ReconciliationDifferenceRow.tenant_id==tenant_id,ReconciliationDifferenceRow.run_id==run_id)))
