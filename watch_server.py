"""Run Segura Gestão and periodically fast-forward it from the public main branch."""
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "server.py"
VERSION_FILE = ROOT / "data" / "build-version.txt"
UPDATE_INTERVAL = 60


def run_git(*args, timeout=45):
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(["git", *args], cwd=ROOT, env=env, capture_output=True,
                          text=True, timeout=timeout, check=False)


def signature():
    return hashlib.sha256(SERVER.read_bytes()).hexdigest()


def current_commit():
    if not shutil.which("git"):
        return "local"
    try:
        result = run_git("rev-parse", "HEAD")
    except (OSError, subprocess.TimeoutExpired):
        return "local"
    return result.stdout.strip() if result.returncode == 0 else "local"


def publish_version(version):
    VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = VERSION_FILE.with_suffix(".tmp")
    temporary.write_text(version + "\n", encoding="utf-8")
    temporary.replace(VERSION_FILE)


def check_for_updates():
    """Fetch and fast-forward only when no local edits or divergent commits exist."""
    if not shutil.which("git"):
        return False, "Git não foi encontrado; a atualização automática está desativada."
    try:
        status = run_git("status", "--porcelain", "--untracked-files=normal")
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f"Não foi possível conferir o repositório: {error}"
    if status.returncode != 0:
        return False, status.stderr.strip() or "Não foi possível verificar as alterações locais."
    if status.stdout.strip():
        return False, "Há alterações locais; pulei a atualização automática para preservá-las."
    try:
        fetched = run_git("fetch", "--quiet", "origin", "+refs/heads/main:refs/remotes/origin/main")
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f"Não consegui consultar o GitHub; vou tentar novamente: {error}"
    if fetched.returncode != 0:
        return False, fetched.stderr.strip() or "Não consegui consultar o GitHub; vou tentar novamente."
    counts = run_git("rev-list", "--left-right", "--count", "HEAD...origin/main")
    if counts.returncode != 0:
        return False, counts.stderr.strip() or "Não foi possível comparar com a branch main."
    ahead, behind = (int(value) for value in counts.stdout.split())
    if behind == 0:
        return False, ""
    if ahead:
        return False, "A cópia local divergiu do GitHub; nenhuma alteração foi aplicada automaticamente."
    try:
        merged = run_git("merge", "--ff-only", "origin/main")
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f"A atualização não pôde ser aplicada: {error}"
    if merged.returncode != 0:
        return False, merged.stderr.strip() or "A atualização não pôde ser aplicada em avanço rápido."
    version = current_commit()
    print(f"Atualização do GitHub aplicada ({version[:8]}).", flush=True)
    return True, version


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
    if shutil.which("git"):
        updated, message = check_for_updates()
        if message and not updated:
            print(message, flush=True)
    version = current_commit()
    publish_version(version)
    current = signature()
    process = start_server()
    print(f"Verificando atualizações do GitHub a cada {UPDATE_INTERVAL} segundos.", flush=True)
    next_check = time.monotonic() + UPDATE_INTERVAL
    version_after_restart = None
    last_message = ""
    try:
        while True:
            time.sleep(0.5)
            if process.poll() is not None:
                print(f"O servidor terminou com código {process.returncode}.", flush=True)
                return process.returncode or 0
            now = time.monotonic()
            if now >= next_check:
                updated, message = check_for_updates()
                next_check = now + UPDATE_INTERVAL
                if message and not updated and message != last_message:
                    print(message, flush=True)
                last_message = message if not updated else ""
                if updated:
                    new_signature = signature()
                    if new_signature == current:
                        # Static files are served from disk; notify open browsers to reload.
                        publish_version(message)
                    else:
                        version_after_restart = message
            changed = signature()
            if changed == current:
                continue
            # Give Git time to finish replacing files before restarting the backend.
            time.sleep(1.5)
            stable = signature()
            if stable == current:
                continue
            print("Código do servidor atualizado; reiniciando automaticamente...", flush=True)
            stop_server(process)
            current = stable
            process = start_server()
            publish_version(version_after_restart or current_commit())
            version_after_restart = None
    except KeyboardInterrupt:
        print("Encerrando Segura Gestão...", flush=True)
        stop_server(process)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
