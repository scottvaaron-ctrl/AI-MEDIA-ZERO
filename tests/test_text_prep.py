from __future__ import annotations

from aimz.providers.tts.text_prep import apply_pronunciations, clean_for_tts, split_sentences


def test_split_sentences_caps_chunk_length() -> None:
    text = "One. Two is a longer sentence with many words inside it, and it keeps going, and going, past the limit. Three!"
    chunks = split_sentences(text, max_words=8)
    assert chunks[0] == "One."
    assert all(len(c.split()) <= 16 for c in chunks)
    assert chunks[-1] == "Three!"


def test_pronunciations_whole_word_only() -> None:
    out = apply_pronunciations("NASA and NASAL and GitHub.", {"NASA": "Nassa", "GitHub": "Git Hub"})
    assert out == "Nassa and NASAL and Git Hub."


def test_clean_for_tts_strips_markup() -> None:
    assert clean_for_tts("**Bold** [note] “quoted” text") == 'Bold "quoted" text'
