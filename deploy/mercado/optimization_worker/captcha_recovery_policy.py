"""Bounded decisions for observed platform verification states."""


def next_action(observed_state, attempts, browser_available):
    if (observed_state not in ('loading','interactive','manual_code','passed')
            or type(attempts) is not int or not 0<=attempts<=2
            or type(browser_available) is not bool):
        raise ValueError('Invalid verification state')
    if observed_state=='passed':return {'action':'verify_page'}
    if observed_state=='manual_code':return {'action':'pause','reason':'user_verification_required'}
    if observed_state=='loading':return {'action':'wait'}
    if not browser_available:return {'action':'pause','reason':'browser_capability_unavailable'}
    if attempts>=2:return {'action':'pause','reason':'captcha_attempts_exhausted'}
    return {'action':'attempt','attempt':attempts+1}
