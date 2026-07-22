from contextvars import ContextVar, Token

request_id_context: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    return request_id_context.get()


def set_request_id(value: str) -> Token[str]:
    return request_id_context.set(value)


def reset_request_id(token: Token[str]) -> None:
    request_id_context.reset(token)
