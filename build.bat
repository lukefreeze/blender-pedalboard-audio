@echo off
REM =============================================================================
REM build.bat — The Hijacker audio engine build script
REM Requires: MSVC (cl.exe), Python 3.12, pybind11, PortAudio
REM =============================================================================

set TOOL_DIR=C:\Users\lukeb\Documents\BlenderTool
set SRC=%TOOL_DIR%\src
set INC=%TOOL_DIR%\include
set DEPS=%TOOL_DIR%\deps
set OUT=%TOOL_DIR%\blender_sync

REM PortAudio — download from https://www.portaudio.com/download.html
REM Build as static lib and place portaudio_static_x64.lib in deps\portaudio\lib
REM Place portaudio.h in deps\portaudio\include
set PA_INC=%DEPS%\portaudio\include
set PA_LIB=%DEPS%\portaudio\lib\portaudio_static_x64.lib

cl /O2 /LD /EHsc /std:c++17 ^
    /I "%INC%" ^
    /I "%INC%\blender-4.5\extern\audaspace\include" ^
    /I "%INC%\blender-4.5\extern\audaspace\include\fx" ^
    /I "%INC%\blender-4.5\extern\audaspace\include\respec" ^
    /I "%INC%\blender-4.5\extern\audaspace\include\util" ^
    /I "%INC%\blender-4.5\extern\audaspace\include\devices" ^
    /I "%INC%\blender-4.5\extern\audaspace\include\file" ^
    /I "%INC%\blender-4.5\extern\audaspace\include\sequence" ^
    /I "%SRC%" ^
    /I "%SRC%\imgui" ^
    /I "%DEPS%\python\include" ^
    /I "%DEPS%\pybind11\include" ^
    /I "%PA_INC%" ^
    "%SRC%\wrapper.cpp" ^
    "%SRC%\hijacker_processor.cpp" ^
    "%SRC%\hijacker_audio_engine.cpp" ^
    "%SRC%\mixer_ui.cpp" ^
    "%SRC%\imgui\imgui.cpp" ^
    "%SRC%\imgui\imgui_draw.cpp" ^
    "%SRC%\imgui\imgui_widgets.cpp" ^
    "%SRC%\imgui\imgui_tables.cpp" ^
    /link ^
    /OUT:"%OUT%\hijacker_engine.pyd" ^
    /LIBPATH:"%DEPS%\python\libs" ^
    "%PA_LIB%" ^
    winmm.lib ^
    ole32.lib ^
    uuid.lib ^
    advapi32.lib

echo.
echo Build complete: %OUT%\hijacker_engine.pyd
