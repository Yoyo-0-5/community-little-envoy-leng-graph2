import os
from pathlib import Path

import uvicorn


def _ssl_files() -> tuple[str | None, str | None]:
    """找出要掛的 HTTPS 憑證（cert, key）。先看環境變數，再自動偵測 Tailscale 憑證。"""
    cert = os.getenv("SSL_CERTFILE")
    key = os.getenv("SSL_KEYFILE")
    if cert and key:
        return cert, key
    here = Path(__file__).resolve().parent
    for cert_path in sorted(here.glob("*.ts.net.crt")):
        key_path = cert_path.with_suffix(".key")
        if key_path.exists():
            return str(cert_path), str(key_path)
    return None, None


def main() -> None:
    cert, key = _ssl_files()
    kwargs = {}
    if cert and key:
        kwargs.update(ssl_certfile=cert, ssl_keyfile=key)
        print(f"[server] 啟用 HTTPS: https://{Path(cert).stem}:8000")
    else:
        print("[server] 未找到憑證，以 HTTP 啟動")
    uvicorn.run(
        "app.server:app",
        host="0.0.0.0",
        port=8000,
        reload=os.getenv("UVICORN_RELOAD") == "1",
        **kwargs,
    )


if __name__ == "__main__":
    main()