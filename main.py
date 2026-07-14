import sys

_SERVER_FLAGS = {"--server", "--once", "--headless"}

def main() -> int:
    if _SERVER_FLAGS.intersection(sys.argv[1:]):
        from mangodango.server import main as server_main
        return server_main(sys.argv[1:])
    from mangodango.app import main as gui_main
    return gui_main()

if __name__ == "__main__":
    raise SystemExit(main())