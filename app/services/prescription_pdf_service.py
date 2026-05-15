"""
Generate prescription PDF for patient download (ReportLab).
Includes hospital info, doctor, patient, date, prescription number, medicines with directions.
"""
from io import BytesIO
from typing import List, Dict, Any, Optional

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer


def _escape(s: str) -> str:
    if not s:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _hospital_line(h: Dict[str, Any]) -> str:
    parts = [_escape(h.get("name") or "Hospital")]
    if h.get("address"):
        parts.append(_escape(h["address"]))
    if h.get("city") or h.get("state"):
        parts.append(_escape(", ".join(filter(None, [h.get("city"), h.get("state"), h.get("pincode")]))))
    if h.get("phone"):
        parts.append(f"Ph: {_escape(h['phone'])}")
    if h.get("email"):
        parts.append(_escape(h["email"]))
    return "<br/>".join(parts)


def _timing_slots_label(med: Dict[str, Any]) -> str:
    timing = med.get("timing")
    if not isinstance(timing, dict):
        timing = {}
    parts: List[str] = []
    if timing.get("morning"):
        parts.append("Morning")
    if timing.get("afternoon"):
        parts.append("Afternoon")
    if timing.get("night"):
        parts.append("Night")
    for t in timing.get("times") or []:
        if t:
            parts.append(_escape(str(t)))
    return ", ".join(parts) if parts else ""


def _medication_line(med: Dict[str, Any]) -> str:
    name = med.get("generic_name") or med.get("brand_name") or "Medicine"
    if med.get("strength"):
        name = f"{_escape(name)} {_escape(str(med['strength']))}"
    else:
        name = _escape(name)
    lines = [name]
    dosage = _escape(med.get("dosage_text") or med.get("dosage") or "")
    freq = _escape(med.get("frequency") or "")
    duration = med.get("duration_days")
    if duration:
        duration_str = f"{duration} days"
    else:
        duration_str = _escape(med.get("duration") or "")
    if dosage or freq or duration_str:
        lines.append(f"  Dose: {dosage} &nbsp;|&nbsp; {freq} &nbsp;|&nbsp; {duration_str}".strip(" |"))
    when = _timing_slots_label(med)
    if when:
        lines.append(f"  When: {when}")
    if med.get("before_food"):
        lines.append("  Before meals (before lunch)")
    if med.get("after_food"):
        lines.append("  After meals (after lunch)")
    if med.get("route") and str(med.get("route")).upper() != "ORAL":
        lines.append(f"  Route: {_escape(str(med['route']))}")
    if med.get("instructions"):
        lines.append(f"  {_escape(med['instructions'])}")
    if med.get("quantity"):
        lines.append(f"  Quantity: {med['quantity']}")
    return "<br/>".join(lines)


def generate_prescription_pdf(
    hospital: Dict[str, Any],
    doctor_name: str,
    patient_name: str,
    patient_ref: Optional[str],
    prescription_number: str,
    prescription_id: str,
    prescription_date: str,
    diagnosis: Optional[str],
    medications: List[Dict[str, Any]],
    general_instructions: Optional[str] = None,
    diet_instructions: Optional[str] = None,
    follow_up_date: Optional[str] = None,
) -> bytes:
    """Build prescription PDF and return as bytes."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "PrescriptionTitle",
        parent=styles["Heading1"],
        fontSize=18,
        spaceAfter=12,
        alignment=1,
    )
    heading_style = ParagraphStyle(
        "PrescriptionHeading",
        parent=styles["Heading2"],
        fontSize=12,
        spaceAfter=6,
    )
    body_style = styles["Normal"]

    elements = []

    # Hospital
    elements.append(Paragraph(_hospital_line(hospital), body_style))
    elements.append(Spacer(1, 0.2 * inch))

    # Title
    elements.append(Paragraph("PRESCRIPTION", title_style))
    elements.append(Spacer(1, 0.15 * inch))

    # Prescription number & date
    elements.append(
        Paragraph(
            f"Prescription No: {_escape(prescription_number)} &nbsp;&nbsp; Date: {_escape(prescription_date)}",
            body_style,
        )
    )
    elements.append(Spacer(1, 0.1 * inch))

    # Patient & Doctor
    elements.append(Paragraph(f"<b>Patient:</b> {_escape(patient_name)}", body_style))
    if patient_ref:
        elements.append(Paragraph(f"<b>Patient ID:</b> {_escape(patient_ref)}", body_style))
    elements.append(Paragraph(f"<b>Doctor:</b> {_escape(doctor_name)}", body_style))
    elements.append(Spacer(1, 0.15 * inch))

    # Diagnosis
    if diagnosis:
        elements.append(Paragraph("<b>Diagnosis:</b>", heading_style))
        elements.append(Paragraph(_escape(diagnosis), body_style))
        elements.append(Spacer(1, 0.1 * inch))

    # Medicines
    elements.append(Paragraph("<b>Medicines &amp; Directions</b>", heading_style))
    for i, med in enumerate(medications or [], 1):
        elements.append(Paragraph(f"{i}. {_medication_line(med)}", body_style))
        elements.append(Spacer(1, 0.08 * inch))
    elements.append(Spacer(1, 0.1 * inch))

    # Instructions
    if general_instructions:
        elements.append(Paragraph("<b>General instructions:</b>", heading_style))
        elements.append(Paragraph(_escape(general_instructions), body_style))
        elements.append(Spacer(1, 0.08 * inch))
    if diet_instructions:
        elements.append(Paragraph("<b>Diet instructions:</b>", heading_style))
        elements.append(Paragraph(_escape(diet_instructions), body_style))
        elements.append(Spacer(1, 0.08 * inch))
    if follow_up_date:
        elements.append(Paragraph(f"<b>Follow-up:</b> {_escape(follow_up_date)}", body_style))

    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
