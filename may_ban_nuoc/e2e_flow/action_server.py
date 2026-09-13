"""Terminal 2: existing actions with source-local receive/completion timestamps."""
from .common import load_config
from .support.runtime import arguments, client, instrument_http, instrument_method


def main():
    args = arguments(__doc__, actions=True)
    cfg = load_config(args.config)
    events = client('action', args, cfg)
    from rasa_sdk.executor import ActionExecutor
    from rasa_sdk.endpoint import create_app
    instrument_method(ActionExecutor, 'run', events, 'action.execution',
                      lambda a, k: {'action_name': a[1].get('next_action')})
    app = instrument_http(create_app(args.actions), events, 'action')
    events.emit('service.starting', {'port': cfg['ports']['action']})
    app.run(host='127.0.0.1', port=cfg['ports']['action'], workers=1)


if __name__ == '__main__':
    main()
