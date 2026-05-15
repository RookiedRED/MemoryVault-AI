"""
Tests for app/vault/chunker.py — text chunking logic.
No external dependencies required.
"""

from app.vault.chunker import CHUNK_SIZE, OVERLAP, chunk_text


def test_empty_text_returns_no_chunks():
    assert chunk_text("") == []


def test_short_text_single_chunk():
    result = chunk_text("Hello world")
    assert len(result) == 1
    text, start, end = result[0]
    assert text == "Hello world"
    assert start == 0


def test_chunk_returns_tuples():
    result = chunk_text("abc def ghi")
    assert all(len(c) == 3 for c in result)


def test_long_text_produces_multiple_chunks():
    # Build a text longer than CHUNK_SIZE
    text = ("word " * 500).strip()  # ~2500 chars
    result = chunk_text(text)
    assert len(result) >= 2


def test_chunks_cover_full_text():
    text = ("alpha beta gamma delta epsilon " * 200).strip()
    result = chunk_text(text)
    # Every word in the original text should appear in at least one chunk
    words_in_chunks = set()
    for chunk, _, _ in result:
        words_in_chunks.update(chunk.split())
    for word in text.split():
        assert word in words_in_chunks


def test_offsets_are_non_negative():
    text = "a " * 1000
    for _, start, end in chunk_text(text):
        assert start >= 0
        assert end > start


def test_overlap_means_adjacent_chunks_share_content():
    text = ("word " * 600).strip()
    result = chunk_text(text)
    assert len(result) >= 2
    # The tail of chunk N and the head of chunk N+1 should share at least one word
    for i in range(len(result) - 1):
        tail_words = set(result[i][0].split()[-20:])
        head_words = set(result[i + 1][0].split()[:20])
        assert tail_words & head_words, "Adjacent chunks share no words — overlap may be broken"


def test_custom_chunk_size():
    text = "a " * 200  # 400 chars
    result = chunk_text(text, chunk_size=100, overlap=10)
    assert len(result) >= 3
    for chunk, _, _ in result:
        assert len(chunk) <= 110  # allows a small word-boundary overshoot


def test_no_empty_chunks():
    text = "hello world " * 300
    for chunk, _, _ in chunk_text(text):
        assert chunk.strip() != ""
