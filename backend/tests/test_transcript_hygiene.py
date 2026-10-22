from services.transcript.hygiene import clean_transcript_text


def test_supported_noise_labels_are_removed_case_insensitively():
    assert clean_transcript_text(" Hallo [MUSIK] [singing] Welt [ApPlAuSe] ") == "Hallo Welt"
    assert clean_transcript_text("[music][laughter]") == ""


def test_lexical_text_and_short_utterances_are_preserved():
    assert clean_transcript_text("Don't [music] stop.") == "Don't stop."
    assert clean_transcript_text("Yes.") == "Yes."
    assert clean_transcript_text("Musik macht Spaß.") == "Musik macht Spaß."
