"""Layer matrix planning. A cell is an independently reproducible directed TLR run."""

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter
from pydantic import Field, model_validator
from sqlalchemy import select

from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError
from app.modules.tlr.router import Scope, Service
from app.modules.tlr.models import TlrRun
from app.modules.tlr.schemas import Contract, Identifier, RunInput, RunOptions, RunView
from app.schemas.common import ApiResponse

LAYERS = [
    ("context", "背景 / 风险 / 自然语言"),
    ("requirements", "需求"),
    ("architecture", "架构 / 接口"),
    ("design", "详细设计"),
    ("implementation", "代码实现"),
    ("verification", "测试用例 / 测试代码"),
    ("assurance", "评审 / 其它生命周期文档"),
    ("operation", "发布 / 运维"),
]
KINDS = {
    "hazard": "context",
    "natural_language": "context",
    "requirement": "requirements",
    "architecture_design": "architecture",
    "architecture_model": "architecture",
    "interface": "architecture",
    "design": "design",
    "detailed_design": "design",
    "code": "implementation",
    "test_case": "verification",
    "test_code": "verification",
    "test": "verification",
    "test_plan": "verification",
    "test_report": "verification",
    "release": "operation",
    "operation": "operation",
    "feedback": "operation",
}


def layer_of(artifact):
    explicit = (artifact.structure or {}).get("layer")
    return explicit if explicit in dict(LAYERS) else KINDS.get(artifact.kind, "assurance")


def layer_inventory(artifacts):
    groups = {key: [] for key, _ in LAYERS}
    excluded = []
    for a in artifacts:
        if a.kind == "package" or (a.structure or {}).get("content_status") == "reference_only":
            excluded.append(a.external_id)
        else:
            groups[layer_of(a)].append(a.external_id)
    active = [key for key, _ in LAYERS if groups[key]]
    return {
        "layers": [
            {"id": key, "label": label, "artifact_ids": groups[key], "count": len(groups[key])}
            for key, label in LAYERS
        ],
        "default_pairs": [[a, b] for a, b in zip(active, active[1:], strict=False)],
        "excluded_ids": excluded,
        "default_policy": "adjacent_nonempty_layers_v1",
    }


class LayerPair(Contract):
    source: str
    target: str

    @model_validator(mode="after")
    def valid_pair(self):
        if (
            self.source not in dict(LAYERS)
            or self.target not in dict(LAYERS)
            or self.source == self.target
        ):
            raise ValueError("Select two distinct supported layers")
        return self


class PlanInput(Contract):
    tenant_id: Identifier
    project_id: Identifier
    dataset_id: str
    pairs: list[LayerPair] | None = Field(default=None, max_length=56)
    options: RunOptions = Field(
        default_factory=lambda: RunOptions(source_preprocessor="auto", target_preprocessor="auto")
    )


router = APIRouter(prefix="/tlr", tags=["tlr-layer-planning"])


@router.get("/datasets/{dataset_id}/layers", response_model=ApiResponse[dict])
async def layers(dataset_id: str, tenant_id: Scope, project_id: Scope, service: Service):
    await service.repo.dataset(dataset_id, tenant_id, project_id)
    return ApiResponse(data=layer_inventory(await service.repo.artifacts(dataset_id)))


@router.post("/plans", response_model=ApiResponse[dict])
async def create_plan(request: PlanInput, service: Service):
    await service.repo.dataset(request.dataset_id, request.tenant_id, request.project_id)
    inventory = layer_inventory(await service.repo.artifacts(request.dataset_id))
    groups = {x["id"]: x["artifact_ids"] for x in inventory["layers"]}
    pairs = (
        [(p.source, p.target) for p in request.pairs]
        if request.pairs is not None
        else inventory["default_pairs"]
    )
    if not pairs or len({tuple(p) for p in pairs}) != len(pairs):
        raise AppError(ErrorCode.DATA_INVALID, "至少选择一个层间比较，且不能重复")
    if any(not groups[a] or not groups[b] for a, b in pairs):
        raise AppError(ErrorCode.DATA_INVALID, "所选层没有可分析的正文；引用节点和包节点不参与检测")
    plan_id = str(uuid4())
    batch_label = f"TLR-{datetime.now().astimezone():%Y%m%d-%H%M%S}-{plan_id[:8]}"
    runs = []
    try:
        for batch_index, (source, target) in enumerate(pairs):
            run = await service.create_run(
                RunInput(
                    tenant_id=request.tenant_id,
                    project_id=request.project_id,
                    dataset_id=request.dataset_id,
                    source_ids=groups[source],
                    target_ids=groups[target],
                    options=request.options,
                    plan_id=plan_id,
                    batch_label=batch_label,
                    batch_index=batch_index,
                    layer_pair=[source, target],
                ),
                commit=False,
            )
            runs.append(run)
        await service.session.commit()
    except Exception:
        await service.session.rollback()
        raise
    return ApiResponse(
        data={
            "plan_id": plan_id,
            "batch_id": plan_id,
            "batch_label": batch_label,
            "runs": [RunView.model_validate(r).model_dump(mode="json") for r in runs],
            "excluded_ids": inventory["excluded_ids"],
            "default_policy": inventory["default_policy"],
        }
    )


async def _plan_runs(plan_id: str, tenant_id: str, project_id: str, service: Service):
    rows = list(
        await service.session.scalars(
            select(TlrRun)
            .where(TlrRun.tenant_id == tenant_id, TlrRun.project_id == project_id)
            .order_by(TlrRun.created_at, TlrRun.id)
        )
    )
    runs = [row for row in rows if (row.config or {}).get("plan_id") == plan_id]
    if not runs:
        raise AppError(ErrorCode.NOT_FOUND, "TLR 批次不存在")
    layer_order = {name: index for index, (name, _label) in enumerate(LAYERS)}
    runs.sort(
        key=lambda row: (
            (row.config or {}).get("batch_index", 10_000),
            layer_order.get(((row.config or {}).get("layer_pair") or [""])[0], 10_000),
            row.id,
        )
    )
    return runs


@router.get("/plans/{plan_id}/runs", response_model=ApiResponse[dict])
async def plan_runs(plan_id: str, tenant_id: Scope, project_id: Scope, service: Service):
    runs = await _plan_runs(plan_id, tenant_id, project_id, service)
    return ApiResponse(
        data={
            "batch_id": plan_id,
            "batch_label": (runs[0].config or {}).get("batch_label") or f"批次 {plan_id[:8]}",
            "runs": [RunView.model_validate(row).model_dump(mode="json") for row in runs],
        }
    )


@router.post("/plans/{plan_id}/resume", response_model=ApiResponse[dict])
async def resume_plan(plan_id: str, tenant_id: Scope, project_id: Scope, service: Service):
    runs = await _plan_runs(plan_id, tenant_id, project_id, service)
    attempted, failures = [], []
    for run in runs:
        recoverable = (
            run.status in {"failed", "completed"}
            and run.stage in {"classification", "completed_with_errors"}
            and (run.counts or {}).get("candidates", 0)
            > (run.counts or {}).get("classified", 0)
        )
        try:
            if run.status == "pending":
                await service.execute(run.id, tenant_id, project_id)
                attempted.append(run.id)
            elif recoverable:
                await service.resume(run.id, tenant_id, project_id)
                attempted.append(run.id)
        except AppError as exc:
            failures.append({"run_id": run.id, "message": exc.message, "code": exc.code})
    current = await _plan_runs(plan_id, tenant_id, project_id, service)
    return ApiResponse(
        data={
            "batch_id": plan_id,
            "batch_label": (current[0].config or {}).get("batch_label") or f"批次 {plan_id[:8]}",
            "attempted_run_ids": attempted,
            "failures": failures,
            "runs": [RunView.model_validate(row).model_dump(mode="json") for row in current],
        }
    )
