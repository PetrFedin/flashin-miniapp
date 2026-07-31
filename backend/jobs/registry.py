from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from sqlalchemy.orm import Session

from .campaign_jobs import queue_due_campaigns
from .event_jobs import run_event_dispatcher
from .media_jobs import process_media_jobs, queue_missing_media_jobs
from .moysklad_jobs import execute_moysklad_workflow
from .ops_jobs import create_inventory_snapshot, queue_abandoned_cart_notifications
from .outbox_jobs import process_outbox
from .refund_jobs import reconcile_pending_refunds
from .sla_jobs import mark_overdue_sla
from ..services.crm import recompute_all_profiles
from ..services.moysklad import sync_assortment_to_catalog
from ..services.recommendations import rebuild_basic_recommendations

SyncJob = Callable[[Session], object]
AsyncJob = Callable[[Session], Awaitable[object]]
JobKind = Literal["sync", "async"]


@dataclass(frozen=True)
class JobDefinition:
    name: str
    title: str
    description: str
    permission: str
    kind: JobKind
    function: SyncJob | AsyncJob
    manual_enabled: bool = True
    retry_enabled: bool = True


def run_media_pipeline(db: Session) -> dict[str, int]:
    return {
        "queued": queue_missing_media_jobs(db),
        "processed": process_media_jobs(db),
    }


async def run_moysklad_pipeline(db: Session) -> object:
    return await execute_moysklad_workflow(
        db,
        sync_function=sync_assortment_to_catalog,
        profile_function=recompute_all_profiles,
        recommendation_function=rebuild_basic_recommendations,
    )


_DEFINITIONS = (
    JobDefinition(
        name="campaigns",
        title="Отложенные кампании",
        description="Ставит в очередь наступившие маркетинговые кампании без повторной отправки клиенту.",
        permission="support.write",
        kind="sync",
        function=queue_due_campaigns,
    ),
    JobDefinition(
        name="events",
        title="Бизнес-события",
        description="Переносит внутренние бизнес-события в защищённый webhook outbox.",
        permission="webhooks.write",
        kind="sync",
        function=run_event_dispatcher,
    ),
    JobDefinition(
        name="abandoned-carts",
        title="Брошенные корзины",
        description="Создаёт дедуплицированные уведомления о брошенных корзинах.",
        permission="notifications.retry",
        kind="sync",
        function=queue_abandoned_cart_notifications,
    ),
    JobDefinition(
        name="inventory-snapshot",
        title="Снимок остатков",
        description="Фиксирует текущие остатки и резерв для последующей сверки.",
        permission="inventory.write",
        kind="sync",
        function=create_inventory_snapshot,
    ),
    JobDefinition(
        name="sla",
        title="Контроль SLA",
        description="Отмечает просроченные SLA и создаёт операционные уведомления.",
        permission="support.write",
        kind="sync",
        function=mark_overdue_sla,
    ),
    JobDefinition(
        name="outbox",
        title="Webhook outbox",
        description="Доставляет ожидающие webhook-события с защищёнными повторными попытками.",
        permission="webhooks.write",
        kind="async",
        function=process_outbox,
    ),
    JobDefinition(
        name="refund-reconciliation",
        title="Сверка возвратов",
        description="Сверяет ожидающие возвраты с платёжным провайдером.",
        permission="orders.write",
        kind="async",
        function=reconcile_pending_refunds,
    ),
    JobDefinition(
        name="moysklad-sync",
        title="Синхронизация МойСклад",
        description="Атомарно синхронизирует каталог, CRM-профили и рекомендации.",
        permission="products.write",
        kind="async",
        function=run_moysklad_pipeline,
    ),
    JobDefinition(
        name="media-jobs",
        title="Обработка медиа",
        description="Создаёт недостающие media-задачи и формирует производные изображения.",
        permission="media.write",
        kind="sync",
        function=run_media_pipeline,
    ),
)

JOB_REGISTRY: dict[str, JobDefinition] = {item.name: item for item in _DEFINITIONS}

if len(JOB_REGISTRY) != len(_DEFINITIONS):
    raise RuntimeError("Scheduled job registry contains duplicate names")


def get_job_definition(name: str) -> JobDefinition | None:
    return JOB_REGISTRY.get(str(name or "").strip().lower())


def list_job_definitions() -> tuple[JobDefinition, ...]:
    return tuple(JOB_REGISTRY[name] for name in sorted(JOB_REGISTRY))
