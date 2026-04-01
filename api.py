import httpx
import logging

logger = logging.getLogger(__name__)

# ============================================
# iDrama API (Sumber Tunggal)
# ============================================
BASE_IDRAMA = "https://idrama.dramabos.my.id"
AUTH_CODE = "A8D6AB170F7B89F2182561D3B32F390D"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
}


async def get_drama_detail(drama_id: str):
    """Ambil detail drama dari iDrama API."""
    url = f"{BASE_IDRAMA}/drama/{drama_id}"
    params = {"lang": "id", "code": AUTH_CODE}
    async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
        try:
            res = await client.get(url, params=params)
            if res.status_code != 200:
                logger.error(f"[iDrama] Detail {drama_id} HTTP {res.status_code}")
                return None

            data = res.json()
            payload = data.get("data") or data

            if not payload or not isinstance(payload, dict):
                return None

            # Normalisasi field
            title = payload.get("title") or payload.get("name") or ""
            intro = (payload.get("description") or payload.get("intro") or
                     payload.get("synopsis") or "")
            poster = (payload.get("poster") or payload.get("cover") or
                      payload.get("thumbnail") or
                      payload.get("thumbnailExpanded") or "")
            ep_count = payload.get("episodeCount") or payload.get("totalEp") or 0

            return {
                "_source": "idrama",
                "id": str(drama_id),
                "title": title,
                "intro": intro,
                "poster": poster,
                "episodeCount": ep_count,
                "_raw": payload
            }
        except Exception as e:
            logger.error(f"[iDrama] Detail error {drama_id}: {e}")
    return None


async def get_all_episodes(drama_id: str, detail=None):
    """Ambil semua episode dari iDrama API."""
    if not detail:
        detail = await get_drama_detail(drama_id)
    if not detail:
        return []

    raw = detail.get("_raw", {})

    # Coba ambil dari field episodes di payload detail
    episodes_raw = raw.get("episodes") or raw.get("episodeList") or []
    if episodes_raw:
        eps = []
        for ep in episodes_raw:
            ep_num = (ep.get("ep") or ep.get("episode") or
                      ep.get("episodeNumber") or ep.get("index"))
            video_id = ep.get("id") or ep.get("videoId")
            if ep_num is not None and video_id:
                eps.append({
                    "_source": "idrama",
                    "dramaId": str(drama_id),
                    "ep": int(ep_num),
                    "episode": int(ep_num),
                    "videoId": str(video_id)
                })
        if eps:
            return sorted(eps, key=lambda x: x["episode"])

    # Fallback: build episode list from episodeCount
    ep_count = detail.get("episodeCount") or 0
    if ep_count > 0:
        logger.info(f"[iDrama] Membangun daftar {ep_count} episode untuk {drama_id}")
        return [
            {
                "_source": "idrama",
                "dramaId": str(drama_id),
                "ep": i,
                "episode": i,
                "videoId": None  # akan di-unlock lewat /unlock/:id/:ep
            }
            for i in range(1, int(ep_count) + 1)
        ]

    return []


async def get_stream_url(drama_id: str, ep: int):
    """Dapatkan URL streaming episode dari iDrama API."""
    url = f"{BASE_IDRAMA}/unlock/{drama_id}/{ep}"
    params = {"lang": "id", "code": AUTH_CODE}
    async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
        try:
            res = await client.get(url, params=params)
            if res.status_code != 200:
                logger.error(f"[iDrama] Unlock ep{ep} HTTP {res.status_code}")
                return None
            data = res.json()
            payload = data.get("data") or data

            m3u8 = (payload.get("url") or payload.get("m3u8") or
                    payload.get("stream") or payload.get("hls") or "")
            subtitle = (payload.get("subtitle") or payload.get("vtt") or
                        payload.get("subtitleUrl") or "")
            cookies = payload.get("cookies") or {}

            if not m3u8:
                logger.warning(f"[iDrama] URL kosong untuk ep{ep}")
                return None

            return {"m3u8": m3u8, "subtitle": subtitle, "cookies": cookies}
        except Exception as e:
            logger.error(f"[iDrama] Stream error {drama_id} ep{ep}: {e}")
    return None


async def get_latest_dramas(pages=1, **kwargs):
    """Ambil daftar drama terbaru dari iDrama API."""
    all_dramas = []
    seen_ids = set()

    # Daftar endpoint yang akan discan untuk mencari update drama terbaru
    endpoints = [
        "/tab/channel_ddbdbcef", # Seringkali berisi "Daftar Peringkat / Popular"
        "/tab/channel_7e89a1a2", # Biasanya "Terbaru / Hits"
        "/home"
    ]

    async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
        for endpoint in endpoints:
            for page in range(1, pages + 1):
                try:
                    url = f"{BASE_IDRAMA}{endpoint}"
                    params = {"lang": "id", "page": page}
                    res = await client.get(url, params=params, timeout=15)
                    if res.status_code != 200:
                        logger.warning(f"[iDrama] {endpoint} page {page} HTTP {res.status_code}")
                        continue

                    data = res.json()
                    
                    # Normalisasi data array vs dict
                    items = data
                    if isinstance(data, dict):
                        items = (data.get("data") or data.get("payloads") or
                                 data.get("list") or data.get("dramas") or [])
                        if isinstance(items, dict):
                            items = (items.get("payloads") or items.get("list") or
                                     items.get("dramas") or [])

                    if not isinstance(items, list):
                        continue

                    dramas_to_process = []
                    for item in items:
                        if isinstance(item, dict):
                            if "short_plays" in item and isinstance(item["short_plays"], list):
                                dramas_to_process.extend(item["short_plays"])
                            elif "items" in item and isinstance(item["items"], list):
                                dramas_to_process.extend(item["items"])
                            else:
                                dramas_to_process.append(item)
                        else:
                            dramas_to_process.append(item)

                    for prog in dramas_to_process:
                        if not isinstance(prog, dict): continue
                        real_prog = prog.get("program") or prog
                        
                        drama_id = str(real_prog.get("id") or real_prog.get("dramaId") or real_prog.get("short_series_id") or "")
                        if not drama_id or drama_id in seen_ids:
                            continue
                        seen_ids.add(drama_id)
                        
                        title = real_prog.get("short_play_name") or real_prog.get("title") or real_prog.get("name") or ""
                        poster = (real_prog.get("cover_url") or real_prog.get("poster") or 
                                  real_prog.get("cover") or real_prog.get("thumbnail") or 
                                  real_prog.get("image") or "")

                        if title:
                            all_dramas.append({
                                "_source": "idrama",
                                "id": drama_id,
                                "title": title,
                                "bookName": title,
                                "poster": poster,
                            })

                except Exception as e:
                    logger.error(f"[iDrama] {endpoint} page {page} error: {e}")

    logger.info(f"[iDrama] Scan Endpoint Selesai: {len(all_dramas)} drama unik ditemukan")
    return all_dramas
