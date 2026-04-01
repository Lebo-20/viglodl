import os
import subprocess
import logging
import re
from datetime import timedelta

logger = logging.getLogger(__name__)

def get_video_duration(video_path):
    """Get precise video duration using ffprobe."""
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_path]
        output = subprocess.check_output(cmd, text=True).strip()
        return float(output)
    except:
        return 0.0

def parse_time(t_str):
    """Parse WEBVTT time string 'HH:MM:SS.mmm' or 'MM:SS.mmm' to seconds."""
    parts = re.split(r'[:.]', t_str)
    if len(parts) == 4: # HH:MM:SS.mmm
        h, m, s, ms = map(int, parts)
        return h * 3600 + m * 60 + s + ms / 1000.0
    elif len(parts) == 3: # MM:SS.mmm
        m, s, ms = map(int, parts)
        return m * 60 + s + ms / 1000.0
    return 0.0

def format_time(seconds):
    """Format seconds to WEBVTT time string 'HH:MM:SS.mmm'."""
    td = timedelta(seconds=seconds)
    total_seconds = int(td.total_seconds())
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    ms = int(td.microseconds / 1000)
    return f"{h:02}:{m:02}:{s:02}.{ms:03}"

def merge_episodes(video_dir: str, output_path: str):
    """
    Merges all eps into one, and includes subtitles as softsubs if available.
    """
    try:
        files = sorted([f for f in os.listdir(video_dir) if f.endswith(".mp4")])
        if not files:
            return False
            
        list_file_path = os.path.join(video_dir, "list.txt")
        merged_vtt_path = os.path.join(video_dir, "merged.srt")
        
        # 1. Merge Subtitles
        has_subtitles = False
        vtt_files = sorted([f for f in os.listdir(video_dir) if f.endswith(".vtt")])
        
        if vtt_files:
            logger.info("Merging subtitles...")
            current_offset = 0.0
            with open(merged_vtt_path, "w", encoding='utf-8') as out_f:
                # Basic SRT format is easier for some players, but we'll use simple style
                # Actually ffmpeg can convert vtt to srt
                # Combined VTT logic
                out_f.write("WEBVTT\n\n")
                
                for i, v_file in enumerate(files):
                    ep_num = v_file.split("_")[1].split(".")[0]
                    v_path = os.path.join(video_dir, v_file)
                    v_duration = get_video_duration(v_path)
                    
                    s_file = f"episode_{ep_num}.vtt"
                    s_path = os.path.join(video_dir, s_file)
                    
                    if os.path.exists(s_path):
                        has_subtitles = True
                        with open(s_path, "r", encoding='utf-8') as in_f:
                            lines = in_f.readlines()
                            for line in lines:
                                if "-->" in line:
                                    # Adjust timing
                                    times = re.findall(r'(\d+:\d+:\d+\.\d+|\d+:\d+\.\d+)', line)
                                    if len(times) >= 2:
                                        start = parse_time(times[0]) + current_offset
                                        end = parse_time(times[1]) + current_offset
                                        out_f.write(f"{format_time(start)} --> {format_time(end)}\n")
                                    else:
                                        out_f.write(line)
                                elif line.strip() == "WEBVTT":
                                    continue # Skip header
                                else:
                                    out_f.write(line)
                        out_f.write("\n")
                    
                    current_offset += v_duration
            
        # 2. Merge Videos
        with open(list_file_path, "w") as f:
            for file in files:
                f.write(f"file '{file}'\n")

        # Concat videos first
        temp_merged_video = os.path.join(video_dir, "temp_merged.mp4")
        command = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", list_file_path, "-c", "copy",
            temp_merged_video
        ]
        
        logger.info(f"Merging videos: {' '.join(command)}")
        res = subprocess.run(command, capture_output=True)
        if res.returncode != 0:
            logger.error(f"FFMPEG Concat failed: {res.stderr.decode()[:500]}")
            return False

        if not os.path.exists(temp_merged_video):
            logger.error("FFMPEG Concat produced no output file.")
            return False
            
        # 3. Add softsubs if exists
        if has_subtitles and os.path.exists(merged_vtt_path):
            final_cmd = [
                "ffmpeg", "-y", "-i", temp_merged_video, "-i", merged_vtt_path,
                "-c", "copy", "-c:s", "mov_text", output_path
            ]
            logger.info(f"Adding softsubs: {' '.join(final_cmd)}")
            res_sub = subprocess.run(final_cmd, capture_output=True)
            if res_sub.returncode != 0:
                 logger.warning(f"Adding softsubs failed: {res_sub.stderr.decode()[:200]}. Using video without subs.")
                 os.rename(temp_merged_video, output_path)
        else:
            os.rename(temp_merged_video, output_path)
            
        return os.path.exists(output_path)
    except Exception as e:
        logger.error(f"Error during merge: {e}")
        return False
