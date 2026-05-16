import asyncio
import logging
import platform
import resource
import shutil
import sys
import tempfile
from datetime import datetime, timezone

from config import CPU_LIMIT, MAX_FILE_SIZE, MAX_OPEN_FILES, MAX_OUTPUT, MAX_PROCESSES, MEM_LIMIT, TASK_TTL, TIMEOUT
from models import Status

logger = logging.getLogger(__name__)


class CodeExecutor:
    """Manages task lifecycle and sandboxed execution of arbitrary Python code."""

    def __init__(self):
        self.tasks: dict[str, dict] = {}

    def new_task(self, task_id: str, code: str) -> dict:
        return {
            "task_id": task_id,
            "status": Status.pending,
            "output": "",
            "exit_code": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "code": code,
            "queue": asyncio.Queue(),
        }

    @staticmethod
    def serialize_task(task: dict) -> dict:
        """Return task dict safe for JSON serialization, excluding internal fields."""
        return {k: v for k, v in task.items() if k != "queue"}

    @staticmethod
    def apply_limits():
        """Apply OS-level resource limits to the subprocess before user code runs."""
        limits = [
            (resource.RLIMIT_CPU, (CPU_LIMIT, CPU_LIMIT)),
            (resource.RLIMIT_NPROC, (MAX_PROCESSES, MAX_PROCESSES)),
            (resource.RLIMIT_FSIZE, (MAX_FILE_SIZE, MAX_FILE_SIZE)),
            (resource.RLIMIT_NOFILE, (MAX_OPEN_FILES, MAX_OPEN_FILES)),
        ]
        mem_type = resource.RLIMIT_DATA if platform.system() == "Darwin" else resource.RLIMIT_AS
        limits.append((mem_type, (MEM_LIMIT, MEM_LIMIT)))

        for limit, val in limits:
            try:
                resource.setrlimit(limit, val)
            except (ValueError, resource.error):
                pass

    async def run_code(self, task_id: str):
        """
        Executes the submitted Python code in a sandboxed subprocess.

        Spawns a child process with OS-level resource limits (CPU, memory, file size,
        open files, process count) and a wall-clock timeout. Streams stdout/stderr lines
        into the task's queue for real-time consumption, then puts a None sentinel when
        done. Updates task status to completed, failed, timeout, or killed based on the
        exit code.
        """
        task = self.tasks[task_id]
        tmpdir = tempfile.mkdtemp()
        proc = None

        logger.info("Running task %s", task_id)
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-u", "-c", task["code"],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=tmpdir,
                preexec_fn=self.apply_limits,
            )
            task["status"] = Status.running

            async def read():
                total = 0
                async for line in proc.stdout:
                    if total < MAX_OUTPUT:
                        text = line.decode(errors="replace")
                        task["output"] += text
                        await task["queue"].put(text)
                        total += len(line)
                await proc.wait()

            await asyncio.wait_for(read(), timeout=TIMEOUT)

            code = proc.returncode
            task["exit_code"] = code
            if code == 0:
                task["status"] = Status.completed
            elif code < 0:
                task["status"] = Status.killed
            else:
                task["status"] = Status.failed
            logger.info("Task %s finished with status=%s exit_code=%s", task_id, task["status"], code)

        except asyncio.TimeoutError:
            task["status"] = Status.timeout
            logger.warning("Task %s timed out", task_id)
            if proc and proc.returncode is None:
                proc.kill()
                await proc.wait()

        except Exception as e:
            task["status"] = Status.failed
            task["output"] += f"\n[error: {e}]"
            logger.error("Task %s raised an unexpected error: %s", task_id, e)

        finally:
            task["completed_at"] = datetime.now(timezone.utc).isoformat()
            await task["queue"].put(None)
            shutil.rmtree(tmpdir, ignore_errors=True)

    async def cleanup(self):
        """Periodically remove completed tasks older than 30 minutes to prevent memory growth."""
        while True:
            await asyncio.sleep(60)
            now = datetime.now(timezone.utc).timestamp()
            stale = [
                tid for tid, t in self.tasks.items()
                if t["status"] not in (Status.pending, Status.running)
                and now - datetime.fromisoformat(t["created_at"]).timestamp() > TASK_TTL
            ]
            if stale:
                logger.info("Cleaning up %d stale task(s): %s", len(stale), stale)
            for tid in stale:
                self.tasks.pop(tid, None)
