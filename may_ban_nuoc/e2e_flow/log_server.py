"""Terminal 1: receive source events and write exactly one log per session."""
import json
import os
import re
import threading
import uuid
import time
from pathlib import Path
import psutil
from .common import APIError, serve, utc_now
from .support.report import format_event, write_session, export_delays


class LogServer:
    def __init__(self, output, interval):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        if any(self.output.iterdir()):
            raise ValueError('Use an empty output directory for this run')
        self.interval = interval
        self.origin_ns = None
        self.finalized = False
        self.previous_sample_ns = None
        self.lock = threading.RLock()
        self.seen, self.processes = set(), {}
        self.errors, self.preamble = [], []
        self.sessions = {}
        self.all_events = []
        self.stop_event = threading.Event()
        self.context = {'run_id': self.output.name, 'session_id': None, 'turn_id': None}
        self.register('log', os.getpid())
        self.thread = threading.Thread(target=self.sample_loop, daemon=True)
        self.thread.start()

    def register(self, name, pid):
        proc = psutil.Process(pid)
        proc.cpu_percent(None)
        with self.lock:
            self.processes[pid] = (name, proc)

    def path(self, sid):
        return self.output / (sid + '.log')

    def accept(self, event):
        with self.lock:
            if event['event_id'] in self.seen:
                return
            if self.finalized:
                raise APIError('Run is finalized; event delivery is incomplete', 409)
            sid = event.get('session_id')
            if sid and not re.fullmatch(r'[A-Za-z0-9_-]+', sid):
                raise APIError('Invalid session ID', 400)
            name = event['event_name']
            if name == 'test.started':
                self.origin_ns = event['monotonic_ns']
            event = dict(event)
            event['received_at'] = utc_now()
            event['elapsed_ms'] = ((event['monotonic_ns']-self.origin_ns)/1e6 if self.origin_ns else None)
            if name == 'session.started':
                self.sessions.setdefault(sid, [])
                if len(self.sessions)==1:
                    self.sessions[sid].extend(self.preamble)
                    self.preamble.clear()
                self.context.update(session_id=sid, turn_id=None)
            if name == 'turn.started':
                self.context.update(session_id=sid, turn_id=event.get('turn_id'))
            if name == 'session.completed' and self.context['session_id']==sid:
                self.context.update(session_id=None, turn_id=None)
            if sid:
                self.sessions.setdefault(sid, []).append(event)
                path = self.path(sid)
                if not path.exists():
                    path.write_text(f'SESSION: {sid}\nLIVE LOG — session incomplete until finalized. Times are UTC.\n', encoding='utf-8')
                with path.open('a', encoding='utf-8') as stream:
                    stream.write(format_event(event))
                    stream.flush()
            elif name == 'test.completed' and self.sessions:
                for rows in self.sessions.values():
                    rows.append(event)
            elif name != 'system.sample' or not self.sessions:
                self.preamble.append(event)
            self.all_events.append(event)
            with (self.output/'events.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(event, ensure_ascii=False)+'\n')
            self.seen.add(event['event_id'])

    def dispatch(self, method, path, data):
        if method=='GET' and path=='/health':
            return dict(ready=not self.errors and not self.finalized,
                        output=str(self.output.resolve()), errors=self.errors,
                        sampler_alive=self.thread.is_alive())
        if method=='POST' and path=='/register':
            self.register(data['name'], int(data['pid']))
            return {'ok': True}
        if method=='POST' and path=='/events':
            for key in ('event_id','event_name','time','source','run_id','monotonic_ns'):
                if key not in data:
                    raise APIError('Missing field: '+key, 400)
            self.accept(data)
            return {'ok': True}
        if method=='POST' and path=='/session/close':
            with self.lock:
                sid = data['session_id']
                if sid not in self.sessions:
                    raise APIError('Unknown session', 404)
                complete = write_session(self.path(sid), self.sessions[sid], self.errors)
                return {'complete': complete}
        if method=='POST' and path=='/finalize':
            self.stop_event.set()
            self.thread.join(5)
            with self.lock:
                if data.get('error'):
                    self.errors.append(data['error'])
                self.finalized = True
                rows = sorted(self.all_events, key=lambda e: e['monotonic_ns'])
                for event in rows:
                    event['elapsed_ms'] = ((event['monotonic_ns']-self.origin_ns)/1e6 if self.origin_ns else None)
                (self.output/'events.jsonl').write_text(''.join(json.dumps(e, ensure_ascii=False)+'\n' for e in rows))
                export_delays(self.output, rows)
                results = [write_session(self.path(sid), rows, self.errors) for sid, rows in self.sessions.items()]
                return {'complete': bool(results) and all(results) and not self.thread.is_alive(), 'errors': self.errors}
        raise APIError('Unknown endpoint', 404)

    def sample_loop(self):
        psutil.cpu_percent(None)
        deadline = time.monotonic()
        while not self.stop_event.is_set():
            try:
                memory = psutil.virtual_memory()
                try:
                    temperatures = {k: [x._asdict() for x in v]
                                    for k, v in psutil.sensors_temperatures().items()}
                except (AttributeError, OSError):
                    temperatures = {}
                freq = psutil.cpu_freq()
                record = dict(time=utc_now(), monotonic_ns=time.monotonic_ns(),
                              cpu_pct=psutil.cpu_percent(None), ram_used_mb=memory.used / 2**20,
                              ram_available_mb=memory.available / 2**20,
                              ram_pct=memory.percent, swap_used_mb=psutil.swap_memory().used / 2**20,
                              cpu_frequency_mhz=freq.current if freq else None,
                              temperatures=temperatures or None,
                              temperature_unavailable_reason=None if temperatures else "No accessible sensor",
                              processes=[])
                with self.lock:
                    record.update(self.context)
                    entries = list(self.processes.items())
                for pid, (name, proc) in entries:
                    try:
                        for child in proc.children(recursive=True):
                            with self.lock:
                                if child.pid not in self.processes:
                                    self.register(name + "/child", child.pid)
                        with proc.oneshot():
                            io = proc.io_counters()
                            record["processes"].append(dict(
                                name=name, pid=pid, cpu_pct=proc.cpu_percent(None),
                                rss_mb=proc.memory_info().rss / 2**20,
                                threads=proc.num_threads(), status=proc.status(),
                                io_read_bytes=io.read_bytes, io_write_bytes=io.write_bytes))
                    except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                        record["processes"].append(dict(name=name, pid=pid, unavailable=type(exc).__name__))
                stamp = record['monotonic_ns']
                record['sample_interval_ms'] = ((stamp-self.previous_sample_ns)/1e6 if self.previous_sample_ns else None)
                self.previous_sample_ns = stamp
                event = dict(event_id=uuid.uuid4().hex, event_name='system.sample', source='log',
                             run_id=self.output.name, session_id=record.get('session_id'),
                             turn_id=record.get('turn_id'), operation_id=None, time=record['time'],
                             monotonic_ns=stamp, elapsed_ms=(stamp-self.origin_ns)/1e6 if self.origin_ns else None,
                             event_data=record)
                self.accept(event)
            except Exception as exc:
                self.errors.append(str(exc))
                print("Sampler error:", exc, flush=True)
            deadline += self.interval
            self.stop_event.wait(max(0, deadline-time.monotonic()))
            if time.monotonic() > deadline + self.interval:
                deadline = time.monotonic()


def main():
    from .support.runtime import arguments
    from .common import load_config
    args = arguments(__doc__)
    cfg = load_config(args.config)
    interval = cfg['sample_interval_seconds']
    if interval <= 0:
        raise ValueError('Sample interval must be positive')
    app = LogServer(args.output, interval)
    print(f"Session logs: {args.output.resolve()} (sampling every {interval}s)", flush=True)
    try:
        serve(cfg['ports']['log'], app.dispatch)
    finally:
        app.dispatch('POST', '/finalize', {'error': 'Logger stopped'} if not app.finalized else {})


if __name__=='__main__':
    main()
