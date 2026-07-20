"""桌面单机版入口：起本地 uvicorn 并打开系统默认浏览器。

与容器部署的区别：
- 监听 127.0.0.1 而非 0.0.0.0 —— 桌面应用不应把服务暴露到局域网。
- 数据库与上传文件落在用户数据目录，重装程序不丢数据。
- 端口默认由系统分配，避免固定端口被占用导致启动失败。
"""
import os
import socket
import webbrowser

from app.paths import user_data_dir


def prepare_env() -> None:
    """把数据库与上传目录指向用户数据目录（已设置则不覆盖）。

    必须在 import app.main 之前调用 —— 该模块顶层会执行 create_app()，
    其中读取 REFLOW_UPLOAD_DIR 并创建目录。
    """
    data = user_data_dir()
    os.environ.setdefault("REFLOW_DB", str(data / "reflow.sqlite"))
    os.environ.setdefault("REFLOW_UPLOAD_DIR", str(data / "uploads"))


def bind_socket() -> tuple[socket.socket, int]:
    """绑定 127.0.0.1 并 listen，返回 (socket, 实际端口)。

    端口取 REFLOW_PORT；未设置时用 0 由系统分配空闲端口。
    返回前完成 listen：浏览器发出首个请求时 uvicorn 的 accept 循环可能尚未启动，
    但只要 socket 已 listen，内核就会把连接排队，不会 connection refused。
    """
    port = int(os.environ.get("REFLOW_PORT", "0"))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    actual_port = sock.getsockname()[1]
    sock.listen(128)
    return sock, actual_port


def main() -> None:
    """打包入口：准备环境 → 绑端口 → 开浏览器 → 起服务（阻塞）。"""
    prepare_env()

    # 必须在 prepare_env() 之后再 import：app.main 顶层会执行 create_app()
    import uvicorn
    from app.main import app

    sock, port = bind_socket()
    url = f"http://127.0.0.1:{port}/"

    print(f"Reflow 已启动：{url}")
    print("用完直接关掉这个窗口即可退出。")

    if not os.environ.get("REFLOW_NO_BROWSER"):
        webbrowser.open(url)

    # 不能用 uvicorn.run()：它内部自建 socket，无法接受已绑定的 socket，
    # 也就拿不到启动前的实际端口号。
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    server.run(sockets=[sock])


if __name__ == "__main__":
    main()
