import os
import asyncio
import httpx
import logging
import re
import subprocess

logger = logging.getLogger(__name__)

async def download_all_episodes(episodes, download_dir: str, semaphore_count: int = 5):
    """
    Downloads all episodes concurrently with retry logic.
    """
    os.makedirs(download_dir, exist_ok=True)
    semaphore = asyncio.Semaphore(semaphore_count)
    
    async def limited_download(ep, retries=3):
        async with semaphore:
            ep_num = str(ep.get('episode', 'unk')).zfill(3)
            video_filename = f"episode_{ep_num}.mp4"
            subtitle_filename = f"episode_{ep_num}.vtt"
            video_path = os.path.join(download_dir, video_filename)
            subtitle_path = os.path.join(download_dir, subtitle_filename)
            
            # Check if file already exists and is valid
            if os.path.exists(video_path) and os.path.getsize(video_path) > 1024:
                return True
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            
            for attempt in range(retries):
                if ep.get('_source') == 'vigloo':
                    from api import BASE_VIGLOO, AUTH_CODE
                    st_url = f"{BASE_VIGLOO}/getstream"
                    params = {
                        "lang": "id",
                        "code": AUTH_CODE,
                        "seasonId": ep.get("seasonId"),
                        "ep": ep.get("ep"),
                        "videoId": ep.get("videoId")
                    }
                    
                    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
                        try:
                            # 1. Fetch Fresh Stream Info
                            res = await client.get(st_url, params=params)
                            if res.status_code != 200:
                                logger.warning(f"Attempt {attempt+1}: GetStream failed for ep {ep_num} (Status {res.status_code})")
                                await asyncio.sleep(5)
                                continue
                                
                            data = res.json()
                            if not data.get("success") or "url" not in data:
                                logger.warning(f"Attempt {attempt+1}: GetStream success=False for ep {ep_num}")
                                await asyncio.sleep(5)
                                continue
                                
                            m3u8_url = data["url"]
                            cookies = data.get("cookies")
                            cookie_str = "; ".join([f"{k}={v}" for k, v in cookies.items()]) if cookies else ""
                            base_url = m3u8_url.rsplit('/', 1)[0]
                            
                            # 2. Extract Subtitle
                            subtitle_url = None
                            try:
                                m_res = await client.get(m3u8_url, cookies=cookies)
                                if m_res.status_code == 200:
                                    m_text = m_res.text
                                    sub_match = re.search(r'TYPE=SUBTITLES.*?LANGUAGE="id".*?URI="(.*?)"', m_text)
                                    if not sub_match:
                                        sub_match = re.search(r'TYPE=SUBTITLES.*?URI="(.*?)"', m_text)
                                    if sub_match:
                                        uri = sub_match.group(1)
                                        if not uri.startswith('http'): uri = f"{base_url}/{uri}"
                                        if uri.endswith('.m3u8'):
                                            s_m_res = await client.get(uri, cookies=cookies)
                                            if s_m_res.status_code == 200:
                                                v_m = re.search(r'(\S+\.vtt)', s_m_res.text)
                                                if v_m: subtitle_url = f"{uri.rsplit('/', 1)[0]}/{v_m.group(1)}"
                                        else:
                                            subtitle_url = uri
                            except: pass

                            # 3. Download Video with ffmpeg
                            ffmpeg_cmd = [
                                "ffmpeg", "-y", "-headers", f"Cookie: {cookie_str}\r\nUser-Agent: {headers['User-Agent']}\r\n",
                                "-i", m3u8_url, "-c", "copy", "-bsf:a", "aac_adtstoasc", video_path
                            ]
                            
                            proc = await asyncio.create_subprocess_exec(
                                *ffmpeg_cmd,
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE
                            )
                            _, stderr = await proc.communicate()
                            
                            if os.path.exists(video_path) and os.path.getsize(video_path) > 1024:
                                # 4. Subtitle download
                                if subtitle_url:
                                    try:
                                        s_res = await client.get(subtitle_url, cookies=cookies)
                                        if s_res.status_code == 200:
                                            with open(subtitle_path, "wb") as sf:
                                                sf.write(s_res.content)
                                    except: pass
                                logger.info(f"✅ Success: {video_filename} (Attempt {attempt+1})")
                                return True
                            else:
                                logger.warning(f"⚠️ Attempt {attempt+1} failed for ep {ep_num}")
                                if os.path.exists(video_path): os.remove(video_path)
                        except Exception as e:
                            logger.error(f"❌ Attempt {attempt+1} Error for ep {ep_num}: {e}")
                
                await asyncio.sleep(3 * (attempt + 1))
            return False

    results = await asyncio.gather(*(limited_download(ep) for ep in episodes))
    return all(results)
