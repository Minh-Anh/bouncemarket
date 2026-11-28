"""Runs the server.

    uv run --env-file .env app.py

Render runs it as "python app.py" (see render.yaml). The server itself is the bouncemarket
package. Gravity, bounce and the hidden nudges can be set here, e.g. --bounce 0.3; every
other setting is in bouncemarket/config.py.
"""
import argparse

import uvicorn

from bouncemarket import config
from bouncemarket.web.app import app


def main():
    parser = argparse.ArgumentParser(description="Run the bouncemarket server.")
    parser.add_argument("--gravity", type=float, default=config.GRAVITY, help="pixels / second^2")
    parser.add_argument("--bounce", type=float, default=config.BOUNCE, help="fraction of speed kept after hitting something, 0 to 1")
    parser.add_argument("--nudge", type=float, default=config.NUDGE, help="typical hidden turn at each pin bounce, in radians; 0 for none")
    args = parser.parse_args()
    config.GRAVITY, config.BOUNCE, config.NUDGE = args.gravity, args.bounce, args.nudge
    # uvicorn runs everything on one thread, switching between pages whenever one is waiting,
    # so thousands of open pages are cheap. The short shutdown timeout lets Ctrl-C work while
    # pages still have streams open.
    uvicorn.run(app, host="0.0.0.0", port=config.PORT, timeout_graceful_shutdown=1)


if __name__ == "__main__":
    main()
