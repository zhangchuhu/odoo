"""Transport-neutral requests and tool results."""

from dataclasses import dataclass, field

UNSET = object()


@dataclass(frozen=True)
class Upload:
    content: bytes
    filename: str
    content_type: str


@dataclass(frozen=True)
class RequestSpec:
    method: str
    path: str
    query: tuple[tuple[str, str], ...] = ()
    body: object = UNSET
    upload: Upload | None = None


@dataclass(frozen=True)
class ToolResult:
    payload: dict[str, object] = field(default_factory=dict)
    is_error: bool = False


def error(code: str, **details) -> ToolResult:
    return ToolResult({"mcpError": True, "code": code, **details}, True)
