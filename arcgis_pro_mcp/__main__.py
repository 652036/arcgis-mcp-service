from __future__ import annotations

import sys

from arcgis_pro_mcp.server import mcp


def main() -> None:
    try:
        mcp.run()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001 - top-level guard
        sys.stderr.write(f"arcgis-pro-mcp failed to start: {exc!r}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
