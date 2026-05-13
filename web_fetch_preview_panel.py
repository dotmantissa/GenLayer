# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json

ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"


def _raise_user_error(message: str) -> None:
    err_cls = getattr(gl, "UserError", Exception)
    raise err_cls(message)


class WebFetchPreviewPanel(gl.Contract):
    """Runs sandboxed preview fetches and reports raw payload and parsing diagnostics."""

    previews: str
    next_preview_id: u256

    def __init__(self):
        """Initialize storage.

        Parameters:
            None.

        Returns:
            None.
        """
        self.previews = "{}"
        self.next_preview_id = 1

    @gl.public.write
    def preview_fetch(self, url: str, max_preview_bytes: int) -> str:
        """Fetch a URL and return diagnostics for preview usage.

        Parameters:
            url: Target URL for gl.nondet.web.get.
            max_preview_bytes: Maximum bytes to retain for raw preview.

        Returns:
            Preview id string.
        """
        target = str(url).strip()
        if not (target.startswith("http://") or target.startswith("https://")):
            _raise_user_error(f"{ERROR_EXPECTED} invalid url")
        if max_preview_bytes < 64 or max_preview_bytes > 200000:
            _raise_user_error(f"{ERROR_EXPECTED} max_preview_bytes out of range")

        response = gl.nondet.web.get(target)
        status = int(response.status)
        if status >= 400 and status < 500:
            _raise_user_error(f"{ERROR_EXTERNAL} client error: {status}")
        if status >= 500:
            _raise_user_error(f"{ERROR_EXTERNAL} server error: {status}")

        body_bytes = response.body if response.body is not None else b""
        body_len = len(body_bytes)

        encoding_issue = False
        decoded = ""
        try:
            decoded = body_bytes.decode("utf-8")
        except Exception:
            encoding_issue = True
            decoded = body_bytes.decode("utf-8", errors="replace")

        size_issue = body_len > int(max_preview_bytes)
        raw_preview = decoded[: int(max_preview_bytes)]

        parsed_json = False
        parsed_fields = []
        try:
            parsed = json.loads(decoded)
            parsed_json = True
            if isinstance(parsed, dict):
                parsed_fields = sorted([str(k) for k in parsed.keys()])[:50]
            elif isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
                parsed_fields = sorted([str(k) for k in parsed[0].keys()])[:50]
        except Exception:
            parsed_json = False

        pid = str(self.next_preview_id)
        self.next_preview_id += 1

        previews = json.loads(self.previews)
        previews[pid] = {
            "preview_id": pid,
            "requester": str(gl.message.sender_account),
            "url": target,
            "status_code": status,
            "response_size_bytes": body_len,
            "encoding_issue": bool(encoding_issue),
            "size_issue": bool(size_issue),
            "parsed_json": bool(parsed_json),
            "parsed_fields": parsed_fields,
            "raw_response_preview": raw_preview,
            "created_at": str(gl.block.timestamp),
        }
        self.previews = json.dumps(previews)
        return pid

    @gl.public.view
    def get_preview(self, preview_id: str) -> str:
        """Read one preview result.

        Parameters:
            preview_id: Preview id string.

        Returns:
            Preview JSON string.
        """
        previews = json.loads(self.previews)
        key = str(preview_id)
        if key not in previews:
            _raise_user_error(f"{ERROR_EXPECTED} preview not found")
        return json.dumps(previews[key])

    @gl.public.view
    def get_all_previews(self) -> str:
        """Read all preview results.

        Parameters:
            None.

        Returns:
            Previews map JSON string.
        """
        return self.previews
