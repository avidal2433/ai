#!/usr/bin/env python3
"""
e2e_video.py — Une las grabaciones y capturas de las pruebas end-to-end en un solo MP4.

Uso:
  python3 e2e_video.py <salida.mp4> <clip> [<clip> ...]

  <clip> es un video (webm, mp4, mov) o una imagen (png, jpg) con sus segundos en pantalla:
  `tarjeta.png:4`. Sin segundos, una imagen dura 4. Se unen en el orden recibido.

Normaliza cada clip a 1280x720 (escala sin deformar y rellena con negro), 25 fps y sin audio,
y codifica H.264 yuv420p con faststart, que se reproduce en el navegador y en la vista previa
de ClickUp. Requiere ffmpeg y ffprobe.

Salida: una línea JSON {"ok", "path", "seconds", "bytes"}.
Exit: 0 ok · 1 error de ffmpeg o salida inválida · 2 uso.
"""
import json
import os
import shutil
import subprocess
import sys

WIDTH, HEIGHT, FPS = 1280, 720, 25
IMAGE_SECONDS = 4
IMAGES = (".png", ".jpg", ".jpeg")
VIDEOS = (".webm", ".mp4", ".mov", ".mkv")


def parse_clip(spec):
    """«ruta» o «imagen:segundos» → (ruta, segundos o None para videos)."""
    path, sep, seconds = spec.rpartition(":")
    if not sep or not path.lower().endswith(IMAGES):
        path, seconds = spec, ""
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGES:
        return path, float(seconds) if seconds else IMAGE_SECONDS
    if ext in VIDEOS:
        return path, None
    raise ValueError(f"clip no soportado: {spec}")


def build_command(out, clips):
    cmd, chains = ["ffmpeg", "-y", "-loglevel", "error"], []
    for i, (path, seconds) in enumerate(clips):
        cmd += ["-loop", "1", "-t", f"{seconds:g}", "-i", path] if seconds else ["-i", path]
        chains.append(f"[{i}:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
                      f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},format=yuv420p[v{i}]")
    joined = "".join(f"[v{i}]" for i in range(len(clips)))
    graph = ";".join(chains) + f";{joined}concat=n={len(clips)}:v=1:a=0[out]"
    return cmd + ["-filter_complex", graph, "-map", "[out]", "-an", "-c:v", "libx264", "-preset", "veryfast",
                  "-crf", "28", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]


def probe(path):
    done = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=codec_name:format=duration", "-of", "json", path],
                          capture_output=True, text=True)
    info = json.loads(done.stdout or "{}")
    codec = ((info.get("streams") or [{}])[0]).get("codec_name")
    return codec, float((info.get("format") or {}).get("duration") or 0)


def main(argv):
    if len(argv) < 3 or not argv[1].lower().endswith(".mp4"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        clips = [parse_clip(spec) for spec in argv[2:]]
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    missing = [path for path, _ in clips if not os.path.isfile(path)]
    if missing:
        print(f"ERROR: no existen: {', '.join(missing)}", file=sys.stderr)
        return 2
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("ERROR: faltan ffmpeg o ffprobe", file=sys.stderr)
        return 1

    out = argv[1]
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    done = subprocess.run(build_command(out, clips), capture_output=True, text=True)
    codec, seconds = probe(out) if done.returncode == 0 else (None, 0)
    if codec != "h264" or seconds <= 0:
        print(f"ERROR: ffmpeg no generó un MP4 válido: {(done.stderr or codec or '')[-500:]}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "path": out, "seconds": round(seconds, 1), "bytes": os.path.getsize(out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
