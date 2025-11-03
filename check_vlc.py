import os
import ctypes

def check_vlc_dlls():
    dlls = ["libvlc.dll", "libvlccore.dll"]
    missing = []
    for dll in dlls:
        if not os.path.isfile(dll):
            missing.append(dll)

    if missing:
        print(f"Missing DLLs: {', '.join(missing)}")
        return

    try:
        ctypes.CDLL("libvlc.dll")
        ctypes.CDLL("libvlccore.dll")
        print("✅ VLC DLLs loaded successfully.")
    except Exception as e:
        print(f"❌ Failed to load VLC DLLs: {e}")

if __name__ == "__main__":
    check_vlc_dlls()