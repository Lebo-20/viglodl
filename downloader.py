import os
import asyncio
import httpx
import logging
import re
import subprocess

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

async def download_all_episodes(episodes, download_dir: str, semaphore_count: int = 5):
    """
    Downloads all episodes concurrently with retry logic.
    Supports both iDrama (sesi 1) and Vigloo (sesi 2) sources.
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

            # Skip jika sudah ada dan valid
            if os.path.exists(video_path) and os.path.getsize(video_path) > 1024:
                logger.info(f"⏭ Skip {video_filename} (sudah ada)")
                return True

            for attempt in range(retries):
                try:
                    m3u8_url = None
                    subtitle_url = None
                    cookies = {}

                    # =========================================
                    # SESI 1: iDrama API (Prioritas Utama)
                    # =========================================
                    if ep.get('_source') == 'idrama':
                        from api import idrama_get_stream
                        drama_id = ep.get("dramaId") or ep.get("id")
                        ep_number = ep.get("ep") or ep.get("episode")
                        stream_data = await idrama_get_stream(drama_id, ep_number)
                        if not stream_data or not stream_data.get("m3u8"):
                            logger.warning(f"Attempt {attempt+1}: iDrama stream gagal ep{ep_num}")
                            await asyncio.sleep(5)
                            continue
                        m3u8_url = stream_data["m3u8"]
                        subtitle_url = stream_data.get("subtitle") or None

                    # =========================================
                    # SESI 2: Vigloo API (Fallback)
                    # =========================================
                    else:
                        from api import BASE_VIGLOO, AUTH_CODE
                        st_url = f"{BASE_VIGLOO}/getstream"
                        params = {
                            "lang": "id", "code": AUTH_CODE,
                            "seasonId": ep.get("seasonId"),
                            "ep": ep.get("ep"),
                            "videoId": ep.get("videoId")
                        }
                        async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
                            res = await client.get(st_url, params=params)
                            if res.status_code != 200:
                                logger.warning(f"Attempt {attempt+1}: Vigloo stream gagal ep{ep_num} (HTTP {res.status_code})")
                                await asyncio.sleep(5)
                                continue
                            data = res.json()
                            if not data.get("success") or "url" not in data:
                                logger.warning(f"Attempt {attempt+1}: Vigloo url kosong ep{ep_num}")
                                await asyncio.sleep(5)
                                continue

                            m3u8_url = data["url"]
                            cookies = data.get("cookies") or {}
                            base_url = m3u8_url.rsplit('/', 1)[0]

                            # Cari subtitle dari M3U8
                            try:
                                m_res = await client.get(m3u8_url, cookies=cookies)
                                if m_res.status_code == 200:
                                    m_text = m_res.text
                                    sub_match = re.search(r'TYPE=SUBTITLES.*?LANGUAGE="id".*?URI="(.*?)"', m_text)
                                    if not sub_match:
                                        sub_match = re.search(r'TYPE=SUBTITLES.*?URI="(.*?)"', m_text)
                                    if sub_match:
                                        uri = sub_match.group(1)
                                        if not uri.startswith('http'):
                                            uri = f"{base_url}/{uri}"
                                        if uri.endswith('.m3u8'):
                                            s_m_res = await client.get(uri, cookies=cookies)
                                            if s_m_res.status_code == 200:
                                                v_m = re.search(r'(\S+\.vtt)', s_m_res.text)
                                                if v_m:
                                                    subtitle_url = f"{uri.rsplit('/', 1)[0]}/{v_m.group(1)}"
                                        else:
                                            subtitle_url = uri
                            except:
                                pass

                    # =========================================
                    # Download Video via FFmpeg (Keduanya)
                    # =========================================
                    cookie_str = "; ".join([f"{k}={v}" for k, v in cookies.items()]) if cookies else ""
                    ffmpeg_cmd = [
                        "ffmpeg", "-y",
                        "-headers", f"Cookie: {cookie_str}\r\nUser-Agent: {HEADERS['User-Agent']}\r\n",
                        "-i", m3u8_url,
                        "-c", "copy", "-bsf:a", "aac_adtstoasc",
                        video_path
                    ]

                    proc = await asyncio.create_subprocess_exec(
                        *ffmpeg_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    _, stderr = await proc.communicate()

                    if os.path.exists(video_path) and os.path.getsize(video_path) > 1024:
                        # Download subtitle jika ada
                        if subtitle_url:
                            try:
                                async with httpx.AsyncClient(timeout=20, headers=HEADERS) as sclient:
                                    s_res = await sclient.get(subtitle_url, cookies=cookies)
                                    if s_res.status_code == 200:
                                        with open(subtitle_path, "wb") as sf:
                                            sf.write(s_res.content)
                                        logger.info(f"📝 Subtitle berhasil: {subtitle_filename}")
                            except:
                                pass

                        source_tag = ep.get('_source', 'unknown')
                        logger.info(f"✅ [{source_tag}] Berhasil: {video_filename} (Attempt {attempt+1})")
                        return True
                    else:
                        logger.warning(f"⚠️ Attempt {attempt+1} gagal: {video_filename}")
                        if os.path.exists(video_path):
                            os.remove(video_path)

                except Exception as e:
                    logger.error(f"❌ Attempt {attempt+1} Error ep{ep_num}: {e}")

                await asyncio.sleep(3 * (attempt + 1))

            logger.error(f"❌ Gagal setelah {retries} attempt: {video_filename}")
            return False

    results = await asyncio.gather(*(limited_download(ep) for ep in episodes))
    return all(results)
