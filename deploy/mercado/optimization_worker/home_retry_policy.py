"""Bounded reconnection decisions for homepage collection."""


def next_retry(reason,attempts):
    if (reason not in ('tab_closed','binding_lost','network_temporary','captcha','login','platform_blocked')
            or type(attempts) is not int or not 0<=attempts<=3):raise ValueError('Invalid retry state')
    if reason=='captcha':return {'action':'captcha'}
    if reason=='login':return {'action':'pause','reason':'login_required'}
    if reason=='platform_blocked':return {'action':'pause','reason':'platform_blocked'}
    if attempts>=3:return {'action':'pause','reason':'browser_unavailable'}
    return {'action':'retry','delay_seconds':(2,5,10)[attempts]}
