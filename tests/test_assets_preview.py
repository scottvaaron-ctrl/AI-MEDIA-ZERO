"""Preview URLs and ``preview()`` on every asset provider (used to score pictures before downloading)."""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest
from PIL import Image

from aimz.domain.models import AssetCandidate
from aimz.providers.assets.owner_library import OwnerLibraryAssetProvider
from aimz.providers.assets.stock import PexelsAssetProvider, PixabayAssetProvider
from aimz.providers.assets.wikimedia import WikimediaAssetProvider, preview_url_for

THUMB = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/X.jpg/1280px-X.jpg"


def _png_bytes(size: tuple[int, int] = (8, 6)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _client(handler) -> httpx.Client:  # noqa: ANN001
    return httpx.Client(transport=httpx.MockTransport(handler))


def _cand(url: str, **kw) -> AssetCandidate:  # noqa: ANN003
    return AssetCandidate(provider="p", title="t", page_url="", file_url=url, license="x", **kw)


def _wiki(handler) -> WikimediaAssetProvider:  # noqa: ANN001
    prov = WikimediaAssetProvider("ua")
    prov._client = _client(handler)
    return prov


def test_wikimedia_preview_url() -> None:
    # 320 is not a width the thumbnail server serves (HTTP 400); it snaps up to 330.
    assert preview_url_for(THUMB) == THUMB.replace("1280px-", "330px-")
    assert preview_url_for(THUMB, 200).endswith("/250px-X.jpg")
    plain = "https://upload.wikimedia.org/wikipedia/commons/a/ab/X.jpg"
    assert preview_url_for(plain) == plain


@pytest.mark.parametrize(
    ("requested", "served"),
    [
        (1, 20),
        (20, 20),
        (21, 40),
        (100, 120),
        (250, 250),
        (320, 330),
        (330, 330),
        (331, 500),
        (960, 960),
        (1280, 1280),
        (1281, 1920),
        (3840, 3840),
        (4000, 3840),
    ],
)
def test_wikimedia_standard_width(requested: int, served: int) -> None:
    from aimz.providers.assets.wikimedia import standard_width

    assert standard_width(requested) == served


def test_wikimedia_preview_url_keeps_query_string() -> None:
    url = (
        "https://thumb.wikimedia.org/wikipedia/commons/thumb/5/5d/X.jpg/1280px-X.jpg"
        "?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=thumbnail"
    )
    assert preview_url_for(url, 320) == url.replace("/1280px-", "/330px-")


def test_wikimedia_search_sets_preview_url(svc) -> None:  # noqa: ANN001
    payload = {
        "query": {
            "pages": {
                "1": {
                    "title": "File:X.jpg",
                    "imageinfo": [
                        {
                            "thumburl": THUMB,
                            "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/X.jpg",
                            "descriptionurl": "https://commons.wikimedia.org/wiki/File:X.jpg",
                            "mime": "image/jpeg",
                            "width": 2000,
                            "height": 1500,
                            "extmetadata": {"LicenseShortName": {"value": "CC BY 4.0"}},
                        }
                    ],
                }
            }
        }
    }
    prov = _wiki(lambda req: httpx.Response(200, json=payload))
    found = prov.search(svc.ctx(), "x")
    assert found[0].file_url == THUMB
    assert found[0].preview_url == THUMB.replace("1280px-", "330px-")


def test_pexels_preview_url_small(svc) -> None:  # noqa: ANN001
    data = {
        "photos": [
            {
                "id": 1,
                "url": "https://pexels.com/p/1",
                "photographer": "Ann",
                "width": 100,
                "height": 200,
                "src": {"large2x": "https://i/1-large2x.jpg", "small": "https://i/1-small.jpg"},
            }
        ]
    }
    pex = PexelsAssetProvider("KEY", "ua", _client(lambda req: httpx.Response(200, json=data)))
    c = pex.search(svc.ctx(), "moon")[0]
    assert c.preview_url == "https://i/1-small.jpg" and c.file_url == "https://i/1-large2x.jpg"


def test_pixabay_preview_url_webformat(svc) -> None:  # noqa: ANN001
    hit = {
        "id": 1,
        "pageURL": "https://pixabay.com/p/1",
        "user": "Bo",
        "tags": "moon",
        "largeImageURL": "https://i/large.jpg",
        "webformatURL": "https://i/web.jpg",
        "previewURL": "https://i/prev.jpg",
    }
    only_prev = {**hit, "id": 2, "webformatURL": ""}
    pix = PixabayAssetProvider(
        "KEY", "ua", _client(lambda req: httpx.Response(200, json={"hits": [hit, only_prev]}))
    )
    got = pix.search(svc.ctx(), "moon")
    assert got[0].preview_url == "https://i/web.jpg"
    assert got[1].preview_url == "https://i/prev.jpg"


def test_preview_returns_image(svc) -> None:  # noqa: ANN001
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        return httpx.Response(200, content=_png_bytes())

    img = _wiki(handler).preview(svc.ctx(), _cand(THUMB), width=320)
    assert isinstance(img, Image.Image) and img.size == (8, 6)
    assert seen == [THUMB.replace("1280px-", "330px-")]  # 320 snaps to a served width
    img.getpixel((0, 0))  # fully loaded: usable with no open file

    pex = PexelsAssetProvider("KEY", "ua", _client(lambda req: httpx.Response(200, content=_png_bytes())))
    got = pex.preview(
        svc.ctx(), _cand("https://i/full.jpg", preview_url="https://i/small.jpg", mime="image/jpeg")
    )
    assert got is not None and got.size == (8, 6)


def test_preview_bad_bytes_returns_none(svc) -> None:  # noqa: ANN001
    prov = _wiki(lambda req: httpx.Response(200, content=b"not an image"))
    assert prov.preview(svc.ctx(), _cand(THUMB)) is None


def test_preview_http_error_returns_none(svc) -> None:  # noqa: ANN001
    prov = _wiki(lambda req: httpx.Response(500))
    assert prov.preview(svc.ctx(), _cand(THUMB)) is None


def test_preview_transport_error_returns_none(svc) -> None:  # noqa: ANN001
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert _wiki(boom).preview(svc.ctx(), _cand(THUMB)) is None
    assert _wiki(boom).preview(svc.ctx(), _cand("")) is None


def test_stock_video_candidate_has_no_preview(svc) -> None:  # noqa: ANN001
    pex = PexelsAssetProvider("KEY", "ua", _client(lambda req: httpx.Response(200, content=_png_bytes())))
    assert pex.preview(svc.ctx(), _cand("https://i/v.mp4", mime="video/mp4")) is None


def test_preview_writes_no_files_or_asset_rows(svc, tmp_path: Path) -> None:  # noqa: ANN001
    before = set(tmp_path.rglob("*"))
    rows = svc.db.query("SELECT COUNT(*) AS n FROM assets")[0]["n"]
    _wiki(lambda req: httpx.Response(200, content=_png_bytes())).preview(svc.ctx(), _cand(THUMB))
    assert svc.db.query("SELECT COUNT(*) AS n FROM assets")[0]["n"] == rows
    assert {p for p in tmp_path.rglob("*") if not p.name.startswith("test.sqlite3")} == {
        p for p in before if not p.name.startswith("test.sqlite3")
    }


def test_owner_library_preview_reads_file(svc, tmp_path: Path) -> None:  # noqa: ANN001
    lib = tmp_path / "lib"
    lib.mkdir()
    f = lib / "moon.png"
    Image.new("RGB", (640, 400), (1, 2, 3)).save(f)
    prov = OwnerLibraryAssetProvider(lib)
    cand = prov.search(svc.ctx(), "moon")[0]
    img = prov.preview(svc.ctx(), cand, width=320)
    assert img is not None and img.width == 320 and img.height == 200
    assert prov.preview(svc.ctx(), _cand(str(lib / "missing.png"))) is None
    (lib / "bad.png").write_bytes(b"nope")
    assert prov.preview(svc.ctx(), _cand(str(lib / "bad.png"))) is None
