from fastapi.templating import Jinja2Templates
from app.core.i18n import TRANSLATIONS
from datetime import datetime
from weasyprint import HTML
import io

templates = Jinja2Templates(directory="app/templates")
templates.env.globals['translations'] = TRANSLATIONS

def generate_student_pdf_bytes(report, lang: str = "en") -> bytes:
    """
    Generates a PDF byte string for the student's grade report.
    Takes a report dictionary (calculated data), not a session, to ensure thread safety.
    """
    if not report:
        raise ValueError(f"No report data provided.")

    # Render HTML
    template = templates.get_template("student_score_pdf.html")
    html_content = template.render({
        "report": report,
        "lang": lang,
        "translations": TRANSLATIONS,
        "now_utc": datetime.utcnow()
    })

    # Generate PDF
    # Use weasyprint
    pdf_file = io.BytesIO()
    HTML(string=html_content).write_pdf(target=pdf_file)

    pdf_bytes = pdf_file.getvalue()
    pdf_file.close()

    return pdf_bytes
