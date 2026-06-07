import contextvars
import threading


class CancelledError(Exception):
    """Raised when a generation task is cancelled by the user."""


class CancellationToken:
    def __init__(self, project_id):
        self.project_id = project_id
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._closers = set()

    @property
    def cancelled(self):
        return self._event.is_set()

    def cancel(self):
        self._event.set()
        with self._lock:
            closers = list(self._closers)
        for closer in closers:
            try:
                closer()
            except Exception:
                pass

    def check(self):
        if self.cancelled:
            raise CancelledError("用户取消了生成任务")

    def register_closer(self, closer):
        with self._lock:
            if self.cancelled:
                should_close = True
            else:
                self._closers.add(closer)
                should_close = False
        if should_close:
            try:
                closer()
            finally:
                raise CancelledError("用户取消了生成任务")
        return closer

    def unregister_closer(self, closer):
        with self._lock:
            self._closers.discard(closer)


_current_token = contextvars.ContextVar("novel2script_cancellation_token", default=None)
_active_tokens = {}
_active_tokens_lock = threading.Lock()


def set_current_token(token):
    return _current_token.set(token)


def reset_current_token(context_token):
    _current_token.reset(context_token)


def get_current_token():
    return _current_token.get()


def check_cancelled():
    token = get_current_token()
    if token:
        token.check()


def register_project_token(token):
    with _active_tokens_lock:
        _active_tokens[token.project_id] = token


def unregister_project_token(project_id, token):
    with _active_tokens_lock:
        if _active_tokens.get(project_id) is token:
            del _active_tokens[project_id]


def cancel_project(project_id):
    with _active_tokens_lock:
        token = _active_tokens.get(project_id)
    if not token:
        return False
    token.cancel()
    return True


def is_cancelled_exception(exc):
    return isinstance(exc, CancelledError)
