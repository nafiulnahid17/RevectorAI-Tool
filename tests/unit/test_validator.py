import pytest
from app.validators.svg_validator import validate_svg
from app.pipeline.export import approve
from app.core.exceptions import EngineError

ROOT = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20">{}</svg>'
PATH = '<path id="art" d="M1 1 L19 1 L19 19 L1 19 Z" fill="#512080"/>'


def test_real_paths_pass():
    result = validate_svg(ROOT.format('<g id="DETAILS">' + PATH + '</g>'))
    assert result["true_vector"]
    assert result["embedded_rasters"] == 0
    assert result["path_count"] == 1
    assert result["group_count"] == 1


def test_fake_image_only_never_true_vector():
    result = validate_svg(ROOT.format('<image href="data:image/png;base64,aGVsbG8=" width="20" height="20"/>'))
    assert not result["true_vector"]
    assert result["status"] == "INVALID_VECTOR"
    assert result["embedded_rasters"] == 1


def test_hybrid_is_factual_status_and_export_blocked():
    data = ROOT.format(PATH + '<image href="data:image/png;base64,aGVsbG8="/>')
    report = validate_svg(data)
    assert report["status"] == "HYBRID_VECTOR"
    assert not report["true_vector"]
    with pytest.raises(EngineError) as caught:
        approve(data.encode())
    assert caught.value.code == "RASTER_FOUND_IN_TRUE_VECTOR"


@pytest.mark.parametrize("content", [
    '<script>alert(1)</script>', '<path onload="run()" d="M0 0L10 10"/>',
    '<foreignObject/>', '<image href="https://example.org/art.jpg"/>',
    '<path d="M0 0L10 10" fill="url(https://example.org/x.png)"/>',
    '<path d="M0 0L10 10" style="fill:uRl(data:image/png;base64,aA==)"/>',
    '<style>.a{fill:url(da\\74 a:image/png;base64,aA==)}</style>',
    '<path d="M0 0L10 10" style="fill:url(%64ata:image/webp;base64,aA==)"/>',
    '<path d="M0 0L10 10" fill="url(#missing)"/>',
    '<path id="a" d="M0 0L10 10"/><path id="a" d="M1 1L11 11"/>',
    '<path d="M0 0 BAD"/>', '<path d="M0 0L2"/>',
    '<path d="M0 0L10 10" transform="rotate(broken)"/>',
    '<defs><g id="a"><use href="#b"/></g><g id="b"><use href="#a"/></g></defs><use href="#a"/>',
    '<defs><linearGradient id="a"/></defs>' + PATH,
])
def test_unsafe_or_broken_svg_rejected(content):
    assert not validate_svg(ROOT.format(content))["valid_svg"]


@pytest.mark.parametrize("resource", ["x.png", "x.jpg", "x.jpeg", "x.webp", "data:image/jpeg;base64,aA=="])
def test_css_and_uri_raster_detector(resource):
    report = validate_svg(ROOT.format(PATH + '<rect width="10" height="10" style="fill:url(' + resource + ')"/>'))
    assert not report["true_vector"]
    assert report["embedded_rasters"] > 0


def test_xxe_and_defs_only():
    assert not validate_svg('<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>' + ROOT.format("&x;"))["valid_svg"]
    assert not validate_svg(ROOT.format('<defs>' + PATH + '</defs>'))["true_vector"]
    assert not validate_svg(ROOT.format('<g display="none">' + PATH + '</g>'))["true_vector"]


def test_viewbox_required_and_bounded():
    assert not validate_svg('<svg xmlns="http://www.w3.org/2000/svg">' + PATH + '</svg>')["valid_svg"]
    assert not validate_svg(ROOT.format(PATH).replace('0 0 20 20', '0 0 99999999 99999999'))["valid_svg"]


@pytest.mark.parametrize("shape", [
    '<rect width="0" height="10"/>', '<circle r="0"/>',
    '<path d="M0 0L0 0"/>', '<path d="M0 0L10 10" style="fill:none;stroke:none"/>',
])
def test_non_useful_geometry_never_true_vector(shape):
    assert not validate_svg(ROOT.format(shape))["true_vector"]


@pytest.mark.parametrize("d", ["M0 0L1 2 3", "M0 0Z 2 3", "M0 0A-1 2 0 0 1 10 10", "M0 0A1 2 0 2 1 10 10"])
def test_invalid_command_arity_and_arc_flags(d):
    assert not validate_svg(ROOT.format(f'<path d="{d}"/>'))["valid_svg"]

@pytest.mark.parametrize('d',['M0 0 L0 0','M1 1 C1 1 1 1 1 1'])
def test_degenerate_path_cannot_hide_beside_real_artwork(d):
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M0 0 L10 0 L10 10 Z"/><path d="{d}"/></svg>'
    report=validate_svg(svg)
    assert not report['true_vector'] and report['degenerate_object_count']==1
    assert 'Invalid path geometry' in report['errors']
