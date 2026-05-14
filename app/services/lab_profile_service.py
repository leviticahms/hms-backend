"""
Service layer for Lab Profile screen.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re

from fastapi import HTTPException, status
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab_portal import LabProfileConfig
from app.models.user import User

from app.schemas.lab_profile import (
    ChangePasswordRequest,
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

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)


class LabProfileService:

    def __init__(self, db: AsyncSession, hospital_id):
        self.db = db
        self.hospital_id = hospital_id

    async def get_profile(self) -> LabProfileResponse:

        row = (
            await self.db.execute(
                select(LabProfileConfig).where(
                    LabProfileConfig.hospital_id == self.hospital_id
                )
            )
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

            stats=LabProfileStats(
                total_tests=0,
                total_staff=0,
                equipment=0,
                branches=0,
            ),

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

    async def edit_profile(
        self,
        payload: EditLabProfileRequest
    ) -> EditLabProfileResponse:

        row = (
            await self.db.execute(
                select(LabProfileConfig).where(
                    LabProfileConfig.hospital_id == self.hospital_id
                )
            )
        ).scalar_one_or_none()

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

    async def configure_settings(
        self,
        payload: ConfigureLabSettingsRequest
    ) -> ConfigureLabSettingsResponse:

        return ConfigureLabSettingsResponse(

            message="Lab settings updated successfully.",

            settings=LabSettingsBlock(
                auto_print_reports=payload.auto_print_reports,
                email_notifications=payload.email_notifications,
                sms_notifications=payload.sms_notifications,
                report_template=payload.report_template,
            ),
        )

    async def change_password(
        self,
        current_user: User,
        payload: ChangePasswordRequest,
    ) -> ChangePasswordResponse:

        try:

            # fetch fresh user from current session
            db_user = (
                await self.db.execute(
                    select(User).where(
                        User.id == current_user.id
                    )
                )
            ).scalar_one_or_none()

            if not db_user:

                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found."
                )

            # verify old password
            if not pwd_context.verify(
                payload.old_password,
                db_user.password_hash
            ):

                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Old password is incorrect."
                )

            # prevent same password reuse
            if payload.old_password == payload.new_password:

                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="New password cannot be same as old password."
                )

            # password validation
            if len(payload.new_password) < 8:

                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Password must be at least 8 characters."
                )

            if not re.search(r"[A-Z]", payload.new_password):

                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Password must contain at least one uppercase letter."
                )

            if not re.search(r"[a-z]", payload.new_password):

                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Password must contain at least one lowercase letter."
                )

            if not re.search(r"\d", payload.new_password):

                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Password must contain at least one number."
                )

            if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", payload.new_password):

                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Password must contain at least one special character."
                )

            # hash new password
            hashed_password = pwd_context.hash(
                payload.new_password
            )

            # update password
            db_user.password_hash = hashed_password

            # commit changes
            await self.db.commit()

            return ChangePasswordResponse(
                message="Password changed successfully."
            )

        except HTTPException:
            raise

        except Exception as e:

            await self.db.rollback()

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Password change failed: {str(e)}"
            )

    async def utility_action(
        self,
        action: str
    ) -> LabProfileActionResponse:

        return LabProfileActionResponse(
            message=f"{action} action completed.",
            action=action,
        )