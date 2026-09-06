import os
from pathlib import Path

def report_pdf_font() -> tuple[str, str]:
    from importlib.resources import files
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    configured = os.getenv("REPORT_FONT_PATH")
    candidates = [
        configured,
        str(Path(__file__).resolve().parents[2] / "assets" / "fonts" / "NotoSansSC-Regular.ttf"),
        str(files("scifont").joinpath("fonts/NotoSansSC-VariableFont_wght.ttf")),
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            pdfmetrics.registerFont(TTFont("ValidationUnicode", candidate))
            return "ValidationUnicode", "embedded-truetype"
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    return "STSong-Light", "reportlab-cid-fallback"
