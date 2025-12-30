from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, Response
from yt_dlp import YoutubeDL
from typing import Optional
import os
import uuid
import asyncio
from pydantic import BaseModel
import sys
import httpx
app = FastAPI()
# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
# REMOVED: ensure_ffmpeg function (caused Read-only file system error)
# REMOVED: startup_event (calling ensure_ffmpeg)
# Temporary storage for download progress
download_status = {}
class VideoRequest(BaseModel):
    url: str
class DownloadRequest(BaseModel):
    url: str
    format_id: str
@app.post("/api/video-info")
def get_video_info(request: VideoRequest):
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'format': 'best',
            'force_ipv4': True,
            # 'ffmpeg_location': os.getcwd(), # Relies on system ffmpeg
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
                'Sec-Fetch-Mode': 'navigate',
            },
            'cookiefile': 'cookies.txt',
        }
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(request.url, download=False)
            
            formats = []
            for f in info['formats']:
                if f.get('vcodec') != 'none' or f.get('acodec') != 'none':
                    formats.append({
                        'format_id': f['format_id'],
                        'quality': f.get('format_note', f['ext']),
                        'type': 'Video' if f.get('vcodec') != 'none' else 'Audio',
                        'size': f.get('filesize', 0)
                    })
            return JSONResponse({
                'title': info['title'],
                'thumbnail': info['thumbnail'],
                'duration': info['duration_string'],
                'formats': formats
            })
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
@app.post("/api/download")
async def download_video(request: DownloadRequest):
    download_id = str(uuid.uuid4())
    temp_filename = f"temp_{download_id}.mp4"
    
    def run_download_process(opts, url, d_id):
        try:
            with YoutubeDL(opts) as ydl:
                download_status[d_id] = {'progress': 0, 'status': 'downloading'}
                try:
                    ydl.download([url])
                except Exception as e:
                    print(f"Specific format download failed: {e}. Retrying with 'best'")
                    opts['format'] = 'best'
                    with YoutubeDL(opts) as ydl_retry:
                        ydl_retry.download([url])
                
                download_status[d_id]['status'] = 'completed'
        except Exception as e:
             download_status[d_id] = {'status': 'error', 'error': str(e)}
    # Define hook outside to be picklable/accessible if needed, but inner func is fine for threads
    def progress_hook(d):
        if d['status'] == 'downloading':
            if download_id in download_status:
                download_status[download_id]['progress'] = d['_percent_str']
    async def download_task():
        ydl_opts = {
            'format': request.format_id,
            'outtmpl': temp_filename,
            'progress_hooks': [progress_hook],
             # 'ffmpeg_location': os.getcwd(), # Relies on system ffmpeg
             'force_ipv4': True,
             'cookiefile': 'cookies.txt',
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
                'Sec-Fetch-Mode': 'navigate',
            },
        }
        
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: run_download_process(ydl_opts, request.url, download_id))
        
        # Post-download cleanup/timers (async)
        await asyncio.sleep(600) 
        if os.path.exists(temp_filename):
            try:
                os.remove(temp_filename)
            except:
                pass
        if download_id in download_status:
            del download_status[download_id]
    asyncio.create_task(download_task())
    
    return {'download_id': download_id}
@app.get("/api/progress/{download_id}")
async def get_download_progress(download_id: str):
    status = download_status.get(download_id)
    if not status:
        raise HTTPException(status_code=404, detail="Download ID not found")
    
    return status
def remove_file(path: str):
    try:
        os.remove(path)
    except Exception:
        pass
@app.get("/api/download-file/{download_id}")
async def get_download_file(download_id: str, background_tasks: BackgroundTasks):
    temp_filename = f"temp_{download_id}.mp4"
    if not os.path.exists(temp_filename):
        raise HTTPException(status_code=404, detail="File not found")
    
    # Schedule file deletion after response is sent
    background_tasks.add_task(remove_file, temp_filename)
    
    return FileResponse(
        temp_filename,
        headers={'Content-Disposition': f'attachment; filename="download_{download_id}.mp4"'}
    )
@app.get("/api/proxy-image")
async def proxy_image(url: str):
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
            response.raise_for_status()
            
            return Response(
                content=response.content,
                media_type=response.headers.get("content-type", "image/jpeg")
            )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch image: {str(e)}")
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
