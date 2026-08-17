from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run("rfmapping_web.app:app", host="127.0.0.1", port=3005)


if __name__ == "__main__":
    main()
