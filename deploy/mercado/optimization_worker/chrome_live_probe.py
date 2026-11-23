"""Read-only Codex Chrome probe; verify tool receipts, never model self-report."""
import json
import logging
import base64
import re
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess

from .interactive_dispatcher import codex_settings
from .research_capability_probe import chrome_event_verified

_logger = logging.getLogger(__name__)
_SCREENSHOT_CALL = re.compile(r'\s*await\s+app\.getScreenshot\(\)\s*;?\s*')

# The CLI may truncate large tool results. Validate the actual screenshot bytes
# inside the trusted native tool call and return a small proof, without pixels.
_COMPACT_CAPTURE = (
    "const raw = await app.getScreenshot({emit:false}); "
    "const bytes = Array.isArray(raw) ? raw[0]?.bytes : raw; "
    "if (!ArrayBuffer.isView(bytes) || bytes.length < 100) throw Error('No screenshot bytes'); "
    "let format; "
    "if (bytes[0]===255 && bytes[1]===216 && bytes[2]===255 && bytes.at(-2)===255 && bytes.at(-1)===217) format='jpeg'; "
    "else if ([137,80,78,71,13,10,26,10].every((byte,index)=>bytes[index]===byte) && "
    "[73,69,78,68,174,66,96,130].every((byte,index)=>bytes.at(index-8)===byte)) format='png'; "
    "else throw Error('Invalid screenshot bytes'); "
    "nodeRepl.write('Chrome screenshot proof: '+format+' '+bytes.length);"
)
_COMPACT_PROOF = re.compile(r'^Chrome screenshot proof: (?:jpeg|png) [1-9][0-9]{2,8}$', re.MULTILINE)


def _is_capture_code(code):
    return isinstance(code, str) and (code.strip() == _COMPACT_CAPTURE or _SCREENSHOT_CALL.fullmatch(code) is not None)


def _failed(stage, code):
    _logger.warning('Chrome capability probe at=%s stage=%s code=%s', datetime.now(timezone.utc).isoformat(), stage, code)
    return False


def screenshot_event_verified(item):
    return _screenshot_status(item) == 'ok'


def _screenshot_status(item):
    if not isinstance(item, dict) or any(item.get(k) != v for k, v in {'type':'mcp_tool_call','server':'cua_repl','tool':'js','status':'completed','error':None}.items()):
        return 'invalid'
    args, result = item.get('arguments'), item.get('result')
    if (not isinstance(args, dict) or 'code' not in args or set(args) - {'code', 'title'}
            or ('title' in args and not isinstance(args['title'], str))
            or not isinstance(result, dict) or result.get('isError') is True):
        return 'invalid'
    if not _is_capture_code(args['code']):
        return 'invalid'
    if not isinstance(result.get('content'), list):
        return 'invalid'
    blocks = list(result['content'])
    for block in list(blocks):
        if isinstance(block, dict) and block.get('type') == 'text' and isinstance(block.get('text'), str):
            try:
                wrapped = json.loads(block['text'])
                if isinstance(wrapped, dict) and isinstance(wrapped.get('content'), list):
                    blocks.extend(wrapped['content'])
            except (ValueError, TypeError):
                pass
    if args['code'].strip() == _COMPACT_CAPTURE:
        for block in blocks:
            if (isinstance(block, dict) and block.get('type') == 'text'
                    and _COMPACT_PROOF.search(str(block.get('text', '')))):
                return 'ok'
        return 'missing'
    image_present = False
    for block in blocks:
        if not isinstance(block, dict):
            continue
        image_present |= block.get('type') in ('image', 'input_image')
        data = block.get('data') if block.get('type') == 'image' and block.get('mimeType') in ('image/jpeg','image/png') else None
        url = block.get('image_url') if block.get('type') == 'input_image' else None
        if isinstance(url, str) and url.startswith(('data:image/jpeg;base64,','data:image/png;base64,')):
            data = url.split(',',1)[1]
        if isinstance(data, str):
            try:
                image = base64.b64decode(data, validate=True)
                if image.startswith((b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n')):
                    return 'ok'
            except (ValueError, TypeError):
                pass
    return 'invalid' if image_present else 'missing'


def probe_chrome(codex_home, workspace, computer_use_cwd, proxy, *, runner=subprocess.run, timeout=90):
    try:
        settings = codex_settings({'home': codex_home, 'cwd': workspace,
                                   'computer_use_cwd': computer_use_cwd, 'proxy': proxy})
        if type(timeout) not in (int, float) or not 0 < timeout <= 120:
            return False
        env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL') if key in os.environ}
        env.update(settings['env'])
        command = ['codex', 'exec', '-c', settings['mcp_override'], '--disable', 'shell_tool',
                   '--disable', 'unified_exec', '--skip-git-repo-check', '--ephemeral', '--json', '-']
        prompt = ('Read only: call exactly `let app = await cua.getApp("Google Chrome");`, then in a separate '
                  f'cua_repl.js call run exactly `{_COMPACT_CAPTURE}` to capture and verify a native screenshot. '
                  'Do not click, navigate, type, change data, use other tools or print page content. '
                  'Afterwards follow the tool completion instructions and report only success or failure.')
        result = runner(command, input=prompt, text=True, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, env=env, cwd=settings['cwd'], timeout=timeout, check=False)
        if result.returncode != 0 or not isinstance(result.stdout, str) or len(result.stdout) > 8 * 1024 * 1024:
            return _failed('process', 'chrome_probe_process_failed')
        verified = False
        screenshot_verified = False
        for line in result.stdout.splitlines():
            item = json.loads(line).get('item', {})
            if item.get('type') != 'mcp_tool_call' or item.get('status') != 'completed':
                continue
            if chrome_event_verified(item):
                verified = True
            elif (verified and item.get('server') == 'cua_repl' and item.get('tool') == 'js'
                  and _is_capture_code((item.get('arguments') or {}).get('code'))):
                status = _screenshot_status(item)
                if status != 'ok':
                    return _failed('screenshot', 'chrome_screenshot_missing' if status == 'missing' else 'chrome_screenshot_invalid')
                screenshot_verified = True
            elif not (item.get('server') == 'cua_repl' and item.get('tool') == 'js'
                      and item.get('error') is None and set(item.get('arguments', {})) == {'completion_summary'}):
                return _failed('receipt', 'chrome_probe_unexpected_receipt' if verified else 'chrome_window_unverified')
        if not verified:
            return _failed('window', 'chrome_window_unverified')
        if not screenshot_verified:
            return _failed('screenshot', 'chrome_screenshot_missing')
        return True
    except subprocess.TimeoutExpired:
        return _failed('process', 'chrome_probe_timeout')
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        return _failed('process', 'chrome_probe_error')
