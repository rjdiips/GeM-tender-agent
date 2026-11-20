import pytesseract

from gem_agent.tools.pdf.ocr import configure_tesseract


def test_configure_tesseract_points_pytesseract_at_the_binary():
    before = pytesseract.pytesseract.tesseract_cmd
    try:
        configure_tesseract(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        assert pytesseract.pytesseract.tesseract_cmd == r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    finally:
        pytesseract.pytesseract.tesseract_cmd = before


def test_an_empty_path_leaves_tesseract_alone():
    before = pytesseract.pytesseract.tesseract_cmd
    configure_tesseract("")
    assert pytesseract.pytesseract.tesseract_cmd == before
