"""
run.py — Entry point.

Jalankan dengan: python run.py
Jangan jalankan uvicorn langsung karena ProactorEventLoop harus diset
sebelum uvicorn membuat event loop-nya.
"""

import asyncio
import sys
import os

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    print("[run.py] WindowsProactorEventLoopPolicy aktif.")

# pyrefly: ignore [missing-import]
import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(
        "app.main:app",
        host    = "0.0.0.0",
        port    = port,
        reload  = False,
        loop    = "asyncio",
    )
