"""Run the app and restart its server process when server.py changes."""
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "server.py"


def signature():
    stat = SERVER.stat()
    return stat.st_mtime_ns, stat.st_size


def start_server():
    print("Iniciando Segura Gestão em http://127.0.0.1:8000", flush=True)
    return subprocess.Popen([sys.executable, str(SERVER)], cwd=ROOT)


def stop_server(process):
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def main():
    current = signature()
    process = start_server()
    try:
        while True:
            time.sleep(0.5)
            if process.poll() is not None:
                print(f"O servidor terminou com código {process.returncode}.", flush=True)
                return process.returncode or 0
            changed = signature()
            if changed == current:
                continue
            # Wait until Git has finished replacing the file before restarting.
            time.sleep(1.5)
            stable = signature()
            if stable == current:
                continue
            print("Código do servidor atualizado; reiniciando automaticamente...", flush=True)
            stop_server(process)
            current = stable
            process = start_server()
    except KeyboardInterrupt:
        print("Encerrando Segura Gestão...", flush=True)
        stop_server(process)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
