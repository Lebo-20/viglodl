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
    """Ambil daftar drama terbaru dari iDrama API.

    Alur yang benar:
    1. GET /home  -> dapat list "navigasi" berisi channel key
    2. GET /tab/{key} -> dapat daftar drama dari tiap channel tersebut
    """
    all_dramas = []
    seen_ids = set()

    async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:

        # === STEP 1: Discover channel keys dari /home ===
        tab_keys = []
        try:
            res = await client.get(f"{BASE_IDRAMA}/home", params={"lang": "id"}, timeout=15)
            if res.status_code == 200:
                nav_list = res.json().get("list", [])
                for nav in nav_list:
                    key = nav.get("key", "")
                    if key and key.startswith("channel_"):
                        tab_keys.append(key)
                    # Tambahkan sub_navs juga (Sedang Tren, Hits Terbaru, dll)
                    for sub in nav.get("sub_navs", []):
                        sub_key = sub.get("key", "")
                        if sub_key and sub_key.startswith("channel_") and sub_key not in tab_keys:
                            tab_keys.append(sub_key)
                logger.info(f"[iDrama] /home: ditemukan {len(tab_keys)} channel: {tab_keys}")
        except Exception as e:
            logger.error(f"[iDrama] Gagal fetch /home: {e}")

        # Fallback hardcoded jika /home tidak menghasilkan channel
        if not tab_keys:
            tab_keys = ["channel_7e89a1a2", "channel_f4904f0b", "channel_a57c8658"]
            logger.warning(f"[iDrama] /home kosong, pakai fallback keys: {tab_keys}")

        # === STEP 2: Fetch drama dari tiap /tab/{key} ===
        def extract_dramas_from_tab(raw_data):
            """Ekstrak short_plays/items dari respons /tab."""
            result = []
            items = raw_data if isinstance(raw_data, list) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                if isinstance(item.get("short_plays"), list):
                    result.extend(item["short_plays"])
                elif isinstance(item.get("items"), list):
                    result.extend(item["items"])
                else:
                    result.append(item)
            return result

        for key in tab_keys:
            for page in range(1, pages + 1):
                try:
                    url = f"{BASE_IDRAMA}/tab/{key}"
                    params = {"lang": "id", "page": page}
                    res = await client.get(url, params=params, timeout=15)
                    if res.status_code != 200:
                        logger.warning(f"[iDrama] /tab/{key} p{page} HTTP {res.status_code}")
                        continue

                    raw = res.json()
                    dramas_raw = extract_dramas_from_tab(raw if isinstance(raw, list) else [])

                    added = 0
                    for prog in dramas_raw:
                        if not isinstance(prog, dict):
                            continue
                        real = prog.get("program") or prog
                        drama_id = str(
                            real.get("id") or real.get("dramaId") or
                            real.get("short_series_id") or ""
                        )
                        if not drama_id or drama_id in seen_ids:
                            continue
                        seen_ids.add(drama_id)

                        title = (real.get("short_play_name") or real.get("title") or
                                 real.get("name") or "")
                        poster = (real.get("cover_url") or real.get("poster") or
                                  real.get("cover") or real.get("thumbnail") or "")

                        if title:
                            all_dramas.append({
                                "_source": "idrama",
                                "id": drama_id,
                                "title": title,
                                "bookName": title,
                                "poster": poster,
                            })
                            added += 1

                    logger.info(f"[iDrama] /tab/{key} p{page}: +{added} drama (total {len(all_dramas)})")

                except Exception as e:
                    logger.error(f"[iDrama] /tab/{key} p{page} error: {e}")

    logger.info(f"[iDrama] Selesai: {len(all_dramas)} drama unik ditemukan")
    return all_dramas
