"""Inspect and launch the single pinned local deployment; no model downloads."""
import hashlib
import os
import json
import urllib.request
from pathlib import Path


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def verify(model, root):
    deployment = model['deployment']
    artifacts = deployment['runtime_files'] | {deployment['weights_path']: deployment['weights_sha256']}
    for relative, expected in artifacts.items():
        if file_hash(Path(root) / relative) != expected:
            raise ValueError(f'Local deployment artifact changed: {relative}')
    return deployment


def serve(model, root):
    deployment = verify(model, root)
    binary = str((Path(root) / deployment['server_path']).resolve())
    # CLI flags are pinned; avoid LLAMA_ARG_* environment overrides.
    env = {k: v for k, v in os.environ.items() if not k.startswith('LLAMA_ARG_')}
    os.execve(binary, [binary, '-m', str((Path(root) / deployment['weights_path']).resolve()),
                       '--alias', model['model'], *deployment['server_arguments']], env)


def inspect_server(model, root):
    with urllib.request.urlopen('http://127.0.0.1:8876/props', timeout=20) as response:
        props = json.load(response)
    d = model['deployment']
    if props['model_alias'] != model['model'] or Path(props['model_path']).resolve() != (Path(root) / d['weights_path']).resolve():
        raise ValueError('Local server model/path mismatch')
    reported_commit = props['build_info'].rsplit('-', 1)[-1]
    if len(reported_commit) < 7 or not d['runtime_commit'].startswith(reported_commit):
        raise ValueError('Local server runtime mismatch')
    if hashlib.sha256(props['chat_template'].encode()).hexdigest() != d['chat_template_sha256']:
        raise ValueError('Local server template mismatch')
    defaults = props['default_generation_settings']
    if defaults['n_ctx'] != 4096 or props['total_slots'] != 1 or defaults['params']['samplers'] != ['top_k', 'top_p', 'temperature']:
        raise ValueError('Local context/slot/sampler mismatch')
    if props.get('cors_proxy_enabled') or props.get('ui'):
        raise ValueError('Unexpected local tools proxy or UI enabled')
    return props
