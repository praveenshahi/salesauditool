import os
import uuid
import aiosqlite
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
from prompt import SYSTEM_PROMPT

load_dotenv()

DB_PATH = "reports.db"
client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.getenv("NVIDIA_API_KEY"),
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY,
                report_markdown TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        await db.commit()
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


class AuditRequest(BaseModel):
    transcript: str
    segment: str = ""
    product: str = ""
    ticket_size: str = ""


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.post("/audit")
async def audit(req: AuditRequest):
    if not req.transcript.strip():
        raise HTTPException(status_code=400, detail="Transcript cannot be empty.")

    context_parts = []
    if req.segment:
        context_parts.append(f"Segment: {req.segment}")
    if req.product:
        context_parts.append(f"Product: {req.product}")
    if req.ticket_size:
        context_parts.append(f"Ticket size: {req.ticket_size}")

    context_line = ", ".join(context_parts) if context_parts else "Not provided"
    user_message = f"Context: {context_line}\n\nTranscript:\n{req.transcript.strip()}"

    response = client.chat.completions.create(
        model="nvidia/llama-3.1-nemotron-70b-instruct",
        max_tokens=4096,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )

    report_markdown = response.choices[0].message.content
    report_id = str(uuid.uuid4())

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO reports (id, report_markdown) VALUES (?, ?)",
            (report_id, report_markdown),
        )
        await db.commit()

    return {"id": report_id, "report": report_markdown}


@app.get("/report/{report_id}")
async def get_report(report_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT report_markdown FROM reports WHERE id = ?", (report_id,)
        ) as cursor:
            row = await cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Report not found.")

    return {"id": report_id, "report": row[0]}
