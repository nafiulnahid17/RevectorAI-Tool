"""Portable container launcher: initialize the volume, drop root, honor PORT."""
import os
from pathlib import Path
import pwd


def main() -> None:
    port = int(os.environ.get('PORT', '8000'))
    if not 1 <= port <= 65535:
        raise ValueError('PORT must be between 1 and 65535')
    data_dir = Path(os.environ.get('REVECTOR_DATA_DIR', '/engine/data')).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    if os.geteuid() == 0:
        account = pwd.getpwnam('revector')
        # Mounted Railway volumes replace image ownership. Only initialize the
        # mount root; project files are subsequently created by the non-root API.
        os.chown(data_dir, account.pw_uid, account.pw_gid)
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    import uvicorn
    uvicorn.run('app.main:app', host='0.0.0.0', port=port, workers=1,
                timeout_graceful_shutdown=190)


if __name__ == '__main__':
    main()
