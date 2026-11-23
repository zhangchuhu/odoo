def filtered_response_headers(headers):
    blocked = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
               'te', 'trailer', 'trailers', 'transfer-encoding', 'upgrade', 'host',
               'content-length', 'set-cookie'}
    for key, value in headers:
        if key.lower() == 'connection':
            blocked.update(part.strip().lower() for part in value.split(','))
    return [(key, value) for key, value in headers if key.lower() not in blocked]
