"""Request bodies for billing / finance document endpoints."""

from pydantic import BaseModel, ConfigDict


class FinancialDocumentEmailBody(BaseModel):
    to_email: str

    model_config = ConfigDict(
        json_schema_extra={"example": {"to_email": "patient@example.com"}}
    )
