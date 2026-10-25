from tglib.db.models import Document, DocStatus
from tglib.engine.catalog import FILTERED_EXT, FILTERED_SIZE, Filters, decide


def test_no_match_is_pending():
    assert decide(None, None).status == DocStatus.PENDING


def test_filtered_is_skipped_even_with_match():
    match = Document(id=1, blob_id=7)
    d = decide(FILTERED_EXT, match)
    assert d.status == DocStatus.SKIPPED and d.last_error == FILTERED_EXT and d.blob_id is None


def test_match_with_blob_links_blob():
    d = decide(None, Document(id=3, blob_id=9))
    assert (d.status, d.blob_id, d.duplicate_of_id) == (DocStatus.DUPLICATE, 9, 3)


def test_match_without_blob_links_original():
    d = decide(None, Document(id=3, blob_id=None))
    assert (d.status, d.blob_id, d.duplicate_of_id) == (DocStatus.DUPLICATE, None, 3)


def test_match_that_is_itself_a_duplicate_links_root():
    d = decide(None, Document(id=5, blob_id=None, duplicate_of_id=2))
    assert d.duplicate_of_id == 2


def test_filters():
    f = Filters(exts={".pdf"}, max_size=10)
    assert f.rejection(".pdf", 10) is None
    assert f.rejection(".jpg", 1) == FILTERED_EXT
    assert f.rejection(None, 1) == FILTERED_EXT
    assert f.rejection(".pdf", 11) == FILTERED_SIZE
    assert Filters(exts={".pdf"}, max_size=0).rejection(".pdf", 10**12) is None
