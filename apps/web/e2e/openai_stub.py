"""
A local stand-in for the OpenAI embeddings endpoint.

The `merged` resolution path calls EmbeddingService, which is the only external
model call anywhere in conflict resolution. Rather than let it fail and observe
the documented degradation (NULL embedding, logged loudly, resolution still
succeeds), this serves a deterministic vector so the SUCCESS branch is the one
under test.

Nothing here approximates real embedding behaviour, and it is not meant to:
the vector is derived from a hash of the input so it is stable and non-zero.
Anything that depends on embeddings being semantically meaningful — similarity
search, conflict DETECTION — is therefore not exercised by this stub and is
explicitly out of scope for this run.

The openai-python SDK picks up OPENAI_BASE_URL from the environment when no
base_url is passed to the constructor, which is how the app builds its client,
so pointing that at this server guarantees no outbound network call.
"""

import hashlib
import json
import math
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8402
DIMENSIONS = 3072

_calls = []


def deterministic_vector(text: str, dims: int) -> list[float]:
    """A stable unit vector for `text`. Same input, same vector, every run."""
    seed = hashlib.sha256(text.encode("utf-8")).digest()
    out = []
    i = 0
    while len(out) < dims:
        block = hashlib.sha256(seed + i.to_bytes(4, "big")).digest()
        for b in block:
            if len(out) == dims:
                break
            out.append((b / 255.0) * 2.0 - 1.0)
        i += 1
    norm = math.sqrt(sum(v * v for v in out)) or 1.0
    return [v / norm for v in out]


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # Lets the test assert how many times the model was actually called.
        if self.path == "/__calls":
            self._send(200, {"count": len(_calls), "inputs": _calls})
        else:
            self._send(404, {"error": {"message": "not a stubbed route"}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"

        if not self.path.endswith("/embeddings"):
            # Loud rather than silent: any OTHER model call reaching this stub
            # is something the run did not expect, and the test should see it.
            self._send(404, {"error": {"message": f"unstubbed model route {self.path}"}})
            return

        try:
            req = json.loads(raw)
        except ValueError:
            self._send(400, {"error": {"message": "invalid json"}})
            return

        inputs = req.get("input", [])
        if isinstance(inputs, str):
            inputs = [inputs]
        dims = int(req.get("dimensions") or DIMENSIONS)
        _calls.extend(inputs)

        self._send(200, {
            "object": "list",
            "model": req.get("model", "text-embedding-3-large"),
            "data": [
                {"object": "embedding", "index": i,
                 "embedding": deterministic_vector(text, dims)}
                for i, text in enumerate(inputs)
            ],
            "usage": {"prompt_tokens": sum(len(t.split()) for t in inputs),
                      "total_tokens": sum(len(t.split()) for t in inputs)},
        })

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"openai-stub listening on 127.0.0.1:{PORT}", flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
