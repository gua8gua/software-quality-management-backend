"""Exclusive, cancellable TLR execution around the existing checkpoint pipeline.

PostgreSQL session advisory lock survives pipeline commits. Request disconnect
is an ownership loss, not a semantic no-link. Independent monitor connections
observe durable progress; they never modify evidence or rerun a model.
"""
import asyncio
import hashlib
import json
import time
from contextlib import suppress

from sqlalchemy import func, select, text

from app.core.execution import ExecutionStatus
from app.db.session import AsyncSessionLocal, engine
from app.modules.traceability.models import (
    Candidate,
    RetrievalProjection,
    SemanticAnnotation,
    TlrRun,
)


async def run_owned_tlr(request, run_id, operation, *, idle_seconds=900):
    if engine.dialect.name != "postgresql":
        raise ValueError("Durable TLR execution requires PostgreSQL advisory locks")
    key = int.from_bytes(hashlib.sha256(f"quality-tlr:{run_id}".encode()).digest()[:8], signed=True)
    async with engine.connect() as lock_connection:
        locked = await lock_connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        if not locked:
            operation.close()
            raise ValueError("This TLR already has an active executor")
        running = asyncio.create_task(operation)
        last, progress_at = None, time.monotonic()
        try:
            while not running.done():
                if await request.is_disconnected():
                    raise asyncio.CancelledError("TLR owning executor disconnected")
                async with AsyncSessionLocal() as monitor:
                    row = await monitor.get(TlrRun, run_id)
                    counts = [await monitor.scalar(select(func.count()).select_from(model).where(model.run_id == run_id))
                              for model in (SemanticAnnotation, RetrievalProjection, Candidate)]
                    validated = await monitor.scalar(select(func.count()).select_from(Candidate).where(
                        Candidate.run_id == run_id, Candidate.execution_status.in_([ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED])))
                    fingerprint = json.dumps([row.stage if row else None, row.metrics if row else None, counts, validated], sort_keys=True)
                if last != fingerprint:
                    last, progress_at = fingerprint, time.monotonic()
                if time.monotonic() - progress_at >= idle_seconds:
                    raise TimeoutError("TLR no_business_progress deadline exceeded; checkpoint facts preserved")
                await asyncio.wait({running}, timeout=min(5, idle_seconds))
            return await running
        finally:
            if not running.done():
                running.cancel()
                with suppress(asyncio.CancelledError):
                    await running
            await lock_connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
