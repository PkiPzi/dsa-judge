if __name__ == "__main__":
    sys.setrecursionlimit(1_000_000)
    threading.stack_size(512 * 1024 * 1024)
    state = {"ok": False}

    def guarded():
        try:
            main(int(sys.argv[1]), os.path.abspath(sys.argv[2]), state)
        except BaseException:  # noqa: BLE001
            traceback.print_exc()

    th = threading.Thread(target=guarded)
    th.start()
    th.join()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if state["ok"] else 70)
