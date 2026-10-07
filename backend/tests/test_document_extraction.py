import app as app_module


def test_clean_doc_text_preserves_crlf_paragraph_boundaries():
    text = "First line\r\ncontinues.\r\n\r\nSecond paragraph."

    assert app_module._clean_doc_text(text) == (
        "First line continues.\nSecond paragraph."
    )


def test_clean_doc_text_dehyphenates_crlf_line_breaks():
    assert app_module._clean_doc_text("An exam-\r\nple sentence.") == (
        "An example sentence."
    )
