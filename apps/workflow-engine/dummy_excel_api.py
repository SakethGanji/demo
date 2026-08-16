"""Standalone dummy API for testing app builder file-download handling.

Run:
    cd apps/workflow-engine
    source venv/bin/activate
    python dummy_excel_api.py

Then POST to http://localhost:8765/generate-excel with a JSON body like:
    {"filename": "report", "rows": 25, "title": "Sales Q1"}

The response is an .xlsx file download (Content-Disposition: attachment).
"""

from __future__ import annotations

import io
import random
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from pydantic import BaseModel, Field

app = FastAPI(title="Dummy Excel API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ExcelRequest(BaseModel):
    filename: str = Field(default="report", description="Name of the file (no extension)")
    rows: int = Field(default=10, ge=1, le=10000, description="Number of data rows")
    title: str = Field(default="Dummy Report", description="Sheet title shown in row 1")


def _build_workbook(req: ExcelRequest) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"

    ws["A1"] = req.title
    ws.append(["ID", "Name", "Date", "Amount", "Status"])

    statuses = ["pending", "approved", "rejected", "shipped"]
    names = ["Alice", "Bob", "Carol", "Dan", "Eve", "Frank", "Grace", "Henry"]

    base_date = datetime(2026, 1, 1)
    for i in range(1, req.rows + 1):
        ws.append([
            i,
            random.choice(names),
            (base_date + timedelta(days=i)).date().isoformat(),
            round(random.uniform(10, 5000), 2),
            random.choice(statuses),
        ])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


@app.post("/generate-excel")
def generate_excel(req: ExcelRequest) -> StreamingResponse:
    """Generate a dummy Excel file from the given parameters."""
    data = _build_workbook(req)
    headers = {
        "Content-Disposition": f'attachment; filename="{req.filename}.xlsx"',
    }
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@app.get("/")
def root() -> dict:
    return {
        "name": "Dummy Excel API",
        "endpoint": "POST /generate-excel",
        "example_body": {"filename": "report", "rows": 25, "title": "Sales Q1"},
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765)
