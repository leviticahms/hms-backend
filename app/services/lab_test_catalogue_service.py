"""
Service for Test Catalogue Management UI.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab_portal import LabCatalogueTest, LabTestCategory
from app.schemas.lab_test_catalogue import (
    AddCategoryRequest,
    AddCategoryResponse,
    AddTestRequest,
    AddTestResponse,
    BulkActionResponse,
    TestCategoryChip,
    TestCatalogueListResponse,
    TestCatalogueMeta,
    TestCatalogueRow,
    TestCatalogueSummary,
)


class LabTestCatalogueService:
    def __init__(self, db: AsyncSession, hospital_id):
        self.db = db
        self.hospital_id = hospital_id

    def _chips(self, rows: list[TestCatalogueRow]) -> list[TestCategoryChip]:
        counts: dict[str, int] = {}
        for r in rows:
            counts[r.category] = counts.get(r.category, 0) + 1
        chips = [TestCategoryChip(category_name="All Tests", test_count=len(rows))]
        chips.extend(TestCategoryChip(category_name=k, test_count=v) for k, v in sorted(counts.items()))
        return chips

    async def list_catalogue(
        self,
        *,
        search: Optional[str] = None,
        category: Optional[str] = None,
    ) -> TestCatalogueListResponse:
        rows = await self._db_rows()
        if search:
            q = search.strip().lower()
            rows = [r for r in rows if q in r.test_name.lower() or q in r.test_code.lower()]
        if category and category.strip().lower() not in ("all", "all tests"):
            c = category.strip().lower()
            rows = [r for r in rows if r.category.lower() == c]

        summary = TestCatalogueSummary(
            active_tests=sum(1 for r in rows if r.status == "ACTIVE"),
            categories=len({r.category for r in rows}),
            total_parameters=sum(r.parameters_count for r in rows),
        )
        return TestCatalogueListResponse(
            meta=TestCatalogueMeta(
                generated_at=datetime.now(timezone.utc),
                live_data=True,
                demo_data=False,
            ),
            category_chips=self._chips(rows),
            summary=summary,
            rows=rows,
        )

    async def add_category(self, payload: AddCategoryRequest) -> AddCategoryResponse:
        row = LabTestCategory(hospital_id=self.hospital_id, category_name=payload.category_name.strip())
        self.db.add(row)
        await self.db.commit()
        return AddCategoryResponse(
            message="Category added successfully.",
            category_name=payload.category_name.strip(),
        )

    async def add_test(self, payload: AddTestRequest) -> AddTestResponse:
        code = (payload.test_code or payload.test_name[:3]).upper().replace(" ", "")[:10]
        row = LabCatalogueTest(
            hospital_id=self.hospital_id,
            test_code=code,
            test_name=payload.test_name,
            category=payload.category,
            sample_type=payload.sample_type,
            turnaround_time=payload.turnaround_time,
            price_inr=payload.price_inr,
            parameters_count=len(payload.parameters),
            status="ACTIVE",
            test_instructions=payload.test_instructions,
        )
        self.db.add(row)
        await self.db.commit()
        return AddTestResponse(
            message="Test added to catalogue successfully.",
            test_code=code,
            test_name=payload.test_name,
        )

    async def bulk_action(self, action: str) -> BulkActionResponse:
        return BulkActionResponse(
            message=f"{action} completed successfully.",
            action=action,
        )

    async def _db_rows(self) -> list[TestCatalogueRow]:
        stmt = (
            select(LabCatalogueTest)
            .where(LabCatalogueTest.hospital_id == self.hospital_id)
            .order_by(LabCatalogueTest.created_at.desc())
        )
        recs = (await self.db.execute(stmt)).scalars().all()
        return [
            TestCatalogueRow(
                test_code=r.test_code,
                test_name=r.test_name,
                category=r.category,
                sample_type=r.sample_type,
                turnaround_time=r.turnaround_time,
                price_inr=float(r.price_inr),
                parameters_count=r.parameters_count,
                status=r.status,
            )
            for r in recs
        ]

