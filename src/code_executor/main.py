import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from .code_check import validate_code
from .executor import CodeExecutor
from .models import ExecuteRequest, Status

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the CodeExecutor on startup and register the background cleanup task."""
    logger.info("Starting up: initializing CodeExecutor")
    app.state.executor = CodeExecutor()
    asyncio.create_task(app.state.executor.cleanup())
    yield

app = FastAPI(lifespan=lifespan)


@app.post("/execute", status_code=202)
async def execute(req: ExecuteRequest, request: Request):
    ok, reason = validate_code(req.code)
    if not ok:
        logger.warning("Code validation failed: %s", reason)
        raise HTTPException(400, reason)

    executor: CodeExecutor = request.app.state.executor
    task_id = str(uuid.uuid4())
    executor.tasks[task_id] = executor.new_task(task_id, req.code)
    asyncio.create_task(executor.run_code(task_id))
    logger.info("Task created: %s", task_id)
    return {"task_id": task_id}


@app.get("/tasks/{task_id}")
async def get_task(task_id: str, request: Request):
    executor: CodeExecutor = request.app.state.executor
    task = executor.tasks.get(task_id)
    if not task:
        logger.warning("Task not found: %s", task_id)
        raise HTTPException(404, "task not found")
    return executor.serialize_task(task)


@app.get("/tasks/{task_id}/stream")
async def stream(task_id: str, request: Request):
    executor: CodeExecutor = request.app.state.executor
    task = executor.tasks.get(task_id)
    if not task:
        logger.warning("Stream requested for unknown task: %s", task_id)
        raise HTTPException(404, "task not found")

    async def events():
        if task["status"] not in (Status.pending, Status.running):
            if task["output"]:
                yield f"data: {task['output']}\n\n"
            done = json.dumps({"status": task["status"], "exit_code": task["exit_code"]})
            yield f"event: done\ndata: {done}\n\n"
            return

        q = task["queue"]
        while True:
            try:
                line = await asyncio.wait_for(q.get(), timeout=30)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue

            if line is None:
                done = json.dumps({"status": task["status"], "exit_code": task["exit_code"]})
                yield f"event: done\ndata: {done}\n\n"
                break

            yield f"data: {line.rstrip()}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
