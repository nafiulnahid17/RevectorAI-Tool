"""Build the production image with an ephemeral CA mount on managed TLS proxies.

Railway builds the plain root Dockerfile. This helper only modifies the pip RUN
instruction in a temporary Dockerfile; the CA never enters an image layer.
"""
from argparse import ArgumentParser
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--proxy-ca', type=Path, required=True,
                        help='Combined trusted CA bundle for the local network proxy')
    parser.add_argument('--tag', default='revector-engine:local')
    args = parser.parse_args()
    certificate = args.proxy_ca.resolve(strict=True)
    if not certificate.is_file():
        parser.error('--proxy-ca must refer to a file')
    root = Path(__file__).resolve().parents[1]
    content = (root / 'Dockerfile').read_text()
    anchor = 'RUN python -m pip install --no-cache-dir'
    if content.count(anchor) != 1:
        parser.error('Dockerfile pip instruction changed; update the CA build helper')
    content = '# syntax=docker/dockerfile:1\n' + content.replace(
        anchor,
        'RUN --mount=type=secret,id=proxy_ca,required=true '
        'PIP_CERT=/run/secrets/proxy_ca python -m pip install --no-cache-dir',
    )
    with TemporaryDirectory(prefix='revector-docker-') as directory:
        dockerfile = Path(directory) / 'Dockerfile'
        dockerfile.write_text(content)
        subprocess.run([
            'docker', '--host=unix:///var/run/docker.sock', 'build',
            '--file', str(dockerfile), '--secret', f'id=proxy_ca,src={certificate}',
            '--tag', args.tag, str(root),
        ], check=True)


if __name__ == '__main__':
    main()
