from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import os
from pathlib import Path
from threading import Event
from time import monotonic, sleep

from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FfmpegOutput
from libcamera import Transform # pyright: ignore[reportAttributeAccessIssue]


DURATION_SECONDS = 30
RESOLUTION = (1920, 1080)
LOW_RESOLUTION = (640, 360)
STREAM_HOST = os.environ.get("STREAM_HOST", "10.203.130.142")
STREAM_PORT = 5000
OUTPUT_DIR = Path(__file__).resolve().parent / "recordings"


def start_recording(camera, encoder, output, start_gate, stream_encoder=None, stream_output=None):
    start_gate.wait()
    if stream_encoder is not None:
        camera.start_encoder(stream_encoder, stream_output, name="lores")
    try:
        camera.start_recording(encoder, output)
    except Exception:
        if stream_encoder is not None:
            camera.stop_encoder(stream_encoder)
        raise
    return monotonic()


def main():
    camera_info = Picamera2.global_camera_info()
    if len(camera_info) < 2:
        raise RuntimeError(f"Two cameras are required; found {len(camera_info)}.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    cameras = []

    try:
        for camera_number in (0, 1):
            camera = Picamera2(camera_num=camera_number)
            streams = {"main": {"size": RESOLUTION}}
            if camera_number == 0:
                configuration = camera.create_video_configuration(
                    main={"size": RESOLUTION},
                    lores={"size": LOW_RESOLUTION, "format": "YUV420"}, transform=Transform(vflip=True, hflip=True)
                )   
            else:
                configuration = camera.create_video_configuration(main={"size": RESOLUTION}, transform=Transform(vflip=True, hflip=True))

            camera.configure(configuration)
            cameras.append(camera)

        encoders = [H264Encoder(bitrate=10_000_000) for _ in cameras]
        outputs = [
            FfmpegOutput(str(OUTPUT_DIR / f"camera_{number}_{timestamp}.mp4"))
            for number in (0, 1)
        ]
        stream_encoders = [H264Encoder(bitrate=1_500_000), None]
        stream_outputs = [
            FfmpegOutput(
                f"-f mpegts udp://{STREAM_HOST}:{STREAM_PORT}?pkt_size=1316"
            ),
            None,
        ]

        start_gate = Event()
        with ThreadPoolExecutor(max_workers=2) as executor:
            start_futures = [
                executor.submit(
                    start_recording,
                    camera,
                    encoder,
                    output,
                    start_gate,
                    stream_encoder,
                    stream_output,
                )
                for camera, encoder, output, stream_encoder, stream_output in zip(
                    cameras, encoders, outputs, stream_encoders, stream_outputs
                )
            ]
            start_gate.set()
            recording_started_at = max(future.result() for future in start_futures)

            deadline = recording_started_at + DURATION_SECONDS
            while (remaining := deadline - monotonic()) > 0:
                sleep(remaining)

            stop_futures = [executor.submit(camera.stop_recording) for camera in cameras]
            for future in stop_futures:
                future.result()

        print(f"Saved both videos in: {OUTPUT_DIR.resolve()}")
        print(f"Camera 0 low-resolution stream: udp://{STREAM_HOST}:{STREAM_PORT}")
    finally:
        for camera in cameras:
            camera.close()


if __name__ == "__main__":
    main()
