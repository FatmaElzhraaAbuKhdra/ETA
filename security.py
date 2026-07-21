from pathlib import Path

DANGEROUS = {
    '.exe', '.bat', '.cmd', '.ps1', '.vbs', '.js', '.jar',
    '.msi', '.dll', '.scr', '.pif', '.com', '.hta', '.wsf',
    '.reg', '.inf', '.lnk', '.sh', '.bash',
}

SUSPICIOUS = {'.zip', '.rar', '.7z', '.iso', '.img', '.cab'}

# File magic bytes (first bytes identify real file type)
MAGIC = {
    b'MZ':         'Windows executable (EXE/DLL)',
    b'PK\x03\x04': 'ZIP archive',
    b'Rar!':       'RAR archive',
    b'\x7fELF':    'Linux executable (ELF)',
    b'\xca\xfe\xba\xbe': 'Java class/JAR',
}


def check_file(filename: str, content: bytes = None) -> tuple[bool, str]:
    """
    Returns (is_dangerous, reason).
    Checks extension first, then file magic bytes if content provided.
    """
    ext = Path(filename).suffix.lower()

    if ext in DANGEROUS:
        return True, f"Dangerous file type: {ext}"

    if ext in SUSPICIOUS:
        return True, f"Archive file — may contain malware: {ext}"

    if content:
        for magic, desc in MAGIC.items():
            if content[:len(magic)] == magic:
                # ZIP is OK for docx/xlsx (they're ZIP-based)
                if magic == b'PK\x03\x04' and ext in {'.docx', '.xlsx', '.pptx', '.odt'}:
                    break
                return True, f"Suspicious file content: {desc}"

    return False, ''
