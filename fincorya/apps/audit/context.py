from contextvars import ContextVar


_request_ip = ContextVar("fincorya_request_ip", default=None)


def set_request_ip(value):
    return _request_ip.set(value)


def reset_request_ip(token):
    _request_ip.reset(token)


def get_request_ip():
    return _request_ip.get()
