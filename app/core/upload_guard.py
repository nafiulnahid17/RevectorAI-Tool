"""Bound multipart uploads even when the gateway streams without Content-Length."""
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse


class UploadGuard:
    def __init__(self, app, limit: int):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['path'] != '/api/revector/upload':
            return await self.app(scope, receive, send)
        raw_length = dict(scope.get('headers', [])).get(b'content-length')
        if raw_length is not None:
            try:
                length = int(raw_length)
                if length < 0 or length > self.limit:
                    raise ValueError
            except ValueError:
                response = JSONResponse(status_code=413, content={'success': False, 'error': {
                    'code': 'UPLOAD_TOO_LARGE', 'message': 'Multipart upload exceeds configured limit', 'recoverable': True}})
                return await response(scope, receive, send)
        consumed = 0

        async def bounded_receive():
            nonlocal consumed
            message = await receive()
            consumed += len(message.get('body', b''))
            if consumed > self.limit:
                raise HTTPException(413, detail={'code': 'UPLOAD_TOO_LARGE', 'message': 'Multipart upload exceeds configured limit'})
            return message

        await self.app(scope, bounded_receive, send)
