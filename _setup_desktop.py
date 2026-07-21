"""Creates eta_sync.ico and a desktop shortcut. Run after install."""
import math, os, sys
from pathlib import Path

BASE_DIR = Path(__file__).parent


def make_icon():
    from PIL import Image, ImageDraw, ImageFont

    sizes  = [256, 128, 64, 48, 32, 16]
    frames = []
    for sz in sizes:
        img  = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        p = sz // 16
        draw.ellipse([p, p, sz-p-1, sz-p-1], fill="#1a3c5e")
        r2 = sz // 10
        draw.ellipse([p+r2, p+r2, sz-p-r2-1, sz-p-r2-1], fill="#1e4a7a")

        cx = cy = sz / 2
        r  = sz * 0.32
        aw = max(1, sz // 16)
        bb = [cx-r, cy-r, cx+r, cy+r]
        draw.arc(bb, 200, 340, fill="#4fc3f7", width=aw)
        draw.arc(bb,  20, 160, fill="#4fc3f7", width=aw)

        ah = sz // 10
        for ang in [340, 160]:
            a  = math.radians(ang)
            ax = cx + r * math.cos(a)
            ay = cy + r * math.sin(a)
            draw.polygon([
                (ax, ay),
                (ax - ah*math.cos(a+2.4), ay - ah*math.sin(a+2.4)),
                (ax - ah*math.cos(a-2.4), ay - ah*math.sin(a-2.4)),
            ], fill="#4fc3f7")

        fs = max(6, sz // 5)
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", fs)
        except Exception:
            font = ImageFont.load_default()

        t  = "ETA"
        bb = draw.textbbox((0, 0), t, font=font)
        tw, th = bb[2]-bb[0], bb[3]-bb[1]
        draw.text((cx - tw/2, cy - th/2), t, fill="white", font=font)
        frames.append(img)

    ico = BASE_DIR / "eta_sync.ico"
    frames[0].save(str(ico), format="ICO",
                   sizes=[(s, s) for s in sizes],
                   append_images=frames[1:])
    print(f"  Icon: {ico}")
    return ico


def make_vbs():
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    vbs = BASE_DIR / "ETA_Sync.vbs"
    vbs.write_text(
        f'Set oShell = CreateObject("WScript.Shell")\n'
        f'oShell.CurrentDirectory = "{BASE_DIR}"\n'
        f'oShell.Run Chr(34) & "{pythonw}" & Chr(34) & " launcher.py", 0, False\n',
        encoding="ascii",
    )
    print(f"  VBS:  {vbs}")
    return vbs, pythonw


def make_shortcut(vbs: Path, ico: Path):
    import subprocess
    desktop = subprocess.check_output(
        ['powershell', '-NoProfile', '-Command',
         '[System.Environment]::GetFolderPath("Desktop")'],
        text=True
    ).strip()
    lnk = Path(desktop) / "ETA Sync.lnk"
    ps = (
        f'$s = New-Object -ComObject WScript.Shell;'
        f'$lnk = $s.CreateShortcut("{lnk}");'
        f'$lnk.TargetPath = "C:\\Windows\\System32\\wscript.exe";'
        f'$lnk.Arguments = \'"{vbs}"\';'
        f'$lnk.WorkingDirectory = "{BASE_DIR}";'
        f'$lnk.IconLocation = "{ico},0";'
        f'$lnk.Description = "ETA Sync";'
        f'$lnk.Save()'
    )
    subprocess.run(['powershell', '-NoProfile', '-Command', ps], check=True)
    print(f"  Shortcut: {lnk}")


if __name__ == "__main__":
    print("\n[Setup] Creating icon...")
    ico = make_icon()
    print("[Setup] Creating VBS launcher...")
    vbs, _ = make_vbs()
    print("[Setup] Creating desktop shortcut...")
    make_shortcut(vbs, ico)
    print("\n[Setup] Done — shortcut is on the Desktop.\n")
