"""Teamarr v2 entry point."""
import os
import uvicorn

from teamarr.api.app import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 9195))
    uvicorn.run(app, host="0.0.0.0", port=port)
