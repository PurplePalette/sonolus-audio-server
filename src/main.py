import asyncio
import hashlib
import os
from tempfile import NamedTemporaryFile

import botocore.exceptions
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import FFMPEG_PATH, get_bucket
from .model import GetRootResponse, PostConvertParams, PostConvertResponse

app = FastAPI()


@app.get("/")
async def get_root() -> GetRootResponse:
    return JSONResponse(content={"status": "ok"})


@app.post("/convert")
async def upload(data: PostConvertParams) -> PostConvertResponse:
    bucket = get_bucket()
    base = NamedTemporaryFile(delete=os.name != "nt")
    dist = NamedTemporaryFile(delete=os.name != "nt")
    try:
        bucket.download_fileobj("LevelBgm/" + data.hash, base)
    except botocore.exceptions.ClientError as e:
        base.close()
        dist.close()
        if e.response["Error"]["Code"] == "404":
            return JSONResponse(content={"status": "not_found"})
    if data.start is not None and data.end is not None:
        if data.end - data.start < 5000:
            return JSONResponse(content={"message": "The audio file must be longer than 5 seconds."}, status_code=400)
        elif data.end - data.start > 30000:
            return JSONResponse(content={"message": "The audio file is too long."}, status_code=400)
        time_args = [
            "-ss",
            str(data.start / 1000),
            "-to",
            str(data.end / 1000),
        ]
        end_time = data.end / 1000
    elif data.start is not None:
        time_args = ["-ss", str(data.start / 1000), "-to", str(data.start / 1000 + 30)]
        end_time = data.start / 1000 + 30
    elif data.end is not None:
        start = max(data.end / 1000 - 30, 0)
        time_args = ["-ss", str(start), "-to", str(data.end / 1000)]
        end_time = data.end / 1000
    else:
        time_args = ["-t", str(30)]
        end_time = 30

    if os.name == "nt":
        dist.close()
    else:
        dist.write(b"")
    process = await asyncio.create_subprocess_exec(
        FFMPEG_PATH,
        "-i",
        base.name,
        "-vn",
        "-b:a",
        "128k",
        "-ar",
        "44100",
        "-f",
        "mp3",
        *time_args,
        "-af",
        f"afade=t=out:st={end_time - 5}:d=5",
        "-y",
        dist.name,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    await process.communicate()
    if process.returncode != 0:
        if os.name == "nt":
            os.remove(dist.name)
        return JSONResponse(
            content={
                "message": "Failed to convert.",
                "ffmpeg_returncode": process.returncode,
            }
        )
    if os.name == "nt":
        dist = open(dist.name, "rb")  # type: ignore
    dist.seek(0)
    cut_hash = hashlib.sha1(dist.read()).hexdigest()
    dist.seek(0)
    bucket.put_object(
        Key="LevelPreview/" + cut_hash,
        Body=dist,
        ContentType="audio/mpeg",
    )
    base.close()
    dist.close()
    if os.name == "nt":
        os.remove(base.name)
        os.remove(dist.name)
    return JSONResponse(content={"hash": cut_hash})
