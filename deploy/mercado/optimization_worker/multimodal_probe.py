"""Read-only randomized input challenge, not an image-tool or write probe."""
import os
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from PIL import Image, ImageDraw
from .text_host import proxy_environment

COLORS = {'RED': (255, 0, 0), 'GREEN': (0, 255, 0), 'BLUE': (0, 0, 255), 'YELLOW': (255, 255, 0)}

def multimodal_capability_adapter(codex_home, proxy, *, timeout=25, run_probe=None, now=time.time):
    """Fresh image-input proof only; not proof of image tools or writeback."""
    if type(timeout) not in (int, float) or not 1 <= timeout <= 25:
        raise ValueError('Invalid capability probe timeout')
    run = run_probe or probe_multimodal
    def probe(nonce):
        ok = run(codex_home, proxy, timeout=timeout) is True
        return {'nonce': nonce, 'observed_at': now(), 'ok': ok}
    return probe

def probe_multimodal(codex_home, proxy, *, runner=subprocess.run, timeout=90):
    try:
        if not Path(codex_home).is_absolute() or not Path(codex_home).is_dir() or not 1 <= timeout <= 120:
            return False
        env = {k: os.environ[k] for k in ('PATH','HOME','TMPDIR','LANG','LC_ALL') if k in os.environ}
        env.update(proxy_environment(proxy))
        env['CODEX_HOME'] = codex_home
        with tempfile.TemporaryDirectory(prefix='codex-vision-probe-') as directory:
            path, answer = Path(directory)/'challenge.png', Path(directory)/'answer.txt'
            sequence = [secrets.choice(list(COLORS)) for _ in range(6)]
            img = Image.new('RGB', (240, 40))
            draw = ImageDraw.Draw(img)
            for index, color in enumerate(sequence):
                draw.rectangle((40*index, 0, 40*index+39, 39), fill=COLORS[color])
            img.save(path)
            prompt = ('The attached image has six solid color vertical bands. Read them left to right. '
                      'Reply only with six uppercase English color names separated by spaces; use RED, GREEN, BLUE, YELLOW. '
                      'Do not use any tools.')
            proc = runner(['codex','exec','--ignore-user-config','--disable','shell_tool','--disable','unified_exec',
                           '--sandbox','read-only','--skip-git-repo-check','--ephemeral','-C',directory,
                           '--image',str(path),'--output-last-message',str(answer),'-'], input=prompt,
                          text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, timeout=timeout, check=False)
            if proc.returncode != 0 or not answer.is_file() or answer.stat().st_size > 200:
                return False
            return answer.read_text().strip() == ' '.join(sequence)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return False
