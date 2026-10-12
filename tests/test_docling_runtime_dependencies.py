def test_docling_docx_equation_encoder_is_installed():
    from pylatexenc.latexencode import UnicodeToLatexEncoder

    assert UnicodeToLatexEncoder().unicode_to_latex("α") == r"\ensuremath{\alpha}"
