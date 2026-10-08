import argparse
import os
import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Modbus 从机模拟器：后端同时提供页面")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args()
    if args.host not in ("127.0.0.1", "::1", "localhost"):
        parser.error("管理界面只允许本机访问；远程管理需先实现专用认证配置")
    if args.data_dir:
        os.environ["MODBUS_DATA_DIR"] = args.data_dir
    from .api import create_app

    uvicorn.run(
        create_app(),
        host=args.host,
        port=args.port,
        workers=1,
        timeout_graceful_shutdown=15,
        ws_max_size=1048576,
        ws_max_queue=2,
        limit_concurrency=128,
        backlog=64,
        access_log=False,
    )


if __name__ == "__main__":
    main()
