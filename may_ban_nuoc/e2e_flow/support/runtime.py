"""Shared CLI and source-local event instrumentation for independently run services."""
import argparse
import json
import inspect
import time
import os
import uuid
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

from ..common import EventClient, load_config, request, utc_now

HERE = Path(__file__).resolve().parents[1]
CONTEXT = ContextVar('e2e_context', default={})


def arguments(description, model=False, actions=False):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--config', type=Path, default=HERE/'config.json')
    parser.add_argument('--output', type=Path, default=HERE/'runs'/'current')
    if actions:
        parser.add_argument("--actions", default="actions")
    if model:
        parser.add_argument('--model', type=Path, required=True)
    return parser.parse_args()


def client(source, args, cfg):
    output = args.output.resolve()
    url = f"http://127.0.0.1:{cfg['ports']['log']}"
    health = request(url+'/health')
    if not health['ready'] or health['output'] != str(output):
        raise RuntimeError('All five terminals must use the same --output directory')
    request(url+'/register', dict(name=source, pid=os.getpid()))
    return EventClient(url, source, output.name, output, background=True)


def serial(value):
    if hasattr(value, 'as_dict'):
        return serial(value.as_dict())
    if isinstance(value, dict):
        return {str(k): serial(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serial(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def instrument_method(cls, method, events, name, inputs, outputs=lambda result, args: result):
    original = getattr(cls, method)
    signature = inspect.signature(original)

    @wraps(original)
    async def measured(*args, **kwargs):
        context = dict(CONTEXT.get(), operation_id=uuid.uuid4().hex)
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        started_context = dict(context, monotonic_ns=time.monotonic_ns(), time=utc_now())
        events.emit(name+'.started', serial(inputs(bound.args, bound.kwargs)), started_context)
        try:
            result = await original(*args, **kwargs)
        except BaseException as exc:
            events.emit(name+'.failed', {'error': repr(exc)}, context, 'ERROR')
            raise
        completed_context = dict(context, monotonic_ns=time.monotonic_ns(), time=utc_now())
        events.emit(name+'.completed', serial(outputs(result, bound.args)), completed_context)
        return result

    setattr(cls, method, measured)


def instrument_http(app, events, source):
    """Timestamp receipt and response creation INSIDE the serving process."""
    from sanic.response import json as response_json

    @app.get('/e2e/health')
    async def health(req):
        return response_json(dict(ready=True, source=source, pid=os.getpid(),
                                  output=str(events.path.parent.resolve()), run_id=events.run_id))

    @app.post('/e2e/flush')
    async def flush(req):
        return response_json({'pending_events': events.flush()})

    @app.middleware('request')
    async def received(req):
        target = '/webhooks/rest/webhook' if source == 'rasa' else '/webhook'
        if req.path != target or req.method != 'POST':
            return
        stamp = dict(monotonic_ns=time.monotonic_ns(), time=utc_now())
        import zlib
        body = json.loads(zlib.decompress(req.body)) if req.headers.get('Content-Encoding') == 'deflate' else (req.json or {})
        metadata = body.get('metadata', {}) if source == 'rasa' else body.get('tracker', {}).get('latest_message', {}).get('metadata', {})
        context = {k: metadata.get(k) for k in ('session_id', 'turn_id')}
        context['operation_id'] = uuid.uuid4().hex
        req.ctx.e2e_context = context
        req.ctx.e2e_token = CONTEXT.set(context)
        logged = body if source == 'rasa' else {
            'action_name': body.get('next_action'),
            'message': body.get('tracker', {}).get('latest_message'),
            'slots': body.get('tracker', {}).get('slots')}
        events.emit(source+'.received', logged, dict(context, **stamp))

    @app.middleware('response')
    async def completed(req, response):
        context = getattr(req.ctx, 'e2e_context', None)
        if context is None:
            return
        stamp = dict(monotonic_ns=time.monotonic_ns(), time=utc_now())
        try:
            body = json.loads(response.body)
        except (ValueError, TypeError, AttributeError):
            body = {'body': str(getattr(response, 'body', None))}
        events.emit(source+('.processing.completed' if response.status < 400 else '.processing.failed'),
                    dict(status_code=response.status, response=body), dict(context, **stamp),
                    'INFO' if response.status < 400 else 'ERROR')
        CONTEXT.reset(req.ctx.e2e_token)
    return app
