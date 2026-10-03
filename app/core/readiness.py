"""Independent, measured readiness checks for the server, engine and web tool."""
import logging
from tempfile import NamedTemporaryFile
from app.core.config import capabilities
from app.validators.svg_validator import validate_svg
from app.validators.visual_diff import render_svg
from app.core.security import configured

PROBE_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16"><g id="probe"><path d="M0 0H16V16H0Z" fill="#6d3dee"/></g></svg>'


def readiness(app) -> dict:
    dependencies = capabilities()
    checks = {}
    checks['engine_initialized'] = bool(getattr(app.state, 'runtime_ready', False))
    checks['opencv'] = dependencies['opencv']
    checks['renderer'] = dependencies['resvg']
    try:
        report = validate_svg(PROBE_SVG)
        checks['vector_validator'] = report['true_vector'] and report['embedded_rasters'] == 0
        image = render_svg(PROBE_SVG)
        checks['vector_render'] = image.size == (16, 16) and image.getpixel((8, 8))[3] == 255
        image.close()
    except Exception:
        checks['vector_validator'] = checks.get('vector_validator', False)
        checks['vector_render'] = False
        logging.getLogger('revector').warning('Readiness SVG self-check failed')
    engine_ok = all(checks.values())
    try:
        # Actually write and read a temporary file in the mounted data volume.
        with NamedTemporaryFile(dir=app.state.engine.storage.root) as probe:
            probe.write(b'ReVector readiness')
            probe.flush()
            probe.seek(0)
            storage_ok = probe.read() == b'ReVector readiness'
    except (AttributeError, OSError):
        storage_ok = False
    tool_checks = {'storage': storage_ok, 'gateway_auth': configured(app.state.engine.settings) or app.state.engine.settings.allow_unauthenticated,
                   'queue': hasattr(app.state, 'queue') and checks['engine_initialized'],
                   'vector_conversion': dependencies['inkscape']}
    tool_ok = engine_ok and all(tool_checks.values())
    return {'status': 'ready' if engine_ok and tool_ok else 'not_ready', 'engine': 'ReVector',
            'segments': {'server': {'status': 'connected'},
                         'engine': {'status': 'connected' if engine_ok else 'failed', 'checks': checks},
                         'tool': {'status': 'connected' if tool_ok else 'failed', 'checks': tool_checks}},
            'dependencies': dependencies, 'ai_required': False}
