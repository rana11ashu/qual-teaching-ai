# Code Executor API

An HTTP API that runs arbitrary Python code in a sandboxed subprocess and streams the output back in real time.

## How it works

You POST some Python code, get back a task ID, then either stream the output as it runs or poll for the final result.

```
POST /execute          → submit code, get task_id
GET  /tasks/{id}       → get task status + full output
GET  /tasks/{id}/stream → stream stdout line by line (Server-Sent Events)
```

## Setup

You need Python 3.9+ installed.

```bash
# Create a virtual environment and install dependencies
python3 -m venv test-env
source test-env/bin/activate
pip install -r requirements.txt
```

Or just run the startup script which does all of the above:

```bash
chmod +x start.sh
./start.sh
```

The server starts on `http://localhost:8080`.

## Testing

All examples use `curl`. Replace `<task_id>` with the ID returned from `/execute`.

### Basic execution

```bash
# Submit code
curl -s -X POST http://localhost:8080/execute \
  -H 'Content-Type: application/json' \
  -d '{"code": "print(\"hello world\")"}'
# → {"task_id": "abc-123"}

# Check result
curl -s http://localhost:8080/tasks/<task_id>

# Stream output
curl -N http://localhost:8080/tasks/<task_id>/stream
```

### Real-time streaming

Submit a slow loop and watch lines arrive one by one:

```bash
curl -s -X POST http://localhost:8080/execute \
  -H 'Content-Type: application/json' \
  -d '{"code": "import time\nfor i in range(5):\n    print(i)\n    time.sleep(1)"}'

curl -N http://localhost:8080/tasks/<task_id>/stream
```

### Security rejections (HTTP 400)

These are caught before execution via AST analysis:

```bash
# Blocked import
curl -s -X POST http://localhost:8080/execute \
  -H 'Content-Type: application/json' \
  -d '{"code": "import os\nos.system(\"ls\")"}'

# Blocked builtin
curl -s -X POST http://localhost:8080/execute \
  -H 'Content-Type: application/json' \
  -d '{"code": "eval(\"1+1\")"}'
```

### Resource limit scenarios

```bash
# Infinite loop → status: timeout (killed after wall-clock limit)
curl -s -X POST http://localhost:8080/execute \
  -H 'Content-Type: application/json' \
  -d '{"code": "while True: pass"}'

# Output flood → output truncated at 512 KB, task still completes
curl -s -X POST http://localhost:8080/execute \
  -H 'Content-Type: application/json' \
  -d '{"code": "for i in range(100000):\n    print(\"x\" * 100)"}'
```

More test cases are in [test.txt](test.txt).

## Implemented functionality

### API endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/execute` | POST | Submit Python code. Returns `task_id`. |
| `/tasks/{id}` | GET | Get task status, output, exit code, and timestamps. |
| `/tasks/{id}/stream` | GET | Stream output as Server-Sent Events while the task runs. If the task is already done, replays the output immediately. |

### Task lifecycle

Tasks go through these statuses: `pending` → `running` → `completed / failed / timeout / killed`

- `completed` — exited with code 0
- `failed` — exited with a non-zero code (e.g. unhandled exception)
- `timeout` — exceeded the wall-clock time limit
- `killed` — terminated by a signal (e.g. memory limit)

Completed tasks are kept in memory for 30 minutes and then cleaned up automatically.

### Security

Two layers of protection:

**1. AST-based static analysis (before execution)**

The submitted code is parsed and inspected before anything runs. Any use of blocked modules or dangerous builtins is rejected with HTTP 400.

Blocked imports: `os`, `sys`, `subprocess`, `socket`, `shutil`, `pathlib`, `importlib`, `ctypes`, `signal`, `multiprocessing`, `threading`, `pty`, `tty`, `termios`, `fcntl`, `mmap`, `resource`

Blocked builtins: `eval`, `exec`, `compile`, `__import__`, `open`, `breakpoint`, `input`

Syntax errors are also caught here and rejected before spawning a process.

**2. OS-level resource limits (applied at subprocess spawn)**

Even if the AST check is bypassed somehow, the subprocess runs with hard limits enforced by the OS:

| Limit | Value |
|---|---|
| Wall-clock timeout | 100 seconds |
| CPU time | 5 seconds |
| Memory | 64 MB |
| Max file size written | 1 MB |
| Max open file descriptors | 32 |
| Max child processes | 50 |
| Max output captured | 512 KB |
| Max code size accepted | 50 KB |

The subprocess also runs in an isolated temp directory that is deleted after execution.

### What's not covered

This is a best-effort sandbox, not a production-grade solution. Notable gaps:

- No network isolation — code can make outbound network requests if it finds a way around the blocked imports
- No filesystem isolation beyond the temp working directory — the process inherits the parent's file system view
- The AST check can be bypassed with creative Python (e.g. attribute tricks, string-based import via builtins) — the OS limits are the real backstop
- No per-user rate limiting or request queuing
- Tasks are stored in memory only — restarting the server loses all task history
