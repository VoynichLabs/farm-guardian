@echo off
:loop
"C:\Users\markb\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin\ffmpeg.exe" -f dshow -video_size 1280x720 -framerate 15 -i video="Hy-HD-Camera" -c:v libx264 -preset ultrafast -tune zerolatency -b:v 1000k -f rtsp rtsp://localhost:8554/gwtc
timeout /t 3
goto loop
