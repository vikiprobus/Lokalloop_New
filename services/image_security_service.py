import io
import os
import re
import uuid
from PIL import Image, ImageOps
from django.core.files.storage import default_storage
from django.core.exceptions import ValidationError

# Strict limits
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB maximum
MIN_FILE_SIZE_BYTES = 100               # 100 bytes minimum to reject empty/dummy payloads

# Allowed genuine image extensions and content types
ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
ALLOWED_MIME_TYPES = {'image/jpeg', 'image/png', 'image/webp'}

# Magic byte signatures (file headers) for verified image formats
# JPEG starts with FF D8 FF
# PNG starts with 89 50 4E 47 0D 0A 1A 0A
# WEBP starts with RIFF (bytes 0-4) and WEBP (bytes 8-12)
MAGIC_SIGNATURES = {
    'jpeg': b'\xFF\xD8\xFF',
    'png': b'\x89\x50\x4E\x47\x0D\x0A\x1A\x0A',
    'riff': b'RIFF',
    'webp': b'WEBP'
}

# Dangerous executable / script patterns forbidden anywhere in the raw binary content
SUSPICIOUS_CONTENT_PATTERNS = [
    b'<?php',
    b'<script',
    b'<html',
    b'<body',
    b'<svg',           # SVG can contain embedded JavaScript (XSS vectors)
    b'<!doctype',
    b'eval(',
    b'base64_decode',
    b'system(',
    b'exec(',
    b'shell_exec(',
    b'passthru(',
    b'document.cookie',
    b'onload=',
    b'onerror=',
    b'MZ\x90\x00',     # Windows PE executable header (MZ DOS stub signature)
]

# Regex pattern for PHP short echo tags followed by identifiers or expressions
PHP_SHORT_TAG_REGEX = re.compile(rb'<\?=\s*[$"\'a-zA-Z_]')


class ImageSecurityException(ValidationError):
    """Raised when an uploaded file violates security or format constraints."""
    pass


def validate_image_security(file_obj):
    """
    Performs multi-layered deep security inspection on an uploaded file:
    1. Size validation (prevent DoS memory exhaustion).
    2. Extension whitelist check (reject .exe, .sh, .php, .js, .html, .py, etc.).
    3. Magic byte signature verification (ensures file is not a disguised binary).
    4. Anti-malware payload scan (detects embedded web shells, script tags, executables).
    5. Pillow deep image decode & integrity verification (checks for decompression bombs, corruption).
    
    Returns the opened and verified PIL Image instance.
    """
    # 1. Size Validation
    if not hasattr(file_obj, 'size'):
        file_obj.seek(0, os.SEEK_END)
        size = file_obj.tell()
        file_obj.seek(0)
    else:
        size = file_obj.size

    if size > MAX_FILE_SIZE_BYTES:
        raise ImageSecurityException(f"File size exceeds the 10 MB limit (current: {size / (1024*1024):.1f} MB).")
    if size < MIN_FILE_SIZE_BYTES:
        raise ImageSecurityException("File is too small or corrupted.")

    # 2. Extension check
    orig_name = getattr(file_obj, 'name', '')
    ext = os.path.splitext(orig_name)[1].lower() if orig_name else ''
    if ext not in ALLOWED_EXTENSIONS:
        raise ImageSecurityException(
            f"Invalid file extension '{ext}'. Only clean image formats (.jpg, .jpeg, .png, .webp) are permitted."
        )

    # 3. Magic Header Validation
    file_obj.seek(0)
    header = file_obj.read(32)
    file_obj.seek(0)

    is_valid_header = False
    if header.startswith(MAGIC_SIGNATURES['jpeg']):
        is_valid_header = True
    elif header.startswith(MAGIC_SIGNATURES['png']):
        is_valid_header = True
    elif header.startswith(MAGIC_SIGNATURES['riff']) and len(header) >= 12 and header[8:12] == MAGIC_SIGNATURES['webp']:
        is_valid_header = True

    if not is_valid_header:
        raise ImageSecurityException(
            "Security Violation: File header does not match valid image binary signatures (disguised or corrupt file)."
        )

    # 4. Anti-Malware / Dangerous Byte Scan (Read up to 2MB sample to prevent memory spikes)
    file_obj.seek(0)
    sample_data = file_obj.read(min(size, 2 * 1024 * 1024))
    file_obj.seek(0)

    sample_lower = sample_data.lower()
    for pattern in SUSPICIOUS_CONTENT_PATTERNS:
        if pattern in sample_data or pattern.lower() in sample_lower:
            raise ImageSecurityException(
                "Security Alert: Harmful script, shell payload or executable pattern detected in uploaded file."
            )

    if PHP_SHORT_TAG_REGEX.search(sample_data):
        raise ImageSecurityException(
            "Security Alert: Harmful script, shell payload or executable pattern detected in uploaded file."
        )

    # 5. Pillow Deep Image Decode & Pixel Sanitization
    try:
        # Set max pixels to guard against decompression bomb attacks
        Image.MAX_IMAGE_PIXELS = 50_000_000  # Max 50 megapixels

        file_obj.seek(0)
        img = Image.open(file_obj)

        # Call verify() to check file structure without loading pixel buffer
        img.verify()

        # Re-open because verify() makes the Image object unusable for further operations
        file_obj.seek(0)
        img = Image.open(file_obj)

        # Force full decoding of image pixels into memory to ensure zero hidden truncation
        img.load()

        # Validate that Pillow recognized it as a valid image format
        if img.format.lower() not in ['jpeg', 'png', 'webp']:
            raise ImageSecurityException(f"Unsupported internal image format: {img.format}.")

        return img

    except ImageSecurityException:
        raise
    except Exception as e:
        raise ImageSecurityException(f"Image integrity check failed: The file is corrupted or not a valid image ({str(e)}).")


def process_and_sanitize_image(file_obj, subfolder='shops', return_relative_path=False):
    """
    Validates, strips any embedded metadata/EXIF vulnerabilities, converts, re-encodes,
    and safely persists the image under a fresh cryptographically random UUID filename.
    
    Guarantees:
    - Never saves the original client-provided file bytes directly.
    - Completely regenerates pixels into clean JPEG / PNG / WEBP, stripping any injected code or EXIF exploits.
    - Uses UUID filenames to eliminate path traversal vulnerabilities (e.g. '../../malicious.jpg').
    - Auto-scales high-res photos to 1920px max dimension.
    """
    # Run full multi-layer security validation
    img = validate_image_security(file_obj)

    # Strip risky EXIF / metadata while preserving proper portrait/landscape orientation
    img = ImageOps.exif_transpose(img)

    # Downscale if excessively large to save server bandwidth and memory
    max_dim = 1920
    if max(img.width, img.height) > max_dim:
        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

    output = io.BytesIO()

    # Determine safe output format and strip metadata
    if img.format == 'PNG' and img.mode in ('RGBA', 'LA'):
        # Clean RGBA PNG
        clean_img = Image.new('RGBA', img.size)
        clean_img.paste(img, (0, 0))
        clean_img.save(output, format='PNG', optimize=True)
        save_ext = '.png'
    elif img.format == 'WEBP':
        clean_img = Image.new('RGB', img.size)
        clean_img.paste(img.convert('RGB'), (0, 0))
        clean_img.save(output, format='WEBP', quality=85)
        save_ext = '.webp'
    else:
        # Default: Convert to clean 85% quality JPEG (guaranteed RGB, no executable headers)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        clean_img = Image.new('RGB', img.size)
        clean_img.paste(img, (0, 0))
        clean_img.save(output, format='JPEG', quality=85, optimize=True)
        save_ext = '.jpg'

    output.seek(0)

    # Sanitize subfolder name to prevent directory traversal
    clean_subfolder = subfolder.replace('\\', '/').strip('/')
    clean_subfolder = '/'.join(part for part in clean_subfolder.split('/') if part and part not in ('.', '..'))
    if not clean_subfolder:
        clean_subfolder = 'uploads'

    # Generate brand new random UUID filename (ignores user-supplied filename completely)
    safe_filename = f"{clean_subfolder}/{uuid.uuid4().hex[:16]}{save_ext}"
    saved_path = default_storage.save(safe_filename, output)
    if return_relative_path:
        return saved_path
    return default_storage.url(saved_path)
