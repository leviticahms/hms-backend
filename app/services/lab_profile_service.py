"""
Service layer for Lab Profile screen.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab_portal import LabProfileConfig
from app.schemas.lab_profile import (
    ChangePasswordResponse,
    ConfigureLabSettingsRequest,
    ConfigureLabSettingsResponse,
    ContactInfoBlock,
    EditLabProfileRequest,
    EditLabProfileResponse,
    FacilitiesBlock,
    LabInfoBlock,
    LabProfileActionResponse,
    LabProfileMeta,
    LabProfileResponse,
    LabProfileStats,
    LabSettingsBlock,
    OperationalHoursBlock,
    ServicesBlock,
    UserProfileBlock,
)


class LabProfileService:
    def __init__(self, db: AsyncSession, hospital_id):
        self.db = db
        self.hospital_id = hospital_id

    async def get_profile(self) -> LabProfileResponse:
        row = (
            await self.db.execute(select(LabProfileConfig).where(LabProfileConfig.hospital_id == self.hospital_id))
        ).scalar_one_or_none()
        def _str_or_blank(value) -> str:
            if value is None:
                return ""
            return str(value)

        settings_blk = LabSettingsBlock(
            auto_print_reports=False,
            email_notifications=False,
            sms_notifications=False,
            report_template="",
        )
        blank = ""
        return LabProfileResponse(
            meta=LabProfileMeta(
                generated_at=datetime.now(timezone.utc),
                live_data=bool(row),
                demo_data=False,
            ),
            stats=LabProfileStats(total_tests=0, total_staff=0, equipment=0, branches=0),
            lab_information=LabInfoBlock(
                lab_id=_str_or_blank(row.lab_id) if row else blank,
                lab_name=_str_or_blank(row.lab_name) if row else blank,
                lab_type=_str_or_blank(row.lab_type) if row else blank,
                registration_number=_str_or_blank(row.registration_number) if row else blank,
                established_date=_str_or_blank(row.established_date) if row else blank,
                accreditation=_str_or_blank(row.accreditation) if row else blank,
                accreditation_number=_str_or_blank(row.accreditation_number) if row else blank,
            ),
            contact_information=ContactInfoBlock(
                address=_str_or_blank(row.address) if row else blank,
                city=_str_or_blank(row.city) if row else blank,
                state=_str_or_blank(row.state) if row else blank,
                pincode=_str_or_blank(row.pincode) if row else blank,
                phone=_str_or_blank(row.phone) if row else blank,
                emergency_phone=_str_or_blank(row.emergency_phone) if row else blank,
                email=_str_or_blank(row.email) if row else blank,
                website=_str_or_blank(row.website) if row else blank,
            ),
            facilities=FacilitiesBlock(
                total_area_sqft=0,
                departments=[],
                specialties=[],
                rooms=[],
            ),
            user_profile=UserProfileBlock(
                name=blank,
                role=blank,
                email=blank,
                phone=blank,
                department=blank,
                joined=blank,
                last_login=blank,
                status=blank,
            ),
            operational_hours=OperationalHoursBlock(
                working_hours=blank,
                weekdays=blank,
                sunday=blank,
                emergency=blank,
                home_collection=blank,
                report_delivery=blank,
            ),
            services=ServicesBlock(
                sample_types=[],
                routine_tat=blank,
                urgent_tat=blank,
                stat_tat=blank,
            ),
            settings=settings_blk,
        )

    async def edit_profile(self, payload: EditLabProfileRequest) -> EditLabProfileResponse:
        row = (await self.db.execute(select(LabProfileConfig).where(LabProfileConfig.hospital_id == self.hospital_id))).scalar_one_or_none()
        if not row:
            row = LabProfileConfig(
                hospital_id=self.hospital_id,
                lab_name=payload.lab_name,
                lab_type=payload.lab_type,
                registration_number=payload.registration_number,
                established_date=payload.established_date,
                accreditation=payload.accreditation,
                accreditation_number=payload.accreditation_number,
            )
            self.db.add(row)
        else:
            row.lab_name = payload.lab_name
            row.lab_type = payload.lab_type
            row.registration_number = payload.registration_number
            row.established_date = payload.established_date
            row.accreditation = payload.accreditation
            row.accreditation_number = payload.accreditation_number
        await self.db.commit()
        return EditLabProfileResponse(
            message="Lab profile updated successfully.",
            updated_lab_name=payload.lab_name,
        )

    async def configure_settings(self, payload: ConfigureLabSettingsRequest) -> ConfigureLabSettingsResponse:
        return ConfigureLabSettingsResponse(
            message="Lab settings updated successfully.",
            settings=LabSettingsBlock(
                auto_print_reports=payload.auto_print_reports,
                email_notifications=payload.email_notifications,
                sms_notifications=payload.sms_notifications,
                report_template=payload.report_template,
            ),
        )

    async def change_password(self) -> ChangePasswordResponse:
        return ChangePasswordResponse(message="Password change flow initiated.")

    async def utility_action(self, action: str) -> LabProfileActionResponse:
        return LabProfileActionResponse(
            message=f"{action} action completed.",
            action=action,
        )

